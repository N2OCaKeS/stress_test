"""Unicode-safe нормализация для security-чувствительных string-входов.

Используется на ingest-пути событий аудита, чтобы reserved-service-name
guard нельзя было обойти через Unicode-confusables (кириллическая `о` vs.
латинская `o`) или невидимые code points (zero-width space, BOM, joiners),
которые иначе проскочили бы наивное `.strip().lower()` сравнение — и
атакующий минтил бы события под именем `loging_service` (события, которые
retention-sweeper отказывается удалять).

Threat model: держатель утёкшего `SERVICE_API_KEY` шлёт
`service="loging_service​"` (каноническое имя + U+200B zero-width space)
или `service="lоging_service"` (кириллическая `о`). С наивным
`payload.service.strip().lower() == "loging_service"` row попадает в
`audit_events`, и retention WHERE-clause `service != 'loging_service'`
его тоже не видит — событие переживает все sweep'ы,
открывая путь к silent erasure audit-trail'а (минтуем кучу fake-loging
событий, чтобы замаскировать настоящие admin-действия, а потом ждём, пока
retention выметет настоящие).

`normalize_identifier` сворачивает invisibles, применяет NFKC и
маппит curated set homoglyph-confusables в ASCII-эквиваленты ДО
case-insensitive сравнения. Имя обобщённое: функция применяется не только
к `service`, но и к `action` (`GET /events?action=...`), `X-Service-Identity`
и path-параметру `/services/{service}/event_definitions`.

Алиас `normalize_service_name` оставлен для backward-compat — несколько
модулей и тестов исторически импортируют именно это имя.
"""

from __future__ import annotations

import re
import unicodedata

# Invisible / formatting code points, которые сносим явно. NFKC сворачивает
# многие из них, но горстка (особенно ZWSP U+200B, ZWNJ U+200C, ZWJ U+200D,
# WORD JOINER U+2060, BOM U+FEFF, SHY U+00AD) выживают NFKC — у них нет
# canonical/compatibility decomposition. Удаляем их явно до lower/strip,
# чтобы, например, `"loging_service​"` нормализовалось в
# `"loging_service"` и попалось reserved-гарду.
_INVISIBLE_CHARS_RE = re.compile(
    "["
    "­"  # SOFT HYPHEN
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "⁠"  # WORD JOINER
    "﻿"  # ZERO WIDTH NO-BREAK SPACE / BOM
    "]"
)

# Кириллические / греческие / fullwidth буквы, выглядящие в распространённых
# шрифтах идентично ASCII. Curated subset UTS #39 "Confusables.txt",
# ограниченный символами, выживающими NFKC. Map применяется ПОСЛЕ NFKC, но
# ДО lower-кейса, чтобы оба варианта регистра свернулись.
#
# Покрытие: каждая ASCII-буква, встречающаяся в любом reserved service name
# (сейчас только `loging_service`: l, o, g, i, n, s, e, r, v, c) плюс
# остальные [a-z] на forward-compat. Numeric look-alikes (кириллическая `о`
# это *не* цифра, а вот греческая `ο` — да) тоже сворачиваются.
_CONFUSABLES_MAP = {
    # Кириллица (lower & upper).
    "а": "a", "А": "A",  # а / А
    "е": "e", "Е": "E",  # е / Е
    "о": "o", "О": "O",  # о / О
    "р": "p", "Р": "P",  # р / Р
    "с": "c", "С": "C",  # с / С
    "у": "y", "У": "Y",  # у / У
    "х": "x", "Х": "X",  # х / Х
    "і": "i", "І": "I",  # і / І
    "ј": "j", "Ј": "J",  # ј / Ј
    "ӏ": "l",                 # ӏ
    "ԛ": "q", "Ԛ": "Q",  # ԛ / Ԛ
    "ѕ": "s", "Ѕ": "S",  # ѕ / Ѕ
    "в": "v", "В": "V",  # в (в шрифтах похожа на Latin B/V; считаем `v`)
    "һ": "h",                 # һ
    # Греческий нижний регистр (homoglyphs, выживающие NFKC).
    "ο": "o", "Ο": "O",  # ο / Ο
    "α": "a", "Α": "A",  # α / Α
    "ε": "e", "Ε": "E",  # ε / Ε
    "ι": "i", "Ι": "I",  # ι / Ι
    "υ": "y", "Υ": "Y",  # υ / Υ
    "ρ": "p", "Ρ": "P",  # ρ / Ρ
    "ν": "v", "Ν": "N",  # ν / Ν (ν выглядит как Latin v)
    "κ": "k", "Κ": "K",  # κ / Κ
    "χ": "x", "Χ": "X",  # χ / Χ
    "η": "n", "Η": "H",  # η / Η
    "μ": "u",                 # μ → u-look
    "τ": "t", "Τ": "T",  # τ / Τ
    "β": "b", "Β": "B",  # β / Β
    "ζ": "z", "Ζ": "Z",  # ζ / Ζ
    # IPA / расширенная латиница — выглядят как ASCII в большинстве шрифтов,
    # NFKC их не сворачивает (нет compatibility-decomposition).
    "ɡ": "g",                  # U+0261 LATIN SMALL LETTER SCRIPT G
    "ı": "i",                  # U+0131 LATIN SMALL LETTER DOTLESS I
    "ŋ": "n",                  # U+014B LATIN SMALL LETTER ENG
}

