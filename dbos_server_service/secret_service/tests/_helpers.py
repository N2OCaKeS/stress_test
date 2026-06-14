"""Маленькие хелперы для тестов secret_service."""

from __future__ import annotations

import base64


def b64(plaintext: str) -> str:
    """base64(plaintext) для поля secret_b64 в create/update-телах."""
    return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
