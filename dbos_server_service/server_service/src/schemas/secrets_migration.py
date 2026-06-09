"""Схемы для internal-эндпоинтов `/secrets/...` — постепенная фоновая ротация."""

from pydantic import BaseModel, Field


class OutboxStatusSnapshot(BaseModel):
    """Слепок outbox-таблицы для observability'ы."""

    pending: int = Field(0, ge=0, description="Сколько row'ов ждут claim'а.")
    processing: int = Field(0, ge=0, description="Сколько в полёте (claim'нуто).")
    done: int = Field(0, ge=0, description="Закрыто успешно, ещё не cleanup'нуто.")
    failed: int = Field(
        0, ge=0, description="Финализировано с ошибкой; ждёт оператора или requeue."
    )


class ColumnMigrationBreakdown(BaseModel):
    """Per-column сводка по миграции одной зашифрованной колонки.

    `remaining_legacy` — сумма счётчиков по версиям, отличным от
    активной. Когда оператор хочет дропнуть `SERVER_ENCRYPTION_KEY__v<N>`,
    он смотрит на per-column `remaining_legacy=0`, чтобы убедиться, что
    конкретная колонка уже не зависит от старого ключа.
    """

    total: int = Field(0, ge=0, description="Всего non-NULL row'ов в этой колонке.")
    by_version: dict[int, int] = Field(
        default_factory=dict,
        description="`{N: count}` по wire-префиксам `v<N>$...` этой колонки.",
    )
    remaining_legacy: int = Field(
        0, ge=0,
        description=(
            "Сколько row'ов ещё не под активной версией"
            " (malformed-токены не учитываются)."
        ),
    )


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
    outbox: OutboxStatusSnapshot = Field(
        default_factory=OutboxStatusSnapshot,
        description=(
            "Состояние outbox-таблицы `secrets_reencrypt_outbox`. Worker"
            " ориентируется на `pending` > 0 как сигнал «нужно работать»;"
            " оператор смотрит на `failed` для разбора инцидентов."
        ),
    )
    # ── Extended per-column / progress fields ───────────────────────────────
    server_account_password_encrypted: ColumnMigrationBreakdown = Field(
        default_factory=ColumnMigrationBreakdown,
        description=(
            "Per-column сводка для `server_accounts.password_encrypted` —"
            " оператор смотрит, какая именно колонка тащит legacy."
        ),
    )
    ipmi_controller_password_encrypted: ColumnMigrationBreakdown = Field(
        default_factory=ColumnMigrationBreakdown,
        description=(
            "Per-column сводка для `ipmi_controllers.password_encrypted` —"
            " симметрично server_account-полю выше."
        ),
    )
    remaining_legacy_total: int = Field(
        0, ge=0,
        description=(
            "Суммарный `remaining_legacy` по обеим колонкам (дублирует"
            " `remaining`, но семантически — для lazy-страницы)."
        ),
    )
    migrated_pct: float = Field(
        100.0, ge=0.0, le=100.0,
        description=(
            "Доля row'ов под активной версией, 0..100 (округление до"
            " десятых). При `total=0` отдаётся 100.0."
        ),
    )
    outbox_pending: int = Field(
        0, ge=0,
        description=(
            "Синоним `outbox.pending` на верхнем уровне — для caller'ов"
            " с короткой read-only-страницы."
        ),
    )


class ReencryptBatchResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_batch (legacy sync-путь)."""

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


# ── Outbox endpoints ────────────────────────────────────────────────────────


class SeedOutboxResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_outbox/seed."""

    inserted: int = Field(
        ..., ge=0, description="Сколько новых outbox-row'ов создано."
    )
    scanned: int = Field(
        ...,
        ge=0,
        description=(
            "Сколько owner-row'ов попало в SELECT-кандидатов в этом проходе"
            " (включая те, что уже представлены в outbox)."
        ),
    )
    active_version: int = Field(
        ..., ge=1, description="Активная версия ключа на момент seed'а."
    )


class OutboxItem(BaseModel):
    """Одна строка claim-ответа — то, что worker получает на обработку."""

    id: str = Field(..., description="`rox_<uuid>` — outbox row id.")
    entity_type: str = Field(
        ..., description="`server_account` | `ipmi_controller`."
    )
    entity_id: str = Field(..., description="ID owner-row'а в исходной таблице.")
    legacy_ciphertext: str = Field(
        ...,
        description=(
            "Wire-token `v<N>$...` на момент seed'а. Если кто-то параллельно"
            " ротировал пароль на этой строке, finalize_done пройдёт"
            " идемпотентно — owner-row не перепишется."
        ),
    )
    attempts: int = Field(
        ..., ge=1, description="Какая по счёту попытка обработки этой строки."
    )


class OutboxClaimResponse(BaseModel):
    """Ответ GET /internal/secrets/reencrypt_outbox/pending."""

    items: list[OutboxItem] = Field(
        default_factory=list,
        description="Список claim'нутых row'ов; пустой если очередь иссякла.",
    )


class OutboxFinalizeDoneResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_outbox/{id}/done."""

    id: str = Field(..., description="Outbox row id.")
    status: str = Field(
        ...,
        description=(
            "`done` — нормально закрыто; `missing` — row не найден"
            " (вернулись 404 в endpoint); другие значения — row уже"
            " закрыт другой ветвью."
        ),
    )
    skipped: bool = Field(
        ...,
        description=(
            "True, если owner-row уже не содержит legacy ciphertext"
            " (ротация прошла параллельно). Outbox-row всё равно помечается"
            " `done` — работа фактически выполнена."
        ),
    )


class OutboxFinalizeFailedRequest(BaseModel):
    """Тело POST /internal/secrets/reencrypt_outbox/{id}/failed."""

    error: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description=(
            "Текст ошибки. Сохраняется в `last_error` без редактирования;"
            " caller должен сам не класть туда секреты."
        ),
    )


class OutboxFinalizeFailedResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_outbox/{id}/failed."""

    id: str
    status: str = Field(
        ...,
        description="`failed` — нормальный исход; `missing` если row уже нет.",
    )


class OutboxCleanupResponse(BaseModel):
    """Ответ POST /internal/secrets/reencrypt_outbox/cleanup."""

    deleted: int = Field(
        ..., ge=0, description="Сколько done-row'ов удалено в этом проходе."
    )
