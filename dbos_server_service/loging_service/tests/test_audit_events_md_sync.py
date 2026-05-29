"""Sync-проверка `_DEFAULT_SEVERITY` (код) vs таблицы в AUDIT_EVENTS.md.

`AUDIT_EVENTS.md` дублирует контент `_DEFAULT_SEVERITY` в нескольких таблицах
(HTTP middleware, retention, admin actions, severity-override). Доки расходятся
с кодом, если кто-то правит только одну сторону. Этот тест парсит markdown
и для каждой `(action, status, severity)` строки проверяет, что соответствие
в коде совпадает или явно опущено в whitelist'е.

Контракт:
  * Источник истины — `_DEFAULT_SEVERITY` в `services/rule_service.py`.
    Расхождение severity между кодом и AUDIT_EVENTS.md — фейл теста,
    чинится правкой markdown'а (или кода, если на самом деле менялась
    политика severity).
  * Local action'ы (`logging.*`, `http.*` и др. — те, что эмитит сам
    loging_service) должны присутствовать в обеих сторонах. MD-строка
    для local action'а без записи в `_DEFAULT_SEVERITY` — тоже фейл
    (или action удалён из кода, и markdown отстал).
  * External action'ы (`user.*`, `server.*`, `token.*` и т.д. — эмитятся
    auth/server/worker сервисами) живут в их собственных AUDIT_EVENTS.md.
    Здесь проверяем такие строки только если они случайно дублируются в
    нашем `_DEFAULT_SEVERITY`-таблице — тогда severity должен совпадать.
    Иначе пропускаем по `_EXTERNAL_TO_DOC` prefix-whitelist'у.

Не превращаем код в источник истины автоматически — таблицы в MD несут
человеческий контекст («Когда возникает», `target_type`), который из dict'а
не восстанавливается. Проверка достаточна, чтобы фиксить расхождения
точечно.
"""

import re
from pathlib import Path

from src.services.rule_service import _DEFAULT_SEVERITY


# Корень репо вычисляем относительно файла теста: tests/ → loging_service/.
_AUDIT_EVENTS_PATH = (
    Path(__file__).resolve().parent.parent / "AUDIT_EVENTS.md"
)

# Action'ы, которые в `_DEFAULT_SEVERITY` есть, но в `AUDIT_EVENTS.md` живут
# в отдельных файлах (auth/server/worker — собственный AUDIT_EVENTS у каждого).
# Сюда же — служебные http.* (general schema, конкретики по запросам нет).
_EXTERNAL_TO_DOC = frozenset({
    # auth_service AUDIT_EVENTS.md — отдельный файл, тут не валидируем.
    "user", "token", "department", "group", "service", "service_role",
    "pat", "bot", "oauth_client", "oauth", "docker_registry", "docker",
    # server_service / server_worker AUDIT_EVENTS — отдельные файлы.
    "server", "server_account", "ipmi_controller", "installed_packages",
})


def _action_prefix(action: str) -> str:
    """Возвращает `<object>` часть action'а до первой точки."""
    return action.split(".", 1)[0]


def _parse_md_table_rows(text: str) -> list[tuple[str, str, str]]:
    r"""Извлекает (action, status, severity) из всех таблиц AUDIT_EVENTS.md.

    Ловим строки вида ``| `action.x` | `success` | INFO | ... |``. Колонки
    разные в разных таблицах, поэтому фильтруем по pattern'у action+status в
    первых двух cell'ах + severity-token в третьей.
    """
    severity_tokens = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    status_tokens = {"success", "failure", "denied", "warning"}
    row_re = re.compile(r"^\|(.+)\|$")
    cell_strip_re = re.compile(r"^`(.+)`$")

    rows: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        m = row_re.match(raw.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 3:
            continue

        def _unwrap(cell: str) -> str:
            mm = cell_strip_re.match(cell)
            return mm.group(1) if mm else cell

        action = _unwrap(cells[0])
        status = _unwrap(cells[1])
        sev = _unwrap(cells[2])
        if status not in status_tokens or sev not in severity_tokens:
            continue
        # `action` может содержать дополнительный backtick-wrap или иметь
        # суффикс с alt-формами. Берём только то, что выглядит как
        # `<object>.<verb>`.
        if not re.fullmatch(r"[a-z0-9_.]+", action):
            continue
        rows.append((action, status, sev))
    return rows


def test_audit_events_md_severity_matches_default_severity():
    md_text = _AUDIT_EVENTS_PATH.read_text(encoding="utf-8")
    rows = _parse_md_table_rows(md_text)
    # Ожидаем как минимум несколько local-action'ов (logging.* / http.*).
    assert any(a.startswith("logging.") for a, _, _ in rows), (
        "AUDIT_EVENTS.md parse не выловил ни одного logging.* row — "
        "разъехался формат таблицы?"
    )

    mismatches: list[str] = []
    missing_in_code: list[str] = []
    for action, status, sev in rows:
        if _action_prefix(action) in _EXTERNAL_TO_DOC:
            # Кросс-сервисный action — owner'ы доки в своих AUDIT_EVENTS.md;
            # проверяется только если есть совпадение в нашем `_DEFAULT_SEVERITY`.
            code_sev = _DEFAULT_SEVERITY.get((action, status))
            if code_sev is not None and code_sev != sev:
                mismatches.append(
                    f"{action}/{status}: code={code_sev}, AUDIT_EVENTS.md={sev}"
                )
            continue
        code_sev = _DEFAULT_SEVERITY.get((action, status))
        if code_sev is None:
            missing_in_code.append(f"{action}/{status} (md says {sev})")
        elif code_sev != sev:
            mismatches.append(
                f"{action}/{status}: code={code_sev}, AUDIT_EVENTS.md={sev}"
            )

    assert not mismatches, "severity desync:\n  " + "\n  ".join(mismatches)
    assert not missing_in_code, (
        "AUDIT_EVENTS.md содержит local action, которого нет в _DEFAULT_SEVERITY:\n  "
        + "\n  ".join(missing_in_code)
    )
