"""Генерация ID с префиксами."""

import uuid


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def audit_event_id() -> str:
    return _new_id("log_")


def audit_rule_id() -> str:
    return _new_id("rl_")


def service_event_id() -> str:
    return _new_id("se_")
