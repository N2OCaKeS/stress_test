from __future__ import annotations
from typing import Dict, Any, Iterable
from celery.utils.log import get_task_logger

log = get_task_logger(__name__)

def _require(envelope: Dict[str, Any], fields: Iterable[str]) -> None:
    miss = [f for f in fields if envelope.get(f) in (None, "", [], {})]
    if miss:
        raise ValueError(f"Envelope is missing required fields: {miss}")

def _std_ok(envelope: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "task_id": envelope.get("task_id"), "op": envelope.get("operation")}

def _std_error(envelope: Dict[str, Any], *, error: Exception, stage: str | None = None,
               command: str | None = None, exit_code: int | None = None,
               stdout: str | None = None, stderr: str | None = None,
               **extra) -> Dict[str, Any]:
    return {
        "task_id": envelope.get("task_id"),
        "operation": envelope.get("operation"),
        "status": "Error",
        "error": {
            "type": type(error).__name__,
            "message": str(error),
            "stage": stage,
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        },
        **extra,
    }