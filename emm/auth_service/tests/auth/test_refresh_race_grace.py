"""Grace-окно на непосредственно-предыдущий refresh-hash.

Проигравший benign-гонку `/refresh` может наивно ретрайнуть СТАРЫМ (только что
ротированным) токеном — нового у него на руках нет. В пределах
`REFRESH_RACE_GRACE_SECONDS` это не reuse, а RACE: сессия остаётся живой,
kill-switch не срабатывает. Вне окна (или если предъявлен не последний в
истории hash) — это по-прежнему reuse со сносом всех сессий юзера.
"""

from datetime import timedelta

from sqlalchemy import select, update

from src.core.config import get_settings
from src.core.security import hash_refresh_token
from src.models import Session
from src.utils.time import utcnow
from tests._helpers.http import login as _login

URL = "/api/auth/v1/refresh"


async def test_immediate_replay_within_grace_is_race_not_reuse(client, account_admin, db):
    """(а) Повтор непосредственно-предыдущим hash'ем в пределах grace → RACE,
    сессия жива, никакого kill-switch."""
    data = await _login(client)
    old_rt = data["refresh_token"]

    first = await client.post(URL, json={"refresh_token": old_rt})
    assert first.status_code == 200
    new_rt = first.json()["refresh_token"]

    # Немедленный повтор старого токена — в пределах grace-окна.
    replay = await client.post(URL, json={"refresh_token": old_rt})
    assert replay.status_code == 401
    assert replay.json()["error_code"] == "REFRESH_TOKEN_RACE"

    # Сессия не убита: свежий токен победителя всё ещё работает.
    still = await client.post(URL, json={"refresh_token": new_rt})
    assert still.status_code == 200


async def test_race_within_grace_does_not_revoke_other_sessions(client, account_admin, db):
    """Grace-повтор одной сессии не задевает другие сессии того же юзера."""
    s1 = await _login(client)
    s2 = await _login(client)

    old_rt = s1["refresh_token"]
    first = await client.post(URL, json={"refresh_token": old_rt})
    assert first.status_code == 200

    replay = await client.post(URL, json={"refresh_token": old_rt})
    assert replay.json()["error_code"] == "REFRESH_TOKEN_RACE"

    # Вторая сессия жива (kill-switch не сработал).
    alive = await client.post(URL, json={"refresh_token": s2["refresh_token"]})
    assert alive.status_code == 200


async def test_replay_outside_grace_window_is_reuse(client, account_admin, db):
    """(б) Тот же непосредственно-предыдущий hash, но ротация была давно
    (вне grace-окна) → reuse: 401 INVALID + revoke всех сессий."""
    s1 = await _login(client)
    s2 = await _login(client)
    old_rt = s1["refresh_token"]

    first = await client.post(URL, json={"refresh_token": old_rt})
    assert first.status_code == 200

    # Сдвигаем момент ротации сессии за пределы grace-окна.
    grace = get_settings().refresh_race_grace_seconds
    await db.execute(
        update(Session)
        .where(Session.previous_token_hash == hash_refresh_token(old_rt))
        .values(last_used_at=utcnow() - timedelta(seconds=grace + 60))
    )
    await db.commit()

    replay = await client.post(URL, json={"refresh_token": old_rt})
    assert replay.status_code == 401
    assert replay.json()["error_code"] == "REFRESH_TOKEN_INVALID"

    # Kill-switch: другая сессия юзера тоже отозвана.
    dead = await client.post(URL, json={"refresh_token": s2["refresh_token"]})
    assert dead.status_code == 401

    # В БД активных сессий юзера не осталось.
    rows = (await db.execute(
        select(Session).where(Session.user_id == account_admin.id, Session.is_active.is_(True))
    )).scalars().all()
    assert rows == []


async def test_grace_disabled_treats_immediate_replay_as_reuse(client, account_admin, db, monkeypatch):
    """REFRESH_RACE_GRACE_SECONDS=0 → окно выключено, немедленный повтор старого
    токена снова классический reuse."""
    monkeypatch.setattr(get_settings(), "refresh_race_grace_seconds", 0)

    data = await _login(client)
    old_rt = data["refresh_token"]
    first = await client.post(URL, json={"refresh_token": old_rt})
    assert first.status_code == 200

    replay = await client.post(URL, json={"refresh_token": old_rt})
    assert replay.status_code == 401
    assert replay.json()["error_code"] == "REFRESH_TOKEN_INVALID"
