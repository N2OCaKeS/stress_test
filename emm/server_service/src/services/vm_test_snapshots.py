"""Выбор снимка ВМ для отката перед тестом.

Как у ACS: сравнение версии после `normalize_os_version_name`, нет снимка —
`VM_SNAPSHOT_NOT_FOUND`. Отличие — схема имени задаётся настраиваемыми
шаблонами (`vm_test_settings.snapshot_name_templates`), а не одной формулой.

Кандидаты — снимки `vm_snapshots` в `ready`, кроме системных `<ver>_build`.
Несколько подходящих — порядок шаблонов, затем точное совпадение версии,
затем имя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import VmSnapshotState
from src.core.exceptions import DomainValidationError
from src.core.known_os import normalize_os_version_name
from src.models import Vm, VmSnapshot
from src.models.vm_test_settings import (
    DEFAULT_SNAPSHOT_NAME_TEMPLATES,
    SINGLETON_ID,
    VmTestSettings,
)
from src.repositories import vm_snapshot as vm_snapshot_repo
from src.services import audit_service

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_ALLOWED_PLACEHOLDERS = {"version", "mode", "hostname", "vm_name"}
# Режимы Astra, которые знает пайплайн подготовки (`PREPARE_FOR_TEST_MODES`).
_MODES = ("orel", "smolensk")
# Захваченная `{version}` должна после нормализации выглядеть как версия
# (`1.7.5.9`, `1.7.5.UU.1.7`) — иначе шаблон `{version}` съел бы и
# `1.7.5.9_orel` целиком.
_VERSION_LIKE_RE = re.compile(r"^\d+(?:\.(?:\d+|UU))+$")
_MAX_TEMPLATES = 10
_MAX_TEMPLATE_LEN = 128


@dataclass(frozen=True)
class SnapshotMatch:
    template: str
    template_index: int
    version_name: str
    normalized_version: str
    mode: str | None


def validate_templates(templates: list[str]) -> list[str]:
    """Проверить шаблоны перед сохранением; 422 `VM_SNAPSHOT_TEMPLATE_INVALID`."""
    cleaned = [t.strip() for t in templates]
    problems: list[str] = []
    if not cleaned:
        problems.append("нужен хотя бы один шаблон")
    if len(cleaned) > _MAX_TEMPLATES:
        problems.append(f"не больше {_MAX_TEMPLATES} шаблонов")
    for template in cleaned:
        names = _PLACEHOLDER_RE.findall(template)
        unknown = sorted(set(names) - _ALLOWED_PLACEHOLDERS)
        if not template or len(template) > _MAX_TEMPLATE_LEN:
            problems.append(f"«{template}»: длина 1..{_MAX_TEMPLATE_LEN}")
        elif unknown:
            problems.append(f"«{template}»: неизвестные плейсхолдеры {unknown}")
        elif names.count("version") != 1:
            problems.append(f"«{template}»: `{{version}}` должен встречаться ровно один раз")
        elif any(names.count(name) > 1 for name in ("mode", "hostname", "vm_name")):
            problems.append(f"«{template}»: плейсхолдер повторяется")
    if len(set(cleaned)) != len(cleaned):
        problems.append("шаблоны повторяются")
    if problems:
        raise DomainValidationError(
            error_code="VM_SNAPSHOT_TEMPLATE_INVALID",
            message="; ".join(problems),
            details={"templates": cleaned},
        )
    return cleaned


async def get_templates(db: AsyncSession) -> list[str]:
    """Действующие шаблоны; нет строки (до миграции) — легаси-дефолт."""
    row = await db.get(VmTestSettings, SINGLETON_ID)
    if row is None or not row.snapshot_name_templates:
        return list(DEFAULT_SNAPSHOT_NAME_TEMPLATES)
    return [str(t) for t in row.snapshot_name_templates]


async def update_templates(db: AsyncSession, templates: list[str], *, updated_by: str | None) -> list[str]:
    """Сохранить шаблоны (платформенная настройка). Audit `settings.vm_test_updated`."""
    cleaned = validate_templates(templates)
    row = await db.get(VmTestSettings, SINGLETON_ID)
    previous = list(row.snapshot_name_templates) if row is not None else list(DEFAULT_SNAPSHOT_NAME_TEMPLATES)
    if row is None:
        row = VmTestSettings(id=SINGLETON_ID, snapshot_name_templates=cleaned, updated_by=updated_by)
        db.add(row)
    else:
        row.snapshot_name_templates = cleaned
        row.updated_by = updated_by
    await db.commit()
    audit_service.emit(
        "settings.vm_test_updated", target_id=SINGLETON_ID, target_type="settings",
        status="success", allowed=True,
        details={"snapshot_name_templates": cleaned, "previous": previous},
    )
    return cleaned


def _guest_hostname(vm: Vm) -> str:
    return vm.hostname or vm.name


def _compile(template: str, vm: Vm, mode: str | None) -> re.Pattern:
    """Шаблон → регулярка. `{mode}` — конкретный режим или любой из известных."""
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(template):
        parts.append(re.escape(template[pos:m.start()]))
        name = m.group(1)
        if name == "version":
            parts.append(r"(?P<version>.+?)")
        elif name == "mode":
            modes = (mode,) if mode else _MODES
            parts.append("(?P<mode>" + "|".join(re.escape(x) for x in modes) + ")")
        elif name == "hostname":
            parts.append(re.escape(_guest_hostname(vm)))
        elif name == "vm_name":
            parts.append(re.escape(vm.name))
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts))


def match_snapshot_name(
    name: str, vm: Vm, templates: list[str], *, mode: str | None = None,
) -> SnapshotMatch | None:
    """Первый шаблон, под который подходит имя снимка (с версией-подобным хвостом)."""
    for index, template in enumerate(templates):
        m = _compile(template, vm, mode).fullmatch(name)
        if m is None:
            continue
        raw = m.group("version")
        normalized = normalize_os_version_name(raw)
        if not _VERSION_LIKE_RE.fullmatch(normalized):
            continue
        return SnapshotMatch(
            template=template, template_index=index, version_name=raw,
            normalized_version=normalized, mode=m.groupdict().get("mode"),
        )
    return None


def render_names(vm: Vm, templates: list[str], version_name: str, mode: str | None) -> list[str]:
    """Имена, которые искали — для текста ошибки `VM_SNAPSHOT_NOT_FOUND`."""
    values = {
        "version": version_name, "mode": mode or "{mode}",
        "hostname": _guest_hostname(vm), "vm_name": vm.name,
    }
    return [_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], t) for t in templates]


def _is_candidate(snapshot: VmSnapshot) -> bool:
    return not snapshot.is_system and snapshot.state == VmSnapshotState.READY.value


async def list_matched(
    db: AsyncSession, vm: Vm, templates: list[str],
) -> list[tuple[VmSnapshot, SnapshotMatch | None]]:
    """Все пригодные для отката снимки ВМ с разбором имени (для списка и UI)."""
    snapshots = [s for s in await vm_snapshot_repo.list_all_for_vm(db, vm.id) if _is_candidate(s)]
    snapshots.sort(key=lambda s: s.name)
    return [(s, match_snapshot_name(s.name, vm, templates)) for s in snapshots]


async def find_snapshot(
    db: AsyncSession, vm: Vm, version_name: str, mode: str | None, templates: list[str],
) -> VmSnapshot | None:
    """Снимок для отката на версию `version_name` в режиме `mode`, либо None."""
    target = normalize_os_version_name(version_name)
    candidates: list[tuple[tuple, VmSnapshot]] = []
    for snapshot in await vm_snapshot_repo.list_all_for_vm(db, vm.id):
        if not _is_candidate(snapshot):
            continue
        match = match_snapshot_name(snapshot.name, vm, templates, mode=mode)
        if match is None or match.normalized_version != target:
            continue
        exact = match.version_name == version_name
        candidates.append(((match.template_index, not exact, snapshot.name), snapshot))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
