"""create_pat должен нормализовать naive `expires_at` в UTC до записи в БД.

До фикса `create_pat` валидировал `exp_dt` (наивный→aware), но в
`token_repo.create(...)` улетал ОРИГИНАЛЬНЫЙ `expires_at` — асимметрия с
`create_bot_token`, который пишет нормализованный datetime. В итоге в БД
оказывался naive datetime, а response собирался из `pat.expires_at` —
рассинхрон с тем, что валидатор отсёк бы как «в прошлом» при aware-сравнении.
"""

from datetime import datetime, timedelta, timezone

from src.schemas.tokens import PATCreateResponse
from src.services import token_service


async def test_create_pat_naive_expires_at_stored_aware(
    db, user_a, service_x,
):
    """naive `expires_at` (без tz) → сохраняется в БД уже как aware UTC."""
    naive_future = datetime.utcnow() + timedelta(days=7)
    assert naive_future.tzinfo is None

    resp: PATCreateResponse = await token_service.create_pat(
        db,
        actor_id=user_a.id,
        name="naive_exp_pat",
        allowed_services=[service_x.service_name],
        expires_at=naive_future,
    )

    # Response собран из `pat.expires_at` — то, что реально в БД.
    assert resp.expires_at is not None
    assert resp.expires_at.tzinfo is not None, (
        "expires_at должен быть aware (UTC) — иначе клиент получает "
        "ambiguous datetime, и сравнения с aware-utcnow ломаются"
    )
    # Контент совпадает с тем, что отдал caller (после tz-normalize).
    expected = naive_future.replace(tzinfo=timezone.utc)
    assert resp.expires_at == expected


async def test_create_pat_aware_expires_at_passes_through(
    db, user_a, service_x,
):
    """aware `expires_at` пишется как есть, без двойной конверсии."""
    aware_future = datetime.now(timezone.utc) + timedelta(days=14)

    resp: PATCreateResponse = await token_service.create_pat(
        db,
        actor_id=user_a.id,
        name="aware_exp_pat",
        allowed_services=[service_x.service_name],
        expires_at=aware_future,
    )

    assert resp.expires_at == aware_future


async def test_create_pat_no_expires_at_stays_none(
    db, user_a, service_x,
):
    """expires_at=None — токен бессрочный (PAT-семантика отличается от bot)."""
    resp: PATCreateResponse = await token_service.create_pat(
        db,
        actor_id=user_a.id,
        name="no_exp_pat",
        allowed_services=[service_x.service_name],
        expires_at=None,
    )
    assert resp.expires_at is None
