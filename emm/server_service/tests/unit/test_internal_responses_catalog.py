"""Контракт responses-каталога internal-эндпоинтов.

Internal-роуты скрыты из OpenAPI (`include_in_schema=False`), поэтому их
`responses=`-каталог — чисто документация для SDK-codegen'а и читателей. Он
обязан совпадать с фактическими статус-кодами, которые поднимает
`internal_service`. Тест ловит drift: `record_ipmi_credentials_rotated`
raise'ит `ROTATED_AT_*` / `BMC_VERIFY_REQUIRED` как 400 (`BadRequestError`),
а не 422 — каталог должен это отражать.
"""

from __future__ import annotations

from src.api.v1.endpoints.internal import router


def _route(name: str):
    for r in router.routes:
        if getattr(r, "name", "") == name:
            return r
    raise AssertionError(f"route {name} not found")


class TestIpmiCredentialsRotatedResponses:
    def test_400_declared_with_correct_error_codes(self):
        route = _route("ipmi_credentials_rotated_callback")
        assert 400 in route.responses, "400 must be catalogued (ROTATED_AT_* / BMC_VERIFY_REQUIRED)"
        desc = route.responses[400]["description"]
        for code in ("ROTATED_AT_IN_FUTURE", "ROTATED_AT_TOO_OLD", "BMC_VERIFY_REQUIRED"):
            assert code in desc, f"{code} must be listed under 400"

    def test_422_does_not_claim_bmc_verify(self):
        """BMC_VERIFY_REQUIRED — это 400, не 422. Каталог 422 не должен врать."""
        route = _route("ipmi_credentials_rotated_callback")
        desc = route.responses.get(422, {}).get("description", "")
        assert "BMC_VERIFY" not in desc
        assert "IPMI_VERIFY_TOO_OLD" not in desc

    def test_409_still_declared(self):
        route = _route("ipmi_credentials_rotated_callback")
        assert "CREDENTIALS_ALREADY_APPLIED" in route.responses[409]["description"]

    def test_shared_callback_catalog_has_no_bmc_verify(self):
        """Общий callback-каталог тоже не должен приписывать BMC_VERIFY к 422 —
        иначе любой callback-роут унаследует неверный код."""
        from src.api.v1.endpoints.internal import _INTERNAL_RESPONSES_CALLBACK

        desc = _INTERNAL_RESPONSES_CALLBACK[422]["description"]
        assert "BMC_VERIFY" not in desc
