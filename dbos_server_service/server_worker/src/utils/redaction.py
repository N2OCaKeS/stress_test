"""Маскировка секретов в строках сообщений об ошибках.

Когда `_runner.run_task` ловит исключение из impl (idrac/ssh/HTTP-клиента),
текст ошибки идёт в три места:

  * `task.last_error` (worker-DB),
  * audit `details.error` (loging-DB),
  * stdout/логи воркера.

Реальные клиенты (`sushy`, `asyncssh`, `pyghmi`, `ipmitool`) часто включают
полные команды/URL в текст ошибки, например::

    https://root:Calvin@idrac.example/redfish/v1/Systems
    ipmitool -U admin -P plaintext lan print 1
    Auth failed for user 'root' with password 'hunter2'

Поэтому строку перед сохранением прогоняем через `redact_error_message`,
которая прячет известные формы секретов.

Дублирует логику `auth_service.services.redaction` /
`loging_service.utils.redaction`, но работает не с dict, а с произвольным
free-form текстом — поэтому реализация на регэкспах, а не на классификации
ключей. Плейсхолдеры взяты те же: `<PASSWORD>`, `<TOKEN>`, `<USER>`.
"""

from __future__ import annotations

import re

# ── Многострочный PEM-блок (private key) ─────────────────────────────────────
#
# `-----BEGIN ... PRIVATE KEY----- ... -----END ... PRIVATE KEY-----`. Если
# asyncssh/cryptography когда-нибудь вставит plaintext-ключа в repr исключения
# — без этого паттерна он попадёт в `task.last_error` и audit `details.error`.
# Сегодня asyncssh передаёт ключи через path, в repr не кладёт; правило —
# defence-in-depth. Также покрывает `BEGIN OPENSSH PRIVATE KEY`, `BEGIN RSA
# PRIVATE KEY`, `BEGIN EC PRIVATE KEY` и любые BEGIN…END с латинскими словами.
_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----",
)

# ── URL credentials: scheme://user:pass@host/... ─────────────────────────────
#
# Поддерживаем любой scheme (http/https/redfish/ssh/redis/postgres/...).
# `user` и `pass` могут содержать percent-encoded символы — берём всё до `@`,
# но не позволяем пробелам/слэшам, чтобы не съесть лишнее.
_URL_CREDS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)"
    r"(?P<user>[^\s:/@]+)"
    r":"
    r"(?P<password>[^\s/@]+)"
    r"@",
)

# ── Shell-style флаги: -U <user>, -P <password>, -u <user> ──────────────────
#
# Маскируем значение после флага. Не трогаем сам флаг — оператор должен видеть
# структуру команды. Покрывает `ipmitool -U admin -P plaintext` и слитное
# `-Psecret`.
#
# ВАЖНО: маскируется только UPPERCASE `-P` для пароля. ipmitool использует
# `-p` (lowercase) под номер порта BMC (623 по умолчанию для IPMI-over-LAN),
# а `-P` — под пароль. Маскировать `-p 623` бессмысленно и мешает оператору
# читать команду; перепутать лёгко (mysql, наоборот, кладёт пароль в `-p`,
# но мы для mysql ловим только key=value/quoted формы — `mysql -psecret`
# покрывается KV-веткой через `password=` синонимы где это возможно).
#
# Чтобы не съесть следующий флаг, значение ограничиваем символами, не
# являющимися пробелом.
_DASH_U_RE = re.compile(r"(?<![\w\-])(-[Uu])(\s+|=)(\S+)")
# Сепаратор `\s+|=` — обязательный. Старый вариант с пустым третьим
# членом давал false-positive на `-Path /foo` (matched `-P` + `ath`),
# который встречается в PowerShell-трейсах. Слитный `-Psecret`
# (короткая форма ipmitool) ловится отдельным паттерном ниже: после
# `-P` должна идти НЕ буква, иначе это другая опция вроде `-Path`.
_DASH_P_RE = re.compile(r"(?<![\w\-])(-P)(\s+|=)(\S+)")
_DASH_P_JOINED_RE = re.compile(r"(?<![\w\-])(-P)(?=[^A-Za-z\s=])(\S+)")

# ── key=value формы ──────────────────────────────────────────────────────────
#
# password=..., passwd=..., pwd=..., secret=..., token=..., api_key=..., и т.д.
# Значение — всё до первого whitespace/`&`/конца строки.
_KV_PASSWORD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|pass)\s*[=:]\s*"
    r"(?P<val>[^\s&,;]+)",
)

# `\b` не срабатывает на границе `_<key>`, потому что `_` — word-char.
# Из-за этого `service_api_key=val` или `db_secret=val` проскакивали
# без редакции. Меняем на негативный lookbehind по буквам: `_api_key`
# матчится (перед `_` нет буквы), `myapi_key` — нет (перед `_` буква).
_KV_SECRET_RE = re.compile(
    r"(?i)(?<![A-Za-z])(secret|secret_key|api_key|apikey|client_secret|"
    r"private_key|signing_key)\s*[=:]\s*"
    r"(?P<val>[^\s&,;]+)",
)
_KV_TOKEN_RE = re.compile(
    r"(?i)\b(token|access_token|refresh_token|id_token|oauth_token|"
    r"bearer|jwt|jwt_token|session_token)\s*[=:]\s*"
    r"(?P<val>[^\s&,;]+)",
)

