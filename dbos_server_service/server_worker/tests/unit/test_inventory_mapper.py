"""Unit-тесты `services/ssh_client.py::inventory_facts_to_payload`.

Маппер парсит raw `SshClient.get_inventory()` (вложенный dict с lscpu/lsblk
JSON и /etc/os-release parse) в flat-schema `InventoryCallbackRequest`
server_service'а. Тесты покрывают:

* happy-path с полным набором facts (включая cpu_brand/threads/frequency);
* частично-битые блоки (одна команда упала — остальные проходят);
* edge-cases для size-parser'а (G/T/M/bytes) и os-release;
* нормализацию vendor (`GenuineIntel` → `Intel`, `AuthenticAMD` → `AMD`,
  MCST/Elbrus → `MCST`).
"""

from __future__ import annotations

from src.services.ssh_client import (
    _detect_astra_mode,
    _extract_repositories,
    _normalize_cpu_vendor,
    _parse_size_to_gb,
    inventory_facts_to_payload,
)


_FULL_FACTS = {
    "hostname": {"stdout": "srv-01", "stderr": "", "returncode": 0},
    "kernel": {"stdout": "Linux srv-01 5.15.0-91-generic ...", "returncode": 0},
    "cpu": {"data": {"lscpu": [
        {"field": "Architecture:", "data": "x86_64"},
        {"field": "Vendor ID:", "data": "GenuineIntel"},
        {"field": "Model name:", "data": "Intel(R) Xeon Gold 6248"},
        {"field": "CPU(s):", "data": "40"},
        {"field": "Thread(s) per core:", "data": "2"},
        {"field": "CPU max MHz:", "data": "3900.0000"},
    ]}},
    "disks": {"data": {"blockdevices": [
        {"name": "sda", "size": "500G", "type": "disk", "model": "Samsung", "serial": "S1"},
        {"name": "sda1", "size": "499G", "type": "part"},  # partition — отфильтровать
        {"name": "nvme0n1", "size": "1.8T", "type": "disk", "model": "WD", "serial": "N1"},
    ]}},
    "os": {
        "NAME": "Astra Linux",
        "VERSION_ID": "1.7",
        "PRETTY_NAME": "Astra Linux SE 1.7",
    },
    "pci": {"devices": [
        '00:00.0 "Host bridge" "Intel"',
        '01:00.0 "Network controller" "Mellanox"',
    ]},
}


class TestInventoryMapperHappyPath:
    def test_maps_full_facts_to_flat_schema(self):
        payload = inventory_facts_to_payload(_FULL_FACTS)
        assert payload["hostname"] == "srv-01"
        assert payload["kernel"].startswith("Linux srv-01")
        assert payload["cpu_brand"] == "Intel"
        assert payload["cpu_model"] == "Intel(R) Xeon Gold 6248"
        assert payload["cpu_cores"] == 40
        assert payload["cpu_threads"] == 80  # 40 cores * 2 threads/core
        assert payload["cpu_frequency_ghz"] == 3.9
        assert payload["os_version"] == "Astra Linux SE 1.7"
        assert payload["lspci"] == (
            '00:00.0 "Host bridge" "Intel"\n01:00.0 "Network controller" "Mellanox"'
        )
        assert len(payload["disks"]) == 2  # sda1 partition отфильтрован
        assert payload["disks"][0]["name"] == "sda"
        assert payload["disks"][0]["size_gb"] == 500
        assert payload["disks"][0]["model"] == "Samsung"
        assert payload["disks"][0]["device_path"] == "/dev/sda"
        assert payload["disks"][1]["size_gb"] == 1843  # 1.8 * 1024

    def test_required_fields_always_present(self):
        """Схема server_service требует non-empty hostname/kernel/cpu_cores/os_version.

        cpu_brand/cpu_model/cpu_threads/cpu_frequency_ghz могут быть None.
        """
        payload = inventory_facts_to_payload(_FULL_FACTS)
        for required in ("hostname", "kernel", "cpu_cores", "os_version"):
            assert payload[required], f"required field {required} empty"


