"""Unit-тесты ``utils.normalization.normalize_service_name``.

Покрытие: NFKC-фолд, удаление невидимых символов, confusable-маппинг,
trim whitespace, lower-case, защитные ошибки на не-str вход.

Связь с security: см. ``test_ingest.py::TestIngestReservedServiceUnicodeBypass``
и ``test_retention_protection.py::TestLogingServiceProtectionUnicodeBypass``
для end-to-end проверок через HTTP/DB.
"""

import pytest

from src.utils.normalization import normalize_service_name


class TestCanonicalForm:
    def test_pure_ascii_unchanged(self):
        assert normalize_service_name("loging_service") == "loging_service"

    def test_upper_case_lowered(self):
        assert normalize_service_name("LOGING_SERVICE") == "loging_service"

    def test_mixed_case_lowered(self):
        assert normalize_service_name("LoGiNg_SeRvIcE") == "loging_service"

    def test_ascii_whitespace_trimmed(self):
        assert normalize_service_name("  loging_service  ") == "loging_service"
        assert normalize_service_name("\tloging_service\n") == "loging_service"


class TestInvisibleChars:
    def test_zero_width_space_trailing(self):
        # 'loging_service' + U+200B
        assert normalize_service_name("loging_service​") == "loging_service"

    def test_zero_width_space_leading(self):
        assert normalize_service_name("​loging_service") == "loging_service"

    def test_zero_width_non_joiner(self):
        assert normalize_service_name("loging_‌service") == "loging_service"

    def test_zero_width_joiner(self):
        assert normalize_service_name("loging_‍service") == "loging_service"

    def test_word_joiner(self):
        assert normalize_service_name("loging_⁠service") == "loging_service"

    def test_byte_order_mark(self):
        assert normalize_service_name("﻿loging_service") == "loging_service"
        assert normalize_service_name("loging_service﻿") == "loging_service"

    def test_soft_hyphen(self):
        assert normalize_service_name("loging­_service") == "loging_service"

    def test_multiple_invisibles(self):
        # ZWSP + BOM + ZWJ — все должны схлопнуться
        assert normalize_service_name("​﻿loging_‍service") == "loging_service"


class TestNFKC:
    def test_fullwidth_letters(self):
        # ｌｏｇｉｎｇ_ｓｅｒｖｉｃｅ → loging_service
        assert (
            normalize_service_name("ｌｏｇｉｎｇ_ｓｅｒｖｉｃｅ")
            == "loging_service"
        )

    def test_fullwidth_underscore_preserved(self):
        # NFKC: U+FF3F (full-width low line) → ASCII '_'
        assert normalize_service_name("loging＿service") == "loging_service"

    def test_nbsp_collapses_to_space_then_stripped(self):
        #   NBSP → ASCII space after NFKC, .strip() убирает
        assert normalize_service_name(" loging_service ") == "loging_service"


class TestConfusables:
    def test_cyrillic_lowercase_homoglyphs(self):
        # 'l' (lat) + cyrillic 'о' + 'g' + 'i' + 'n' + 'g' + '_' + 's' + 'e' + 'r' + 'v' + 'i' + 'c' + 'e'
        assert normalize_service_name("lоging_service") == "loging_service"

    def test_cyrillic_uppercase_homoglyphs(self):
        # LОGING (cyrillic О) → loging
        assert normalize_service_name("LОGING_SERVICE") == "loging_service"

    def test_greek_omicron(self):
        assert normalize_service_name("lοging_service") == "loging_service"

    def test_multiple_cyrillic_chars(self):
        # 'loging_serviсе' с двумя кириллическими 'с' и 'е'
        assert normalize_service_name("loging_serviсе") == "loging_service"

    def test_other_service_names_with_confusables(self):
        # Кириллическое 'а' и 'с'
        assert normalize_service_name("аuth_serviсe") == "auth_service"

    def test_ipa_script_g(self):
        # U+0261 LATIN SMALL LETTER SCRIPT G — выглядит как ASCII 'g',
        # NFKC не сворачивает; маппится в `g` нашей таблицей.
        assert normalize_service_name("loɡing_service") == "loging_service"

    def test_ipa_dotless_i(self):
        # U+0131 LATIN SMALL LETTER DOTLESS I → 'i'.
        assert normalize_service_name("logıng_service") == "loging_service"

    def test_latin_eng(self):
        # U+014B LATIN SMALL LETTER ENG → 'n'.
        # `logi` + ŋ + `g_service` → `login` + `g_service` = `loging_service`.
        assert normalize_service_name("logiŋg_service") == "loging_service"


class TestCombined:
    def test_unicode_plus_case_plus_invisible(self):
        # Капс + кириллическая О + ZWSP в конце
        assert normalize_service_name("LОGING_SERVICE​") == "loging_service"

    def test_whitespace_plus_invisible_plus_confusable(self):
        assert normalize_service_name("  lоging_service​  ") == "loging_service"


class TestDefensive:
    def test_non_str_raises(self):
        with pytest.raises(TypeError):
            normalize_service_name(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            normalize_service_name(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            normalize_service_name(b"loging_service")  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self):
        assert normalize_service_name("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_service_name("   ") == ""
        assert normalize_service_name("\t\n​") == ""


class TestNonReservedUnaffected:
    """Регрессия: легитимные имена не ломаются нормализацией."""

    def test_legitimate_services(self):
        for svc in (
            "auth_service",
            "server_service",
            "config_service",
            "server_worker",
            "logging_service",  # с двумя 'g' — НЕ reserved
        ):
            assert normalize_service_name(svc) == svc

    def test_legitimate_with_padding(self):
        assert normalize_service_name("  auth_service  ") == "auth_service"

    def test_legitimate_with_case(self):
        assert normalize_service_name("AUTH_SERVICE") == "auth_service"
