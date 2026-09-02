"""Строгий декод base64-секрета на входе write-эндпоинтов credentials.

Reveal отдаёт plaintext-секрет в `secret_b64` через `base64.b64encode`.
Симметрично write-контракт принимает секрет в base64: клиент кодирует
`base64.b64encode(plaintext)`, мы декодируем на приёме и дальше работаем с
plaintext (политика длины, шифрование, хранение).
"""

import base64
import binascii


def decode_b64(value: str, field_name: str) -> str:
    """Декодировать base64-строку в UTF-8. Битый вход → ValueError (→ 422).

    Строгий режим (`validate=True`): любой не-base64 символ или неверный
    паддинг отбивается, а не молча игнорируется. После декода требуем
    валидный UTF-8 — секрет хранится и применяется как текст.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field_name} is not valid base64") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field_name} does not decode to UTF-8") from exc
