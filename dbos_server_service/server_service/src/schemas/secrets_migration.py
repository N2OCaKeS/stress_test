"""Схемы для internal-эндпоинтов `/secrets/...` — постепенная фоновая ротация."""

from pydantic import BaseModel, Field


class MigrationStatusResponse(BaseModel):
    """Ответ GET /internal/secrets/migration_status."""

    remaining: int = Field(
        ...,
        ge=0,
        description=(
            "Сколько записей с password_encrypted ещё ждут перешифровки "
            "под активную версию ключа (сумма счётчиков по версиям, "
            "отличным от active_version; malformed записи не учитываются)."
        ),
    )
    total: int = Field(
        ...,
        ge=0,
        description="Всего записей с non-NULL password_encrypted (обе таблицы).",
    )
    active_version: int = Field(
        ...,
        ge=1,
        description="Текущая активная версия `SERVER_ENCRYPTION_KEY` (см. settings).",
    )
    by_version: dict[int, int] = Field(
        default_factory=dict,
        description="Разбивка `{version: count}` по wire-префиксам `v<N>$...`.",
    )
    app_env: str = Field(
        ...,
        description=(
            "APP_ENV server_service'а (`local`/`development`/`test`/`staging`/`production`). "
            "Worker сверяет со своим `app_env` перед запуском батч-ре-шифрации: "
            "несовпадение значит, что воркер указывает на чужое окружение и "
            "может переписать чужие секреты — тик должен быть пропущен."
        ),
    )


class ReencryptBatchResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_batch."""

    processed: int = Field(
        ...,
        ge=0,
        description="Сколько строк успешно перешифровано в этом батче.",
    )
    errors: int = Field(
        ...,
        ge=0,
        description=(
            "Сколько строк завершились ошибкой decrypt/encrypt и были пропущены "
            "(например, нет ключа для старой версии в env или повреждён ciphertext)."
        ),
    )
