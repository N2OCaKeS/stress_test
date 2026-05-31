"""Smoke-тест для helper'а `bearer_header` в server_service."""

from __future__ import annotations

from src.core.http import bearer_header


def test_bearer_header_basic():
    assert bearer_header("abc") == {"Authorization": "Bearer abc"}


def test_bearer_header_empty_token():
    assert bearer_header("") == {"Authorization": "Bearer "}


def test_bearer_header_spreadable():
    extra = {**bearer_header("xyz"), "X-Target-Department-Id": "dept_1"}
    assert extra["Authorization"] == "Bearer xyz"
    assert extra["X-Target-Department-Id"] == "dept_1"
