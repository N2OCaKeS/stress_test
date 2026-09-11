#!/usr/bin/env python3
"""Одноразовый импорт каталога тестов и стендов (§13 плана миграции).

Читает JSON/YAML-файл с двумя верхнеуровневыми разделами (`stands`, `tests`)
и заводит записи через обычный сервисный слой (`services/test_stand.py`,
`services/test_definition.py`, `services/test_command_arg.py`) — те же
use case'ы, что дёргает HTTP-API, поэтому создание идёт с той же бизнес-
логикой и тем же аудитом, без параллельного пути записи в БД.

Запуск:
    cd emm/testing_service
    PYTHONPATH=. DATABASE_URL=postgresql+psycopg://... AUTH_SERVICE_URL=... \\
        python scripts/import_catalog.py scripts/import_catalog.example.yaml

Формат входного файла и реальные источники данных (server_id из
server_service, флаги легаси-тестов из allta_app/backup_image.py) — см.
`import_catalog.example.yaml` и раздел «Импорт» в README.md.

Идентичность вызывающего. Сервисные функции проверяют право на создание
через `entity_permissions` (`permissions.require_action`), но права зоны
`test_definition`/`test_stand` сеются системной ролью `admin` с
`department_id IS NULL` (см. миграции `d8f4c1a97e63`/`7b22409f4211`) — то
есть достаточно `Identity` с `service_roles={"testing_service": ["admin"]}`,
без привязки к реальному пользователю в auth_service. Скрипт строит такой
`Identity` локально (`_SYSTEM_IDENTITY` ниже) и не делает HTTP-логин —
это доверенный офлайн-инструмент с прямым доступом к БД сервиса, отдельная
проверка через auth_service тут ничего не добавляет.

Стенды — исключение: `create_test_stand` резолвит `department_id` живым
pass-through вызовом к `server_service` (`GET /servers/{id}`, тем же bearer'ом,
которым видит сервер вызывающий) — обойти это нельзя, не изобретая
параллельный путь записи. Раздел `stands` поэтому требует реальный
`--bearer-token` (или `IMPORT_BEARER_TOKEN` в env) от пользователя, который
видит нужные сервера в server_service; без токена стенды пропускаются с
понятной ошибкой на каждую позицию, а не тихо.

Идемпотентность: `code`/`server_id`, которые уже есть в БД — пропуск с
пометкой skip, не падение и не дубль. Одна плохая позиция (опечатка в
variable_code, недостижимый server_service, конфликт по коду) не должна
останавливать обработку остальных — каждая позиция оборачивается отдельно,
в конце печатается сводка created/skipped/failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.db.session import AsyncSessionLocal
from src.dependencies.auth import Identity
from src.repositories import global_variable as global_variable_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.schemas.test_command_arg import TestCommandArgCreate
from src.schemas.test_definition import TestDefinitionCreate
from src.schemas.test_stand import TestStandCreate
from src.services import audit_context, audit_service, test_command_arg, test_definition, test_stand

logger = logging.getLogger("import_catalog")

# Права зоны test_definition/test_stand сидятся системной роли `admin`,
# department_id IS NULL (system-wide) — Identity без department_id матчит эти
# строки (см. entity_permission.repo._visible_scope_clause). Не привязан к
# реальному user_id в auth_service, только к записям created_by в этой БД.
_SYSTEM_IDENTITY = Identity(
    user_id="scr_import_catalog",
    username="import_catalog_script",
    actor_type="service",
    department_id=None,
    allowed_services=["testing_service"],
    service_roles={"testing_service": ["admin"]},
    is_banned=False,
    platform_role=None,
)

_AUDIT_DRAIN_TIMEOUT_SECONDS = 3.0


@dataclass
class ImportStats:
    """Счётчики + текст ошибок для финального отчёта."""

    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.created += 1
        print(f"  + {msg}")

    def skip(self, msg: str) -> None:
        self.skipped += 1
        print(f"  · {msg}")

    def fail(self, msg: str, exc: Exception) -> None:
        self.failed += 1
        text = f"{msg}: {exc}"
        self.errors.append(text)
        print(f"  ! {text}", file=sys.stderr)


def _load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    # YAML — суперсет JSON, safe_load читает оба формата.
    return yaml.safe_load(text) or {}


async def _import_stands(
    db,
    items: list[dict],
    *,
    bearer_token: str | None,
    dry_run: bool,
    stats: ImportStats,
) -> None:
    if not items:
        return
    print(f"\nСтенды ({len(items)}):")

    if not bearer_token:
        for item in items:
            stats.fail(
                f"stand server_id={item.get('server_id')!r}",
                RuntimeError(
                    "нет --bearer-token / IMPORT_BEARER_TOKEN — создание стенда "
                    "резолвит department_id живым запросом к server_service под "
                    "bearer'ом вызывающего, обойти нельзя"
                ),
            )
        return

    for item in items:
        server_id = item.get("server_id")
        if not server_id:
            stats.fail("stand", ValueError("server_id обязателен"))
            continue

        existing = await test_stand_repo.get_by_server_id(db, server_id)
        if existing is not None:
            stats.skip(f"stand server_id={server_id} уже существует (id={existing.id})")
            continue

        if dry_run:
            stats.ok(f"[dry-run] stand server_id={server_id} — будет создан")
            continue

        payload = TestStandCreate(
            server_id=server_id,
            queue_enabled=item.get("queue_enabled", True),
            is_active=item.get("is_active", True),
        )
        try:
            obj = await test_stand.create_test_stand(db, _SYSTEM_IDENTITY, bearer_token, payload)
        except Exception as exc:  # noqa: BLE001 — одна плохая запись не должна ронять импорт
            await db.rollback()
            stats.fail(f"stand server_id={server_id}", exc)
            continue
        stats.ok(f"stand server_id={server_id} → {obj.id} (department_id={obj.department_id})")


async def _resolve_command_slots(db, commands: list[dict]) -> list[TestCommandArgCreate]:
    """Резолвит все variable_code теста ДО создания test_definition.

    Одна плохая ссылка на переменную не должна оставлять в БД наполовину
    заведённый тест (definition есть, часть слотов — нет) — поэтому весь
    список слотов провалидирован заранее, создание идёт только если резолв
    прошёл целиком.
    """
    resolved: list[TestCommandArgCreate] = []
    for position, item in enumerate(commands):
        kind = item.get("kind")
        variable_id = None
        if kind == "variable":
            code = item.get("variable_code")
            if not code:
                raise ValueError(f"слот #{position}: kind=variable требует variable_code")
            variable = await global_variable_repo.get_by_code(db, code)
            if variable is None:
                raise ValueError(f"слот #{position}: неизвестный variable_code={code!r}")
            variable_id = variable.id
        elif kind != "literal":
            raise ValueError(f"слот #{position}: неизвестный kind={kind!r}")
        resolved.append(TestCommandArgCreate(
            position=position,
            kind=kind,
            literal_value=item.get("literal_value"),
            variable_id=variable_id,
            override_value=item.get("override_value"),
        ))
    return resolved


async def _import_tests(db, items: list[dict], *, dry_run: bool, stats: ImportStats) -> None:
    if not items:
        return
    print(f"\nТесты ({len(items)}):")

    for item in items:
        code = item.get("code")
        if not code:
            stats.fail("test", ValueError("code обязателен"))
            continue

        existing = await test_definition_repo.get_by_code(db, code)
        if existing is not None:
            stats.skip(f"test {code} уже существует (id={existing.id})")
            continue

        try:
            slots = await _resolve_command_slots(db, item.get("command") or [])
        except ValueError as exc:
            stats.fail(f"test {code}", exc)
            continue

        if dry_run:
            stats.ok(f"[dry-run] test {code} — будет создан ({len(slots)} слот(ов))")
            continue

        payload = TestDefinitionCreate(
            code=code,
            full_name=item.get("full_name") or code,
            category=item.get("category"),
            owner=item.get("owner"),
            readiness=item.get("readiness"),
            department_id=item.get("department_id"),
            pinned_stand_id=item.get("pinned_stand_id"),
            changelog_component=item.get("changelog_component"),
            starter_suffix=item.get("starter_suffix"),
        )
        try:
            obj = await test_definition.create_test_definition(db, _SYSTEM_IDENTITY, payload)
        except Exception as exc:  # noqa: BLE001 — одна плохая запись не должна ронять импорт
            await db.rollback()
            stats.fail(f"test {code}", exc)
            continue

        try:
            for slot in slots:
                await test_command_arg.create_command_arg(db, _SYSTEM_IDENTITY, obj.id, slot)
        except Exception as exc:  # noqa: BLE001 — тест уже создан, слоты — best effort
            stats.fail(f"test {code} создан, но слоты команды — нет ({obj.id})", exc)
            continue

        stats.ok(f"test {code} → {obj.id} ({len(slots)} слот(ов))")


async def _drain_pending_audit_tasks() -> None:
    """Дать шанс дойти до сети in-flight audit-emit task'ам перед выходом.

    Тот же приём, что `src/main.py::_drain_pending_audit_tasks` на shutdown'е —
    `audit_service.emit` шедулит отправку через `create_task`, а после
    `asyncio.run()` ничего не гарантирует, что она успела уйти.
    """
    pending = [t for t in audit_service._pending_audit_tasks if not t.done()]
    if not pending:
        return
    _done, still_pending = await asyncio.wait(pending, timeout=_AUDIT_DRAIN_TIMEOUT_SECONDS)
    if still_pending:
        logger.warning(
            "%d audit-emit задач(а) не успели уйти за %.1fs",
            len(still_pending), _AUDIT_DRAIN_TIMEOUT_SECONDS,
        )


async def run(path: Path, *, bearer_token: str | None, dry_run: bool) -> int:
    data = _load(path)
    stands = data.get("stands") or []
    tests = data.get("tests") or []

    audit_context.update_context(
        actor_id=_SYSTEM_IDENTITY.user_id,
        username=_SYSTEM_IDENTITY.username,
        subject_type=_SYSTEM_IDENTITY.actor_type,
    )

    stats = ImportStats()
    async with AsyncSessionLocal() as db:
        await _import_stands(db, stands, bearer_token=bearer_token, dry_run=dry_run, stats=stats)
        await _import_tests(db, tests, dry_run=dry_run, stats=stats)
    await _drain_pending_audit_tasks()

    print(f"\nDone: created={stats.created} skipped={stats.skipped} failed={stats.failed}")
    return 1 if stats.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="Путь к JSON/YAML-файлу импорта.")
    parser.add_argument(
        "--bearer-token",
        default=os.environ.get("IMPORT_BEARER_TOKEN"),
        help="Bearer-токен пользователя, видящего нужные сервера в server_service "
             "(нужен только разделу `stands`). Можно задать через IMPORT_BEARER_TOKEN.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только резолв и проверка идемпотентности, без записи в БД и без "
             "живых вызовов к server_service.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.path.exists():
        parser.error(f"файл не найден: {args.path}")

    return asyncio.run(run(args.path, bearer_token=args.bearer_token, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
