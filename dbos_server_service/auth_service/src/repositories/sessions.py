"""DAO для `Session` — CRUD refresh-сессий + CAS rotate (reuse-detection)."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.session import PREVIOUS_TOKEN_HASH_WINDOW, Session
from src.repositories._cas import atomic_transition
from src.utils.ids import session_id
from src.utils.time import utcnow


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

    async def rotate(self, sess: Session, new_hash: str, new_expires_at) -> bool:
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
            update_values={
                "previous_token_hash": expected_hash,
                "previous_token_hashes": prev_window,
                "refresh_token_hash": new_hash,
                "token_generation": Session.token_generation + 1,
                "expires_at": new_expires_at,
                "last_used_at": now,
            },
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