_CONFUSABLES_TRANSLATE = str.maketrans(_CONFUSABLES_MAP)


def normalize_identifier_preserve_case(value: str) -> str:
    """Security-нормализация без кейсфолда — для charset-проверки до lower().

    Делает всё, что и `normalize_identifier`, кроме финального
    `str.lower()`. Нужно schema-валидатору `service`: charset
    `[a-z_]{1,64}` обязан срабатывать на `"ABC"` (uppercase не по
    snake_case-конвенции имён сервисов). Если случало lower раньше
    charset'а, `"ABC"` свернулось бы в `"abc"` и тихо прошло.

    Шаги:
      1. NFKC compatibility decomp/recomp (full-width → ASCII, лигатуры).
      2. Удалить invisible/zero-width (ZWSP, ZWJ, BOM, SHY и т.п.) —
         выживают NFKC, без явного strip'а ломают reserved-guard.
      3. Confusable homoglyph fold (кир. `о` → ASCII `o`, греч. `ο` → `o`).
      4. Strip пробельных по краям.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"normalize_identifier_preserve_case expects str, got {type(value).__name__}"
        )

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVISIBLE_CHARS_RE.sub("", normalized)
    normalized = normalized.translate(_CONFUSABLES_TRANSLATE)
    normalized = normalized.strip()
    return normalized


def normalize_identifier(value: str) -> str:
    """Возвращает каноническую lowercase-форму идентификатора (service / action / …).

    Шаги (порядок важен):
      1. NFKC normalization — сворачивает compatibility-символы (full-width
         `ｌ` → ASCII `l`, лигатуры и т.п.).
      2. Удаляем явные invisible / zero-width code points, которые NFKC
         оставил (ZWSP, ZWJ, BOM, SHY, …).
      3. Применяем confusable-homoglyph fold (кириллическое `о` → ASCII
         `o`, греческое `ο` → ASCII `o`, …). См. `_CONFUSABLES_MAP`.
      4. `.strip()` пробелов по краям (NFKC уже свернул NBSP в ASCII-space,
         так что обычный strip ловит).
      5. ASCII-кейсфолд через `str.lower`.

    Защитно к не-`str` входу (рейзит `TypeError`) — caller'ы могут
    полагаться на schema-уровневую type-проверку, но misuse падает громко,
    а не молча обходит гард.

    Совместимо с точкой и snake_case в `action` (`user.login_success`):
    маппинг трогает только буквы, разделители не задевает.
    """
    return normalize_identifier_preserve_case(value).lower()


# Backward-compat алиасы. Старое имя `normalize_service_name` исторически
# использовалось в десятке мест (схемы, эндпоинты, тесты); функция всегда
# применялась и к action, и к identity-header'у, не только к service-name.
# Новое имя `normalize_identifier` точнее описывает скоуп; оставляем старое
# доступным, чтобы не править весь репо разом.
normalize_service_name = normalize_identifier
normalize_service_name_preserve_case = normalize_identifier_preserve_case
