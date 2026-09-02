"""Unit-тесты `src/tasks/_bmc_helpers.py::extract_bmc_host`.

Сохранение порта важно: per-host circuit breaker и Redfish-probe строят
ключи по строке, которую возвращает `extract_bmc_host`. Если два iDRAC
живут на одном IP, но разных портах, склейка их в один ключ откроет
breaker для обоих, а так же даст SSRF-like collision в кэшах probe.
"""

from __future__ import annotations

import pytest

from src.tasks._bmc_helpers import extract_bmc_host


class TestExtractBmcHost:
    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            # scheme + host без порта — никаких суффиксов
            ("https://10.0.0.1", "10.0.0.1"),
            ("http://10.0.0.1", "10.0.0.1"),
            # явный нестандартный порт — сохраняется
            ("https://10.0.0.1:8443", "10.0.0.1:8443"),
            ("http://10.0.0.1:8080", "10.0.0.1:8080"),
            # стандартные порты тоже сохраняются (per-host breaker должен
            # различать iDRAC'и, если они вдруг висят на 443 и 80 одновременно)
            ("https://10.0.0.1:443", "10.0.0.1:443"),
            ("http://10.0.0.1:80", "10.0.0.1:80"),
            # без scheme — host[:port] выезжает из split fallback'а
            ("10.0.0.1", "10.0.0.1"),
            ("10.0.0.1:623", "10.0.0.1:623"),
            # доменное имя
            ("https://bmc.example.com:8000", "bmc.example.com:8000"),
            # path после host[:port] игнорируется
            ("https://bmc.example.com:8000/redfish/v1/", "bmc.example.com:8000"),
            # IPv6 в скобках со scheme — netloc срабатывает
            ("https://[2001:db8::1]:443", "[2001:db8::1]:443"),
            ("https://[::1]:8443", "[::1]:8443"),
            ("https://[::1]", "[::1]"),
            # IPv6 без scheme — попадает на split-fallback, скобки сохраняются
            ("[2001:db8::1]:443", "[2001:db8::1]:443"),
            ("[::1]:623", "[::1]:623"),
            ("[::1]", "[::1]"),
        ],
    )
    def test_preserves_port(self, endpoint, expected):
        assert extract_bmc_host(endpoint) == expected

    def test_strips_userinfo(self):
        # basic-auth в URL'е не должен утечь в host-ключ breaker'а
        assert extract_bmc_host("https://admin:secret@10.0.0.1:443") == "10.0.0.1:443"
        assert extract_bmc_host("https://admin@10.0.0.1") == "10.0.0.1"
        # IPv6 с userinfo
        assert extract_bmc_host("https://user:pass@[2001:db8::1]:443") == "[2001:db8::1]:443"
        # bare IPv6 с userinfo (без scheme) — fallback тоже должен резать userinfo
        assert extract_bmc_host("admin@[::1]:623") == "[::1]:623"

    def test_empty_input(self):
        assert extract_bmc_host("") == ""

    def test_different_ports_yield_different_keys(self):
        """Регрессия: раньше `urlparse(...).hostname` склеивал разные iDRAC
        на одном IP в один breaker-ключ — авария на :8443 закрывала :443.
        """
        a = extract_bmc_host("https://10.0.0.1:443")
        b = extract_bmc_host("https://10.0.0.1:8443")
        c = extract_bmc_host("https://10.0.0.1")
        assert a != b
        assert a != c
        assert b != c
