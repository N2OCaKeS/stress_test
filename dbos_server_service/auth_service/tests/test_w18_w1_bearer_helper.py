"""Smoke-тест для helper'а `bearer_header` в auth_service."""

from __future__ import annotations

from src.core.http import bearer_header


def test_bearer_header_basic():
    assert bearer_header("abc") == {"Authorization": "Bearer abc"}


def test_bearer_header_empty_token():
    # Helper не валидирует пустой токен — это контракт.
    assert bearer_header("") == {"Authorization": "Bearer "}


def test_bearer_header_spreadable():
    extra = {**bearer_header("xyz"), "X-Service-Identity": "auth_service"}
    assert extra["Authorization"] == "Bearer xyz"
    assert extra["X-Service-Identity"] == "auth_service"
