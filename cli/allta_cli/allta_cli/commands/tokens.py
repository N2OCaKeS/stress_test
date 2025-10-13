from __future__ import annotations
import json
from typing import Optional

from allta_cli.utils.config_api import tokens as fetch_tokens, ConfigApiError, TokenKeyNotFound
from allta_cli.utils.auth import AuthError, TokenExpiredError, NotAuthenticatedError

SEP = "─" * 60

def tokens_cmd(token_type: Optional[str] = None) -> int:
    """
    Печатает tokens.json:
      - если token_type None — выводит весь объект с понятными разделителями;
      - если token_type задан — печатает только значение этого ключа, а также какой ключ был выведен.
    Возвращает код завершения (0/1).
    """
    try:
        data = fetch_tokens(token_type)
    except (TokenKeyNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        print(f"Ошибка: {e}")
        return 1

    if token_type:
        print(SEP)
        print(f"tokens.json • один ключ")
        print(SEP)
        print(f"Ключ: {token_type}")
        print("Значение:")
        # data здесь строка токена
        print(data)
        print(SEP)
        return 0

    # иначе печатаем все токены (data — dict)
    print(SEP)
    print("tokens.json • все токены (начало)")
    print(SEP)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(SEP)
    print("tokens.json • все токены (конец)")
    print(SEP)
    return 0
