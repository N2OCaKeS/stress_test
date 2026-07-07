"""DAO для `OAuthRefreshToken` — CRUD + CAS rotate (reuse-detection).

Зеркало `SessionRepository`: lookup по hash, атомарная CAS-ротация,
reuse-detection по sliding-window истории и kill-switch по всей цепочке
(client_id, user_id).
"""

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.oauth_refresh_token import PREVIOUS_TOKEN_HASH_WINDOW, OAuthRefreshToken
from src.repositories._cas import atomic_transition
from src.utils.ids import oauth_refresh_token_id
from src.utils.time import utcnow


class OAuthRefreshTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_active_by_token_hash(self, token_hash: str) -> OAuthRefreshToken | None:
        return await self._db.scalar(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.refresh_token_hash == token_hash,
                OAuthRefreshToken.is_active.is_(True),
            )
        )

    async def find_rotated_by_old_hash(self, token_hash: str) -> OAuthRefreshToken | None:
        """Найти цепочку, в истории которой лежит `token_hash`.

        Матчит ТОЛЬКО `previous_token_hashes` (не текущий `refresh_token_hash`).
        Так refresh-flow различает:

        * Цепочка revoked (logout / client deactivate), ротации не было: текущий
          hash всё ещё равен предъявленному — это не reuse, kill-switch не нужен.
        * Старый ротированный refresh подсунут заново: hash в истории — реальный
          reuse, бьём по всей цепочке (`revoke_chain`).

        Зеркало `SessionRepository.find_rotated_by_old_hash`.
        """
        return await self._db.scalar(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.previous_token_hashes.contains([token_hash])
            )
        )

    @staticmethod
    def is_grace_window_rotation(
        token: OAuthRefreshToken, token_hash: str, grace_seconds: int
    ) -> bool:
        """Проигравший benign-гонку ретраит непосредственно-предыдущим hash'ем?

        Зеркало `SessionRepository.is_grace_window_rotation`: True только если
        цепочка активна, `token_hash` — хвост `previous_token_hashes` (только
        что ротированный), а с момента ротации прошло не больше `grace_seconds`.
        Иначе (окно выключено / не последний hash / вне окна) — False, и caller
        бьёт kill-switch по цепочке как раньше.
        """
        if grace_seconds <= 0 or not token.is_active or token.last_used_at is None:
            return False
        window = list(token.previous_token_hashes or [])
        immediately_prev = window[-1] if window else None
        if immediately_prev is None or token_hash != immediately_prev:
            return False
        last_used = token.last_used_at
        if last_used.tzinfo is None:
            from datetime import timezone
            last_used = last_used.replace(tzinfo=timezone.utc)
        return (utcnow() - last_used).total_seconds() <= grace_seconds

    async def create(
        self,
        client_id: str,
        user_id: str,
        refresh_token_hash: str,
        scopes: list[str],
        expires_at,
    ) -> OAuthRefreshToken:
        token = OAuthRefreshToken(
            id=oauth_refresh_token_id(),
            client_id=client_id,
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            scopes=scopes,
            expires_at=expires_at,
        )
        self._db.add(token)
        await self._db.flush()
        return token

    async def rotate(
        self,
        token: OAuthRefreshToken,
        new_hash: str,
        new_expires_at,
    ) -> bool:
        """Атомарная замена `refresh_token_hash` через CAS.

        UPDATE матчит по `id` И ожидаемому текущему `refresh_token_hash` (плюс
        `is_active`). Если параллельный обмен уже ротировал строку — WHERE не
        находит её, `RETURNING` пуст, возвращаем `False`. Caller трактует это
        как benign-race (НЕ reuse: не зовём kill-switch).

        Тот же CAS-шаблон и sliding-window логика, что в
        `SessionRepository.rotate`.
        """
        expected_hash = token.refresh_token_hash
        now = utcnow()
        prev_window = list(token.previous_token_hashes or [])
        prev_window.append(expected_hash)
        if len(prev_window) > PREVIOUS_TOKEN_HASH_WINDOW:
            prev_window = prev_window[-PREVIOUS_TOKEN_HASH_WINDOW:]

        won = await atomic_transition(
            self._db,
            OAuthRefreshToken,
            id_column="id",
            id_value=token.id,
            where_clause=and_(
                OAuthRefreshToken.refresh_token_hash == expected_hash,
                OAuthRefreshToken.is_active.is_(True),
            ),
            update_values={
                "previous_token_hashes": prev_window,
                "refresh_token_hash": new_hash,
                "token_generation": OAuthRefreshToken.token_generation + 1,
                "expires_at": new_expires_at,
                "last_used_at": now,
            },
        )
        if not won:
            return False
        token.previous_token_hashes = prev_window
        token.refresh_token_hash = new_hash
        token.token_generation += 1
        token.expires_at = new_expires_at
        token.last_used_at = now
        return True

    async def revoke(self, token: OAuthRefreshToken) -> None:
        token.is_active = False
        token.revoked_at = utcnow()
        await self._db.flush()

    async def mark_suspicious(self, token: OAuthRefreshToken) -> None:
        token.is_suspicious = True
        await self._db.flush()

    async def revoke_chain(self, client_id: str, user_id: str) -> int:
        """Revoke все активные refresh данной пары (client_id, user_id).

        Kill-switch при reuse-detection: подсунули ротированный refresh —
        гасим всю цепочку этого клиента для этого юзера. Зеркало
        `SessionRepository.revoke_all_for_user`, но скоупится клиентом, чтобы
        компрометация одного third-party app не выкидывала юзера из остальных.

        Возвращает число фактически revoked'нутых токенов.
        """
        now = utcnow()
        result = await self._db.scalars(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.client_id == client_id,
                OAuthRefreshToken.user_id == user_id,
                OAuthRefreshToken.is_active.is_(True),
            )
        )
        count = 0
        for token in result:
            token.is_active = False
            token.revoked_at = now
            count += 1
        await self._db.flush()
        return count
