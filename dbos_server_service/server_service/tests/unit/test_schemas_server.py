"""Unit-тесты схем `src/schemas/server.py` (Pydantic-валидация).

Проверяем граничные значения: длины строк, диапазон ssh_port, IPv4/IPv6
парсинг — то, что декларировано в типах полей.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address

import pytest
from pydantic import ValidationError

from src.schemas.server import ServerCreate, ServerUpdate


_BASE_CREATE = {
    "hostname": "srv-01",
    "ip_address": "10.0.0.1",
    "department_id": "dep_a",
}


# ── ServerCreate ──────────────────────────────────────────────────────────────

class TestServerCreateHostname:
    def test_minimal_payload_passes(self):
        m = ServerCreate(**_BASE_CREATE)
        assert m.hostname == "srv-01"
        assert m.ssh_port == 22  # default

    def test_hostname_max_length_accepted(self):
        m = ServerCreate(**{**_BASE_CREATE, "hostname": "h" * 255})
        assert len(m.hostname) == 255

    def test_hostname_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, "hostname": "h" * 256})

    def test_display_name_max_length(self):
        ServerCreate(**{**_BASE_CREATE, "display_name": "d" * 256})
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, "display_name": "d" * 257})


class TestServerCreateIpParsing:
    def test_ipv4_string_parsed(self):
        m = ServerCreate(**_BASE_CREATE)
        assert isinstance(m.ip_address, IPv4Address)
        assert str(m.ip_address) == "10.0.0.1"

    def test_ipv6_string_parsed(self):
        m = ServerCreate(**{**_BASE_CREATE, "ip_address": "2001:db8::1"})
        assert isinstance(m.ip_address, IPv6Address)

    def test_compressed_ipv6_loopback(self):
        m = ServerCreate(**{**_BASE_CREATE, "ip_address": "::1"})
        assert isinstance(m.ip_address, IPv6Address)

    @pytest.mark.parametrize("bad", [
        "not-an-ip",
        "999.999.999.999",
        "10.0.0",
        "",
        "10.0.0.1/24",     # CIDR — не адрес
    ])
    def test_invalid_ip_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, "ip_address": bad})

    def test_mgmt_ip_optional(self):
        m = ServerCreate(**_BASE_CREATE)
        assert m.mgmt_ip_address is None

    def test_mgmt_ip_separate_family_from_main(self):
        """mgmt_ip может быть IPv6 даже когда основной IPv4 — это два независимых поля."""
        m = ServerCreate(**{**_BASE_CREATE, "mgmt_ip_address": "fe80::1"})
        assert isinstance(m.mgmt_ip_address, IPv6Address)


class TestServerCreateSshPort:
    @pytest.mark.parametrize("port", [1, 22, 65535])
    def test_valid_port_accepted(self, port: int):
        m = ServerCreate(**{**_BASE_CREATE, "ssh_port": port})
        assert m.ssh_port == port

    @pytest.mark.parametrize("port", [0, -1, 65536, 100000])
    def test_out_of_range_port_rejected(self, port: int):
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, "ssh_port": port})


class TestServerCreateNonNegativeCounts:
    @pytest.mark.parametrize("field", ["cpu_cores", "cpu_threads", "ram_total_mb"])
    def test_negative_rejected(self, field: str):
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, field: -1})

    @pytest.mark.parametrize("field", ["cpu_cores", "cpu_threads", "ram_total_mb"])
    def test_zero_accepted(self, field: str):
        """ge=0 — допустим, например, для свежесозданного без инвентаря сервера."""
        m = ServerCreate(**{**_BASE_CREATE, field: 0})
        assert getattr(m, field) == 0

    def test_cpu_frequency_ghz_negative_rejected(self):
        with pytest.raises(ValidationError):
            ServerCreate(**{**_BASE_CREATE, "cpu_frequency_ghz": -0.1})

    def test_cpu_frequency_ghz_accepts_float(self):
        m = ServerCreate(**{**_BASE_CREATE, "cpu_frequency_ghz": 3.2})
        assert m.cpu_frequency_ghz == 3.2


class TestServerCreateRequiredFields:
    @pytest.mark.parametrize("field", ["hostname", "ip_address", "department_id"])
    def test_required_field_missing(self, field: str):
        payload = dict(_BASE_CREATE)
        del payload[field]
        with pytest.raises(ValidationError):
            ServerCreate(**payload)


# ── ServerUpdate ──────────────────────────────────────────────────────────────

class TestServerUpdate:
    def test_empty_update_is_valid(self):
        """PATCH без полей — допустим, экспортируется как пустой diff."""
        m = ServerUpdate()
        assert m.model_dump(exclude_unset=True) == {}

    def test_partial_update_only_includes_set_fields(self):
        m = ServerUpdate(display_name="renamed")
        diff = m.model_dump(exclude_unset=True)
        assert diff == {"display_name": "renamed"}

    def test_update_ssh_port_bounds_enforced(self):
        with pytest.raises(ValidationError):
            ServerUpdate(ssh_port=0)
        with pytest.raises(ValidationError):
            ServerUpdate(ssh_port=65536)

    def test_update_cpu_cores_non_negative(self):
        with pytest.raises(ValidationError):
            ServerUpdate(cpu_cores=-1)

    def test_update_cpu_threads_non_negative(self):
        with pytest.raises(ValidationError):
            ServerUpdate(cpu_threads=-1)

    def test_update_cpu_frequency_ghz_non_negative(self):
        with pytest.raises(ValidationError):
            ServerUpdate(cpu_frequency_ghz=-0.1)

    def test_update_cpu_fields_partial(self):
        """PATCH принимает любую комбинацию CPU-полей по отдельности."""
        m = ServerUpdate(cpu_brand="AMD", cpu_model="EPYC 7763", cpu_cores=64)
        diff = m.model_dump(exclude_unset=True)
        assert diff == {
            "cpu_brand": "AMD",
            "cpu_model": "EPYC 7763",
            "cpu_cores": 64,
        }

    def test_update_ip_parses(self):
        m = ServerUpdate(ip_address="192.168.1.1")
        assert isinstance(m.ip_address, IPv4Address)
        m6 = ServerUpdate(ip_address="2001:db8::42")
        assert isinstance(m6.ip_address, IPv6Address)