class TestInventoryMapperPartialFacts:
    def test_missing_cpu_block_returns_placeholder(self):
        facts = dict(_FULL_FACTS)
        facts.pop("cpu")
        payload = inventory_facts_to_payload(facts)
        assert payload["cpu_brand"] is None
        assert payload["cpu_model"] is None
        assert payload["cpu_cores"] == 1
        assert payload["cpu_threads"] is None
        assert payload["cpu_frequency_ghz"] is None

    def test_cpu_block_with_error_falls_through(self):
        """Если lscpu вернул error (rc!=0), `data` отсутствует → placeholder."""
        facts = dict(_FULL_FACTS)
        facts["cpu"] = {"error": "exit code 1", "returncode": 1}
        payload = inventory_facts_to_payload(facts)
        assert payload["cpu_brand"] is None
        assert payload["cpu_model"] is None
        assert payload["cpu_cores"] == 1

    def test_cpu_only_model_no_vendor_no_freq(self):
        """Если в lscpu есть только Model name — остальные поля None."""
        facts = dict(_FULL_FACTS)
        facts["cpu"] = {"data": {"lscpu": [
            {"field": "Model name:", "data": "Elbrus 8C"},
            {"field": "CPU(s):", "data": "8"},
        ]}}
        payload = inventory_facts_to_payload(facts)
        assert payload["cpu_brand"] is None
        assert payload["cpu_model"] == "Elbrus 8C"
        assert payload["cpu_cores"] == 8
        assert payload["cpu_threads"] is None
        assert payload["cpu_frequency_ghz"] is None

    def test_missing_disks_block_returns_empty_list(self):
        facts = dict(_FULL_FACTS)
        facts.pop("disks")
        payload = inventory_facts_to_payload(facts)
        assert payload["disks"] == []

    def test_disks_error_returns_empty_list(self):
        facts = dict(_FULL_FACTS)
        facts["disks"] = {"error": "no permission", "returncode": 1}
        payload = inventory_facts_to_payload(facts)
        assert payload["disks"] == []

    def test_missing_os_block_returns_unknown(self):
        facts = dict(_FULL_FACTS)
        facts["os"] = {}
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "unknown"

    def test_os_without_pretty_name_uses_name_version(self):
        facts = dict(_FULL_FACTS)
        facts["os"] = {"NAME": "Debian", "VERSION_ID": "12"}
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "Debian 12"

    def test_missing_pci_block_returns_none(self):
        facts = dict(_FULL_FACTS)
        facts.pop("pci")
        payload = inventory_facts_to_payload(facts)
        assert payload["lspci"] is None

    def test_missing_hostname_block_returns_unknown(self):
        facts = dict(_FULL_FACTS)
        facts.pop("hostname")
        payload = inventory_facts_to_payload(facts)
        assert payload["hostname"] == "unknown"


class TestSizeParser:
    def test_gigabytes(self):
        assert _parse_size_to_gb("500G") == 500

    def test_terabytes(self):
        assert _parse_size_to_gb("2T") == 2048

    def test_megabytes_rounded_down(self):
        # 512M ≈ 0.5G → 0 (int truncation)
        assert _parse_size_to_gb("512M") == 0

    def test_fractional_terabytes(self):
        assert _parse_size_to_gb("1.5T") == 1536

    def test_empty_string_zero(self):
        assert _parse_size_to_gb("") == 0

    def test_garbage_zero(self):
        assert _parse_size_to_gb("garbage") == 0

    def test_bytes_raw(self):
        # Без суффикса — считаем bytes; 1GB == 1073741824 → 1
        assert _parse_size_to_gb(str(1024 ** 3)) == 1


class TestCpuVendorNormalization:
    def test_genuine_intel_to_intel(self):
        assert _normalize_cpu_vendor("GenuineIntel") == "Intel"

    def test_authentic_amd_to_amd(self):
        assert _normalize_cpu_vendor("AuthenticAMD") == "AMD"

    def test_mcst_passthrough(self):
        assert _normalize_cpu_vendor("MCST") == "MCST"

    def test_elbrus_to_mcst(self):
        assert _normalize_cpu_vendor("Elbrus") == "MCST"

    def test_empty_returns_none(self):
        assert _normalize_cpu_vendor("") is None
        assert _normalize_cpu_vendor("   ") is None

    def test_unknown_vendor_passthrough(self):
        assert _normalize_cpu_vendor("Loongson") == "Loongson"


# ── Astra os_version / os_security_mode ──────────────────────────────────────


