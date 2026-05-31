"""Детектор подозрительной активности bot-токена с нескольких IP.

Каждый раз, когда сервис вызывает `/authorization/introspect` с bot-токеном,
sidecar-логика сюда записывает caller IP в `bot_accounts.last_known_ips`.
Если за последний час с этого бота прилетело >=2 разных IP — эмитим CRITICAL
audit `bot.suspicious_multi_ip`.

Окно `last_known_ips` ограничено `BOT_LAST_KNOWN_IPS_WINDOW` записями (FIFO):
для долго-живущего бота с большим парком CI-агентов нам важна только свежая
история, не вся жизнь токена. Старые записи вытесняются по timestamp.

Алерт не дедупится sidecar'ом — каждое «новое второе IP в часовом окне» даёт
своё событие. Suppress'ом занимается loging_service rule-engine, если SOC
посчитает шум избыточным.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models.bot_account import BotAccount
from src.services import audit_service


def _parse_ts(raw: str | None) -> datetime | None:
    """Распарсить ISO-timestamp из record'а `last_known_ips`. None при невалидном.

    JSONB-колонка теоретически может вернуть что угодно (int, dict, list) если
    кто-то записал кривой entry мимо нашего кода. Ловим TypeError помимо
    ValueError, чтобы битый row не валил весь трекер.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


async def track_bot_ip(
    db: AsyncSession,
    bot: BotAccount,
    caller_ip: str | None,
    request_id: str | None = None,
) -> bool:
    """Записать caller_ip в `bot.last_known_ips`, при необходимости — алертить.

    Возвращает True, если был эмитен `bot.suspicious_multi_ip` (для тестов).

    `caller_ip is None` — пропускаем (внутренние вызовы без проброса IP, либо
    отключенный trusted-proxy allow-list). Не считаем "новым IP" значение
    None, чтобы не путать счётчик уникальных адресов.

    Алгоритм:
      1. Берём `bot.last_known_ips` как есть, дописываем в хвост новую пару
         `{"ip": <ip>, "ts": <now iso>}`. Если последний хвостовой элемент
         с тем же IP — обновляем его ts (не плодим дубли подряд).
      2. Триммим окно до `BOT_LAST_KNOWN_IPS_WINDOW` элементов с конца.
      3. Считаем уникальные IP среди элементов с `ts >= now - 1h`.
      4. Если уникальных >=2 — emit CRITICAL `bot.suspicious_multi_ip` с
         деталями `{ips, bot_id, time_window_seconds: <окно>}`.

    Замечание про concurrency: два параллельных introspect'а одного бота
    могут перетереть `last_known_ips` друг друга (last-write-wins). При
    N>1 одновременных запросов с разных IP счётчик уникальных IP за окно
    может недосчитать одну-две записи — следующий introspect (или один из
    концурентных, который попадёт в commit позже) её догонит, и алерт
    `bot.suspicious_multi_ip` всё равно сработает. CAS через JSONB-update
    с условным WHERE не оправдан: окно из N IP — observability-сигнал,
    не security-инвариант; редкий миссинг одной записи терпим.
    """
    if not caller_ip:
        return False

    settings = get_settings()
    max_window = settings.bot_last_known_ips_window
    suspicious_window = timedelta(seconds=settings.bot_suspicious_ip_window_seconds)

    now = datetime.now(timezone.utc)
    # Берём поверхностный список, но мутируем только через новые dict'ы:
    # in-place правка `window[-1]["ts"] = ...` модифицировала бы тот же
    # объект, что лежит в `bot.last_known_ips`, и ORM не всегда видит
    # такую мутацию JSONB как изменение атрибута.
    window: list[dict] = list(bot.last_known_ips or [])

    # Если последняя запись — тот же самый IP, просто обновляем её ts.
    # Long-poll CI-агент с фиксированного IP не должен забивать окно.
    if window and window[-1].get("ip") == caller_ip:
        window[-1] = {**window[-1], "ts": now.isoformat()}
    else:
        window.append({"ip": caller_ip, "ts": now.isoformat()})

    if len(window) > max_window:
        window = window[-max_window:]

    bot.last_known_ips = window
    # Атрибут переприсваиваем целиком (а не мутируем in-place), поэтому ORM
    # сам видит dirty — flag_modified тут не нужен.
    await db.flush()

    cutoff = now - suspicious_window
    recent_ips: list[str] = []
    for entry in window:
        ts = _parse_ts(entry.get("ts"))
        if ts is None:
            continue
        # Naive datetime в JSON быть не должно (пишем с tz), но защищаемся.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            ip = entry.get("ip")
            if ip and ip not in recent_ips:
                recent_ips.append(ip)

    if len(recent_ips) >= 2:
        audit_service.emit(
            "bot.suspicious_multi_ip",
            bot.id,
            actor_type="bot",
            department_id=bot.department_id,
            target_id=bot.id,
            target_type="bot",
            status="failure",
            allowed=False,
            details={
                "bot_id": bot.id,
                "bot_name": bot.name,
                "ips": recent_ips,
                "time_window_seconds": settings.bot_suspicious_ip_window_seconds,
            },
            request_id=request_id,
        )
        return True

    return False
