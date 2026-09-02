from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from src.schemas.ipmi_controller import IpmiControllerCreate


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_ipmi_create_accepts_existing_bmc_password_outside_dbos_policy():
    spec = IpmiControllerCreate(
        kind="ipmi",
        endpoint_url="http://bmc.example.local:623",
        username="root",
        password_b64=_b64("admin"),
    )

    assert spec.password() == "admin"


def test_ipmi_create_rejects_invalid_base64_password():
    with pytest.raises(ValidationError, match="password_b64 is not valid base64"):
        IpmiControllerCreate(
            kind="ipmi",
            endpoint_url="http://bmc.example.local:623",
            username="root",
            password_b64="not base64!",
        )
