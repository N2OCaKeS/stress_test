"""DAO для `Session` — CRUD refresh-сессий + CAS rotate (reuse-detection)."""

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.session import PREVIOUS_TOKEN_HASH_WINDOW, Session
from src.repositories._cas import atomic_transition
from src.utils.ids import session_id
from src.utils.time import utcnow

# Namespace-константа для transaction-level advisory-lock'а вокруг login'а.
# `pg_advisory_xact_lock(ns, hashtext(user_id))` сериализует конкурентные
# login'ы одного юзера, чтобы enforce_concurrent_limit видел уже закоммиченные
# чужие сессии (см. docstring метода). Фиксированный namespace отделяет эти
# локи от любых других advisory-локов в БД.
_LOGIN_LOCK_NAMESPACE = 0x4C4F474E  # "LOGN"


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, sid: str) -> Session | None:
        return await self._db.get(Session, sid)

    async def get_active_by_token_hash(self, token_hash: str) -> Session | None:
        return await self._db.scalar(
            select(Session).where(
                Session.refresh_token_hash == token_hash,
                Session.is_active.is_(True),
            )
        )

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Lookup по current ИЛИ любому из previous token-hashes — для reuse-detection.

        Sliding window до `PREVIOUS_TOKEN_HASH_WINDOW` поколений: атакер не
        ловится только если RT был ротирован больше N раз с момента кражи.
        """
        # ARRAY `@>` (contains) — точное совпадение элемента массива.
        return await self._db.scalar(
            select(Session).where(
                or_(
                    Session.refresh_token_hash == token_hash,
                    Session.previous_token_hash == token_hash,
                    Session.previous_token_hashes.contains([token_hash]),
                )
            )
        )

    async def find_rotated_by_old_hash(self, token_hash: str) -> Session | None:
        """Найти сессию, у которой `token_hash` лежит в истории previous_*.

        В отличие от `get_by_token_hash` НЕ матчит по `refresh_token_hash` —
        это нужно, чтобы refresh-flow различал две ситуации:

        * Сессия revoked (logout / sessions-revoke-one / sessions-revoke-all),
          ротации не было: текущий `refresh_token_hash` всё ещё равен hash'у
          предъявленного токена. Это НЕ reuse — пользователь сам её закрыл,
          бить по другим сессиям не надо.
        * Старый ротированный токен подсунут заново: hash лежит в
          `previous_token_hash` / `previous_token_hashes` (sliding window).
          Это реальный reuse — кидаем kill-switch (`revoke_all_for_user`).
        """
        return await self._db.scalar(
            select(Session).where(
                or_(
                    Session.previous_token_hash == token_hash,
                    Session.previous_token_hashes.contains([token_hash]),
                )
            )
        )

    @staticmethod
    def is_grace_window_rotation(sess: Session, token_hash: str, grace_seconds: int) -> bool:
        """Проигравший benign-гонку ретраит непосредственно-предыдущим hash'ем?

        True только если ВСЕ условия выполнены:

        * `grace_seconds > 0` — окно включено;
        * сессия ещё активна (benign-гонка не могла её погасить — победитель
          лишь ротировал токен, сессия жива);
        * `token_hash` — именно НЕПОСРЕДСТВЕННО-предыдущий (только что
          ротированный) hash, т.е. хвост `previous_token_hashes`. Более старый
          hash из середины окна сюда не проходит — он трактуется как настоящий
          reuse;
        * с момента ротации (`last_used_at`) прошло не больше `grace_seconds`.

        Вне окна / не последний hash → False, и caller бьёт kill-switch как
        раньше.
        """
        if grace_seconds <= 0 or not sess.is_active or sess.last_used_at is None:
            return False
        window = list(sess.previous_token_hashes or [])
        immediately_prev = window[-1] if window else sess.previous_token_hash
        if immediately_prev is None or token_hash != immediately_prev:
            return False
        last_used = sess.last_used_at
        if last_used.tzinfo is None:
            from datetime import timezone
            last_used = last_used.replace(tzinfo=timezone.utc)
        return (utcnow() - last_used).total_seconds() <= grace_seconds

    async def create(
        self,
        user_id: str,
        refresh_token_hash: str,
        expires_at,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Session:
        sess = Session(
            id=session_id(),
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._db.add(sess)
        await self._db.flush()
        return sess

    async def rotate(
        self,
        sess: Session,
        new_hash: str,
        new_expires_at,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Атомарная замена `refresh_token_hash` через CAS.

        UPDATE матчит по `id` И ожидаемому текущему `refresh_token_hash`.
        Если параллельный `/refresh` уже ротировал строку — WHERE не находит
        её, `RETURNING` пуст, возвращаем `False`. Caller трактует это как
        race (НЕ token-reuse attack: НЕ зовём `mark_suspicious` и
        `revoke_all_for_user`).

        Через общий `_cas.atomic_transition` (тот же CAS-шаблон, что и
        в `BanRepository.deactivate` / `OAuthCodeRepository.mark_used`).

        Возвращает `True` если caller выиграл ротацию, `False` если строка
        с `refresh_token_hash` больше не совпадает с `sess.refresh_token_hash`
        (кто-то ротировал первым). На success in-memory ORM-инстанс
        синхронизируется, чтобы audit details (`sess.id`,
        `sess.token_generation`) были корректны.
        """
        expected_hash = sess.refresh_token_hash
        now = utcnow()
        # Sliding window поколений. Берём список как он лежит в БД (через
        # ORM-инстанс — DB и память в этой точке согласованы CAS-инвариантом),
        # аппендим expected_hash в хвост, режем голову до N. Записываем как
        # literal-массив — обновление атомарно с прочими values в CAS UPDATE.
        prev_window = list(sess.previous_token_hashes or [])
        prev_window.append(expected_hash)
        if len(prev_window) > PREVIOUS_TOKEN_HASH_WINDOW:
            prev_window = prev_window[-PREVIOUS_TOKEN_HASH_WINDOW:]

        update_values: dict = {
            "previous_token_hash": expected_hash,
            "previous_token_hashes": prev_window,
            "refresh_token_hash": new_hash,
            "token_generation": Session.token_generation + 1,
            "expires_at": new_expires_at,
            "last_used_at": now,
        }
        # ip_address/user_agent обновляем только если передали — иначе
        # сессия теряла бы исходное значение, если клиент refresh'ит без
        # известного IP (например, internal call). None — "не трогать".
        if ip_address is not None:
            update_values["ip_address"] = ip_address
        if user_agent is not None:
            update_values["user_agent"] = user_agent

        # `token_generation = Session.token_generation + 1` — это column-expr,
        # `.values(**dict)` нормально его принимает.
        won = await atomic_transition(
            self._db,
            Session,
            id_column="id",
            id_value=sess.id,
            where_clause=and_(
                Session.refresh_token_hash == expected_hash,
                Session.is_active.is_(True),
            ),
            update_values=update_values,
        )
        if not won:
            return False
        # Синкаем ORM-инстанс с тем, что записали в БД — caller'ы (например
        # `auth_service.refresh`) после возврата читают `sess.id` для audit.
        sess.previous_token_hash = expected_hash
        sess.previous_token_hashes = prev_window
        sess.refresh_token_hash = new_hash
        sess.token_generation += 1
        sess.expires_at = new_expires_at
        sess.last_used_at = now
        if ip_address is not None:
            sess.ip_address = ip_address
        if user_agent is not None:
            sess.user_agent = user_agent
        return True

    async def revoke(self, sess: Session) -> None:
        sess.is_active = False
        sess.revoked_at = utcnow()
        await self._db.flush()

    async def revoke_all_for_user(
        self,
        user_id: str,
        except_session_id: str | None = None,
    ) -> int:
        """Revoke все активные сессии юзера. Опционально пропустить одну.

        `except_session_id` нужен ручке `revoke_sessions(except_current=True)`
        — оставить ту сессию, через которую пришёл вызов, чтобы юзер не
        выкинулся из текущего UI/CLI. None — снести всё (поведение admin
        reset-password / ban / self-password-reset).

        Возвращает число фактически revoked'нутых сессий.
        """
        now = utcnow()
        where = [Session.user_id == user_id, Session.is_active.is_(True)]
        if except_session_id is not None:
            where.append(Session.id != except_session_id)
        result = await self._db.scalars(select(Session).where(*where))
        count = 0
        for sess in result:
            sess.is_active = False
            sess.revoked_at = now
            count += 1
        await self._db.flush()
        return count

    async def enforce_concurrent_limit(
        self,
        user_id: str,
        keep_session_id: str,
        limit: int,
    ) -> list[Session]:
        """Подрезать число активных сессий юзера до `limit` (sliding window).

        Сериализация конкурентных login'ов одного юзера — через
        transaction-level advisory-lock по `user_id`. Второй login блокируется
        на локе, пока первый не закоммитит; после разблокировки его SELECT
        (новый snapshot под READ COMMITTED) уже видит закоммиченную чужую
        сессию, поэтому лимит не пробивается «невидимой» соседней вставкой.
        Одного `FOR UPDATE` для этого мало: свежесозданная сессия соседа не
        закоммичена и в его snapshot невидима.

        Дальше берёт активные сессии под `FOR UPDATE`, сортирует от старых к
        новым по `created_at` и отзывает самые старые лишние, чтобы осталось
        ровно `limit`. Только что созданная сессия (`keep_session_id`) из
        кандидатов на вытеснение исключается — она остаётся всегда.

        `limit <= 0` трактуется как «лимит выключен» — ничего не делаем.
        Возвращает список вытесненных сессий (для audit).
        """
        if limit <= 0:
            return []
        # Сериализуем login'ы одного юзера. Лок держится до конца транзакции
        # (commit/rollback в login), поэтому SELECT ниже идёт уже после того,
        # как конкурент отпустил лок и закоммитил свою сессию.
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
            {"ns": _LOGIN_LOCK_NAMESPACE, "uid": user_id},
        )
        now = utcnow()
        result = await self._db.scalars(
            select(Session)
            .where(Session.user_id == user_id, Session.is_active.is_(True))
            .order_by(Session.created_at.asc(), Session.id.asc())
            .with_for_update()
        )
        active = list(result)
        # keep_session всегда остаётся; среди остальных держим самые свежие.
        revocable = [s for s in active if s.id != keep_session_id]
        excess = len(revocable) - (limit - 1)
        if excess <= 0:
            return []
        evicted = revocable[:excess]
        for sess in evicted:
            sess.is_active = False
            sess.revoked_at = now
        await self._db.flush()
        return evicted

    async def list_active_for_user(self, user_id: str) -> list[Session]:
        """Активные refresh-сессии юзера в порядке last_used_at desc.

        Для UI «Active devices». Истёкшие по `expires_at` исключаются — они
        логически невалидны, хотя `is_active=True` может ещё стоять (revoke
        не делается превентивно). Сортировка по last_used_at desc; для
        свежесозданных (last_used_at IS NULL) — по created_at desc.
        """
        now = utcnow()
        result = await self._db.scalars(
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.is_active.is_(True),
                Session.expires_at > now,
            )
            .order_by(
                # NULLS LAST для last_used_at, чтобы только-что-логиненная
                # сессия (без last_used_at) шла в конце, а реально активные —
                # сверху. PostgreSQL поддерживает NULLS LAST нативно.
                Session.last_used_at.desc().nulls_last(),
                Session.created_at.desc(),
            )
        )
        return list(result)

    async def mark_suspicious(self, sess: Session) -> None:
        sess.is_suspicious = True
        await self._db.flush()