def _astra_facts(build=None, license_text=None, apt=None):
    """`_FULL_FACTS` c Astra-блоками (build_version / astra_license / apt)."""
    facts = dict(_FULL_FACTS)
    if build is not None:
        facts["astra_build"] = {"stdout": build, "returncode": 0}
    if license_text is not None:
        facts["astra_license"] = {"stdout": license_text, "returncode": 0}
    if apt is not None:
        facts["apt_sources"] = {"stdout": apt, "returncode": 0}
    return facts


class TestAstraOsVersion:
    def test_build_version_wins_over_pretty_name(self):
        facts = _astra_facts(build="1.7.5\n")
        payload = inventory_facts_to_payload(facts)
        # os_version — только версия сборки, без «Astra Linux SE» и режима.
        assert payload["os_version"] == "1.7.5"

    def test_security_mode_from_license_smolensk(self):
        facts = _astra_facts(
            build="1.7.5", license_text="Лицензия ... режим Смоленск ...",
        )
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "1.7.5"
        assert payload["os_security_mode"] == "Smolensk"

    def test_security_mode_voronezh(self):
        facts = _astra_facts(build="1.8.1.6", license_text="... Воронеж ...")
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "1.8.1.6"
        assert payload["os_security_mode"] == "Voronezh"

    def test_security_mode_orel(self):
        facts = _astra_facts(build="1.7.0", license_text="... Орёл ...")
        assert inventory_facts_to_payload(facts)["os_security_mode"] == "Orel"

    def test_build_without_recognized_mode_leaves_mode_none(self):
        facts = _astra_facts(build="1.7.5", license_text="нечитаемая лицензия")
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "1.7.5"
        assert payload["os_security_mode"] is None

    def test_non_astra_falls_back_to_pretty_name(self):
        # _FULL_FACTS без astra_build → PRETTY_NAME, режим None.
        payload = inventory_facts_to_payload(_FULL_FACTS)
        assert payload["os_version"] == "Astra Linux SE 1.7"
        assert payload["os_security_mode"] is None

    def test_missing_astra_build_error_block_uses_fallback(self):
        """build_version отсутствует (cat rc!=0) → fallback на os-release."""
        facts = dict(_FULL_FACTS)
        facts["astra_build"] = {"error": "No such file", "returncode": 1, "stdout": ""}
        facts["astra_license"] = {"error": "No such file", "returncode": 1, "stdout": ""}
        payload = inventory_facts_to_payload(facts)
        assert payload["os_version"] == "Astra Linux SE 1.7"
        assert payload["os_security_mode"] is None


class TestDetectAstraMode:
    def test_latin_and_cyrillic(self):
        assert _detect_astra_mode("smolensk edition") == "astra_smolensk"
        assert _detect_astra_mode("... Смоленск ...") == "astra_smolensk"
        assert _detect_astra_mode("Voronezh") == "astra_voronezh"
        assert _detect_astra_mode("орел") == "astra_orel"
        assert _detect_astra_mode("Орёл") == "astra_orel"

    def test_unknown_and_empty(self):
        assert _detect_astra_mode("") is None
        assert _detect_astra_mode("some other os") is None


class TestExtractRepositories:
    def test_only_active_deb_lines(self):
        text = (
            "# комментарий\n"
            "deb http://dl.astralinux.ru/ smolensk main\n"
            "\n"
            "#deb http://old/ off main\n"
            "deb-src http://dl.astralinux.ru/ smolensk main\n"
            "  deb [arch=amd64] http://extra/ stable main  \n"
        )
        repos = _extract_repositories({"stdout": text, "returncode": 0})
        assert repos == [
            "deb http://dl.astralinux.ru/ smolensk main",
            "deb-src http://dl.astralinux.ru/ smolensk main",
            "deb [arch=amd64] http://extra/ stable main",
        ]

    def test_missing_block_returns_empty(self):
        assert _extract_repositories(None) == []
        assert _extract_repositories({"error": "no file", "returncode": 1}) == []

    def test_payload_includes_repositories(self):
        facts = _astra_facts(
            build="1.7.5",
            apt="deb http://dl.astralinux.ru/ smolensk main\n#off\n",
        )
        payload = inventory_facts_to_payload(facts)
        assert payload["repositories"] == [
            "deb http://dl.astralinux.ru/ smolensk main",
        ]
