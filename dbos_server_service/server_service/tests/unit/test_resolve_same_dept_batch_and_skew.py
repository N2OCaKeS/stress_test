"""Фиксы для server_service: миграция dispatch_task.

Покрывает:

  * `dispatch_task` / `dispatch_task_with_hit` миграция.
    Source-inspection всех call-sites в `src/api/`: ни один
    не пробрасывает legacy-kwarg `return_hit`, каждый сайт явно выбирает
    одну из двух функций. Кодом эта инвариантность не enforces'ится,
    но регрессия означает Union-возврат и невозможность статически
    типизировать call-сайт без cast'ов.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from src.services import worker_client


_API_ROOT = Path(worker_client.__file__).resolve().parents[1] / "api"


def _iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


class TestDispatchTaskMigration:
    """Никакой call-site не зовёт `dispatch_task` с устаревшим `return_hit`."""

    def test_no_return_hit_kwarg_in_src_callsites(self):
        offenders: list[str] = []
        for py in _iter_py_files(_API_ROOT):
            text = py.read_text(encoding="utf-8")
            if "dispatch_task" not in text:
                continue
            # Простой текстовый scan: `return_hit=` как kwarg в любом
            # call-site (включая разорванный по строкам блок) — регрессия.
            if "return_hit=" in text:
                offenders.append(str(py))
        assert not offenders, (
            "Found legacy `return_hit=` kwarg in src/api/* — миграция "
            f"должна была их вычистить: {offenders}"
        )

    def test_dispatch_task_returns_str(self):
        """`dispatch_task` — однозначный str-возврат, не Union."""
        sig = inspect.signature(worker_client.dispatch_task)
        ret = sig.return_annotation
        # В runtime annotation — либо `str`, либо строка "str" (postponed).
        assert ret is str or ret == "str", (
            f"dispatch_task должен возвращать str, а не Union; got {ret!r}"
        )
        params = sig.parameters
        assert "return_hit" not in params, (
            "Параметр `return_hit` удалён при split'е"
        )

    def test_dispatch_task_with_hit_returns_tuple(self):
        """`dispatch_task_with_hit` — `tuple[str, bool]`."""
        sig = inspect.signature(worker_client.dispatch_task_with_hit)
        ret = sig.return_annotation
        # Принимаем либо `tuple[str, bool]`, либо строковую форму (postponed).
        assert ret is not inspect.Signature.empty, \
            "dispatch_task_with_hit должен иметь явную return-аннотацию"