# ── Bearer <token> ───────────────────────────────────────────────────────────
_BEARER_RE = re.compile(r"(?i)\bBearer\s+(?P<val>[A-Za-z0-9._\-]+)")

# ── Опаковые токены системы: dbos_pat_..., dbos_bot_..., pat_..., bot_... ────
#
# `pat_` / `bot_` без `dbos_` префикса — лишь намёк на токен; маскируем,
# когда виден характерный random-suffix. Канонические префиксы системы —
# `dbos_pat_` и `dbos_bot_`, но возможны и legacy-формы.
_OPAQUE_TOKEN_RE = re.compile(
    r"\b(?:dbos_)?(?:pat|bot)_[A-Za-z0-9_\-]{12,}\b",
)

# ── JWT-подобные строки (три base64-сегмента через `.`) ──────────────────────
_JWT_RE = re.compile(
    r"\b[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b",
)

# ── Цитированные пароли: password 'hunter2', password "hunter2" ──────────────
_QUOTED_PASSWORD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|pass)\s+(['\"])(?P<val>[^'\"]+)\2",
)

_MAX_LENGTH = 4096


def redact_error_message(msg: str) -> str:
    """Маскирует секреты во free-form тексте ошибки.

    Применяется к сообщениям, попадающим в `task.last_error` и audit
    `details.error`. Возвращает новую строку — вход не мутируется (str
    immutable). На вход допускается любая строка, в том числе пустая.

    Покрытие:
      * `-----BEGIN ... PRIVATE KEY-----` многострочный PEM-блок → `<PRIVATE_KEY>`
      * URL credentials → `<scheme>://<USER>:<PASSWORD>@host/...`
      * `-U user`, `-u user`, `-P pass` (с пробелом/`=`) и слитная форма
        `-P<non-letter><value>` (`-P!secret123`). `-Path /foo` НЕ
        матчится (после `-P` стоит буква). `-p` (lowercase) в ipmitool —
        это номер BMC-порта, его НЕ маскируем.
      * `password=...`, `secret=...`, `token=...` (и др. известные ключи)
      * `Bearer <token>` → `Bearer <TOKEN>`
      * `dbos_pat_*`, `dbos_bot_*`, `pat_*`, `bot_*` → `<TOKEN>`
      * JWT-подобные строки → `<TOKEN>`
      * `password 'hunter2'`, `password "hunter2"` → `password '<PASSWORD>'`

    Если строка длиннее `_MAX_LENGTH` — усекается и помечается
    `…<TRUNCATED>` (защита от blob'ов с base64-стектрейсами).
    """
    if not isinstance(msg, str) or not msg:
        return msg

    result = msg

    # 0. Многострочный PEM-блок — до URL/KV, иначе содержимое ключа может
    # содержать base64-сегменты, похожие на JWT, и съестся другим regex'ом.
    result = _PEM_BLOCK_RE.sub("<PRIVATE_KEY>", result)

    # 1. URL credentials — раньше всего, иначе password/token-regex могут
    # съесть часть URL.
    result = _URL_CREDS_RE.sub(
        lambda m: f"{m.group('scheme')}<USER>:<PASSWORD>@",
        result,
    )

    # 2. Shell-style флаги.
    result = _DASH_U_RE.sub(lambda m: f"{m.group(1)}{m.group(2) or ' '}<USER>", result)
    result = _DASH_P_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<PASSWORD>", result)
    result = _DASH_P_JOINED_RE.sub(lambda m: f"{m.group(1)}<PASSWORD>", result)

    # 3. Цитированные пароли (до key=value, чтобы кавычки не съели регэксп KV).
    result = _QUOTED_PASSWORD_RE.sub(
        lambda m: f"{m.group(1)} {m.group(2)}<PASSWORD>{m.group(2)}",
        result,
    )

    # 4. key=value формы.
    result = _KV_PASSWORD_RE.sub(lambda m: f"{m.group(1)}=<PASSWORD>", result)
    result = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<SECRET>", result)
    result = _KV_TOKEN_RE.sub(lambda m: f"{m.group(1)}=<TOKEN>", result)

    # 5. Bearer-токены.
    result = _BEARER_RE.sub("Bearer <TOKEN>", result)

    # 6. JWT — до OPAQUE, потому что pat_-токен может матчить часть JWT иначе.
    result = _JWT_RE.sub("<TOKEN>", result)

    # 7. Опаковые токены системы.
    result = _OPAQUE_TOKEN_RE.sub("<TOKEN>", result)

    # 8. Усечение длинных строк.
    if len(result) > _MAX_LENGTH:
        result = result[:_MAX_LENGTH] + "…<TRUNCATED>"

    return result
