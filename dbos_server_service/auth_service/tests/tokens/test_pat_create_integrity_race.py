"""PAT create — `IntegrityError` на partial unique переводится в 409.

`exists_name`-чек на app-уровне может проиграть гонку: два параллельных
`POST /tokens` с одинаковым `name` оба видят `exists_name=False`, оба
создают PAT, второй коммит уносит `IntegrityError` на
`uq_pat_user_name_active`. До фикса оно поднималось до global error
handler'а как 500; теперь мы заворачиваем в стабильный 409
`TOKEN_NAME_ALREADY_EXISTS` — тот же error_code, что и при честно
пройденном `exists_name`-чеке.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError
from src.services import token_service


@pytest.mark.asyncio
async def test_create_pat_integrity_error_becomes_409(
    db, user_a, service_x, monkeypatch,
):
    """`token_repo.create` падает с IntegrityError → ConflictError, не 500."""
    from src.repositories import tokens as tokens_repo_mod

    async def _raise_integrity(self, **kwargs):
        raise IntegrityError(
            "INSERT INTO personal_access_tokens ...",
            params=None,
            orig=Exception("duplicate key value violates unique constraint"),
        )

    monkeypatch.setattr(tokens_repo_mod.TokenRepository, "create", _raise_integrity)

    with pytest.raises(ConflictError) as ei:
        await token_service.create_pat(
            db,
            actor_id=user_a.id,
            name="race_pat",
            allowed_services=[service_x.service_name],
        )
    assert ei.value.error_code == "TOKEN_NAME_ALREADY_EXISTS"
