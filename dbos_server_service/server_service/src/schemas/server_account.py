"""Pydantic-схемы для эндпоинтов /server-accounts.

Аккаунт может быть привязан сразу к нескольким серверам (`server_ids`) либо
не привязан ни к одному (тогда это просто хранимый креден).
Пароль — общий на все привязанные серверы. На write принимаем `password_b64`
опционально (если не задан — генерим серверной стороной) — клиент кодирует
plaintext через `base64.b64encode`, симметрично с reveal-картой, где пароль
отдаётся в `password_b64`. В GET-карточке `password_b64` отдаётся только
держателю action `view_password`; для остальных поле остаётся `None`.
"""

import re
from datetime import datetime

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.b64 import decode_b64
from src.core.password_policy import validate_password

# Логин OS-аккаунта: тот же паттерн, что в create и worker'ском `_LOGIN_RE`.
# Вынесен в константу, чтобы create / rename / recreate-схемы держали один
# источник истины.
_LOGIN_PATTERN = r"^[A-Za-z0-9._\-]+$"

# POSIX group name: начинается с lowercase / underscore, дальше цифры / `-`,
# общая длина 32 символа (login.defs default). Те же ограничения дублируются
# в `schemas/internal.py::InventoryUserItem.unix_groups` — расхождение между
# accept-from-API и accept-from-worker привело бы к рассинхрону reconcile.
_POSIX_GROUP_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _validate_unix_groups(value: list[str]) -> list[str]:
    for name in value:
        if len(name) > 32 or not _POSIX_GROUP_NAME_RE.match(name):
            raise ValueError(
                f"unix_groups: '{name}' не соответствует POSIX group name pattern"
            )
    return value


# Допустимые ssh-ключи для authorized_keys: префикс типа + base64-тело
# (+ опциональный комментарий). Финальную валидацию по факту делает воркер
# (`server_worker/.../ssh.py::_validate_ssh_public_key`) — здесь только
# защита от мусора и shell-метасимволов в одном поле перед записью в БД.
_SSH_PUBLIC_KEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9@.-]+) "
    r"[A-Za-z0-9+/]+=*( [^\r\n]*)?$"
)


def _validate_ssh_public_key(value: str) -> str:
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError("ssh_public_key: ключ должен быть одной строкой")
    if not _SSH_PUBLIC_KEY_RE.match(value):
        raise ValueError(
            "ssh_public_key: не похоже на корректный OpenSSH public key "
            "(ожидается `<type> <base64>[ comment]`)"
        )
    return value


def _check_ssh_mode_combo(
    mode: str | None,
    public_key: str | None,
    private_key_b64: str | None = None,
) -> None:
    """Согласованность тройки `(ssh_mode, ssh_public_key, ssh_private_key_b64)`.

    `supply` обязан нести `ssh_public_key`; `generate`/None — наоборот, ключ
    не принимают (он будет сгенерирован сервером или ключ не задаётся вовсе).
    Приватный ключ (`ssh_private_key_b64`) принимается ТОЛЬКО при `supply` —
    клиент может отдать свою пару целиком, чтобы консоль ходила под аккаунтом.
    """
    if mode == "supply" and public_key is None:
        raise ValueError("ssh_mode='supply' требует ssh_public_key")
    if mode != "supply" and public_key is not None:
        raise ValueError("ssh_public_key допустим только при ssh_mode='supply'")
    if private_key_b64 is not None and mode != "supply":
        raise ValueError("ssh_private_key_b64 допустим только при ssh_mode='supply'")


def _validate_ssh_private_key_b64(b64_value: str, public_key: str | None) -> str:
    """Декодировать и проверить приватный SSH-ключ из base64.

    Принимает приватный ключ в OpenSSH- или PEM-формате без passphrase
    (зашифрованный passphrase'ом ключ нам не разобрать — отбиваем). Если задан
    `public_key`, дополнительно проверяем, что публичная часть приватного
    совпадает с переданным public (тип + тело, комментарий игнорируем) — иначе
    в БД легла бы рассогласованная пара и консоль всё равно бы не зашла.
    Возвращает декодированный PEM-текст приватного ключа.
    """
    pem = decode_b64(b64_value, "ssh_private_key_b64")
    raw = pem.encode("utf-8")
    private = None
    for loader in (serialization.load_ssh_private_key, serialization.load_pem_private_key):
        try:
            private = loader(raw, password=None)
            break
        except (ValueError, TypeError, UnsupportedAlgorithm):
            continue
    if private is None:
        raise ValueError(
            "ssh_private_key_b64: не удалось разобрать как приватный ключ "
            "(ожидается OpenSSH/PEM без passphrase)"
        )
    if public_key is not None:
        try:
            derived = private.public_key().public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            ).decode("ascii")
        except (ValueError, UnsupportedAlgorithm) as exc:
            raise ValueError(
                "ssh_private_key_b64: не удалось вывести публичный ключ из приватного"
            ) from exc
        if derived.split()[:2] != public_key.strip().split()[:2]:
            raise ValueError(
                "ssh_private_key_b64 не соответствует переданному ssh_public_key"
            )
    return pem


class ServerAccountCreate(BaseModel):
    """Тело POST /server-accounts. Логин уникален в рамках каждого сервера."""

    server_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Список серверов, к которым привязывается аккаунт. Можно оставить "
            "пустым — тогда аккаунт заводится как хранимый креден без привязок, "
            "серверы добавляются позже через `/server-accounts/{id}/servers`. "
            "Все переданные серверы обязаны принадлежать тому же department'у, "
            "что и вызывающий."
        ),
    )
    # Regex держим в sync с `server_worker/src/clients/ssh.py::_LOGIN_RE` —
    # там идёт повторная валидация перед `chpasswd`, чтобы воркер не зависел
    # от того, дошёл ли request через эту схему. Если меняешь pattern —
    # меняй и там, и обнови тесты в обоих сервисах.
    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_LOGIN_PATTERN,
        description=(
            "Имя OS-аккаунта (root/postgres/...). "
            "Только буквы/цифры/`.`/`_`/`-` — защита от CRLF и shell-инъекций "
            "в audit details и в `chpasswd` payload."
        ),
    )
    password_b64: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Пароль в base64 (`base64.b64encode(plaintext)`). Если не передан "
            "— сервер сгенерирует `secrets.token_urlsafe(32)`. Декодируется на "
            "приёме; к раскодированному plaintext применяется политика: "
            "минимум 8 символов, буквы и цифры. Битый base64 → 422."
        ),
    )
    ssh_mode: str | None = Field(
        default=None,
        pattern=r"^(generate|supply)$",
        description=(
            "Опциональный SSH-ключ для входа под аккаунтом. `generate` — сервер "
            "генерит Ed25519-пару, хранит public + зашифрованный private и "
            "возвращает приватный ключ ОДИН раз в ответе создания (поле "
            "`ssh_private_key`). `supply` — клиент передаёт свой `ssh_public_key` "
            "(приватный остаётся у клиента, в ответе его нет). None — ключ не "
            "задаётся (как раньше; ключ догенерится при первом provision'е)."
        ),
    )
    ssh_public_key: str | None = Field(
        default=None,
        max_length=8192,
        description=(
            "OpenSSH public key (`<type> <base64>[ comment]`) для ssh_mode='supply'. "
            "Однострочный, валидируется на формат; финальная проверка — на воркере."
        ),
    )
    ssh_private_key_b64: str | None = Field(
        default=None,
        max_length=16384,
        description=(
            "Опциональный приватный SSH-ключ в base64 (`base64.b64encode(pem)`) — "
            "принимается ТОЛЬКО при ssh_mode='supply'. Если передан, он шифруется "
            "и хранится рядом с public'ом, и тогда консоль сможет ходить под этим "
            "аккаунтом по ключу. Должен быть в OpenSSH/PEM-формате без passphrase и "
            "соответствовать переданному ssh_public_key (иначе 422). Если не передан "
            "— хранится только public, приватный остаётся у клиента (консоль для "
            "этого аккаунта работать не будет)."
        ),
    )
    has_sudo: bool = Field(
        default=False,
        description="Право sudo. Требует отдельного action `grant_sudo` (admin-only).",
    )
    unix_groups: list[str] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Список Unix-групп аккаунта. Имена валидируются POSIX-паттерном; "
            "существование групп на боксе не проверяется — это задача worker'а."
        ),
    )
    linked_user_id: str | None = Field(
        default=None, description="Опциональный FK на платформенного user'а (для DBoS-аккаунтов)."
    )
    shell: str | None = Field(
        default=None, max_length=64, description="Login shell (/bin/bash и т.п.)."
    )
    home_dir: str | None = Field(
        default=None, max_length=256, description="Путь home-директории."
    )

    @field_validator("server_ids")
    @classmethod
    def _dedupe_server_ids(cls, value: list[str]) -> list[str]:
        # Сохраняем порядок, убираем дубли — иначе два одинаковых server_id
        # в одном запросе упёрлись бы в uq_account_server на середине вставки.
        seen: set[str] = set()
        out: list[str] = []
        for sid in value:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Политика проверяется по РАСКОДИРОВАННОМУ паролю, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str]) -> list[str]:
        return _validate_unix_groups(value)

    @field_validator("ssh_public_key")
    @classmethod
    def _check_ssh_public_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_ssh_public_key(value)

    @model_validator(mode="after")
    def _check_ssh_combo(self) -> "ServerAccountCreate":
        _check_ssh_mode_combo(self.ssh_mode, self.ssh_public_key, self.ssh_private_key_b64)
        if self.ssh_private_key_b64 is not None:
            _validate_ssh_private_key_b64(self.ssh_private_key_b64, self.ssh_public_key)
        return self

    def password(self) -> str | None:
        """Раскодированный plaintext пароля (или `None`, если не передан).

        Валидность base64 и политика уже проверены валидатором — здесь только
        повторный декод для сервис-слоя.
        """
        if self.password_b64 is None:
            return None
        return decode_b64(self.password_b64, "password_b64")

    def ssh_private_key(self) -> str | None:
        """Раскодированный PEM приватного ключа (или `None`, если не передан).

        Формат/соответствие public'у уже проверены валидатором — здесь только
        повторный декод base64 для сервис-слоя.
        """
        if self.ssh_private_key_b64 is None:
            return None
        return decode_b64(self.ssh_private_key_b64, "ssh_private_key_b64")


class ServerAccountUpdate(BaseModel):
    """Тело PATCH /server-accounts/{id}. Пароль через `rotate_password`,
    привязка серверов — через `/servers` под-операции.

    Поля `is_active` в апдейте нет: колонка в БД присутствует и отдаётся
    в GET, но dispatch/rotate/fetch_password её не читают, и менять её
    через PATCH ведёт только к расхождению между «логически выключенным»
    аккаунтом и тем фактом, что воркер всё равно отдаст пароль. Когда
    появится реальная семантика disable — поле вернётся вместе с гейтами
    в internal_service и pipeline'ах.
    """

    login: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_LOGIN_PATTERN,
        description=(
            "Сменить OS-логин — только DB-only переименование. Допустимо лишь "
            "когда аккаунт `present_on_server=false` на ВСЕХ привязанных серверах "
            "(на боксе ещё нет пользователя): пере-проверяется уникальность "
            "(server_id, login) по всем привязкам. Если аккаунт присутствует "
            "хоть на одном сервере → 409 LOGIN_LOCKED; смена живого логина — "
            "через `POST /server-accounts/{id}/recreate_login` (deprovision → "
            "rename → provision)."
        ),
    )
    has_sudo: bool | None = Field(default=None, description="Сменить sudo-флаг (требует `grant_sudo`).")
    unix_groups: list[str] | None = Field(
        default=None,
        max_length=64,
        description="Перезаписать список групп (валидируется POSIX-паттерном).",
    )
    linked_user_id: str | None = Field(default=None, description="Сменить связь с user'ом.")
    shell: str | None = Field(default=None, max_length=64, description="Сменить shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Сменить home_dir.")

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_unix_groups(value)


class ServerAccountServersUpdate(BaseModel):
    """Тело POST/DELETE /server-accounts/{id}/servers — линковка/отвязка.

    Привязываемые серверы обязаны быть в том же department'е, что и аккаунт.
    Отвязка снимает связку сразу и инициирует userdel на боксе, если учётка
    там стояла; можно отвязать и последний сервер.
    """

    server_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Серверы для привязки/отвязки (≥1).",
    )

    @field_validator("server_ids")
    @classmethod
    def _dedupe_server_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for sid in value:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out


class ServerAccountRecreateLoginRequest(BaseModel):
    """Тело POST /server-accounts/{id}/recreate_login.

    Сменить живой OS-логин на боксах: deprovision на всех привязанных
    серверах → переименование в БД → provision заново под новым логином.
    Доступ — только platform dep_admin отдела аккаунта или service-admin.
    """

    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_LOGIN_PATTERN,
        description="Новый OS-логин. Уникальность (server_id, login) проверяется по всем привязкам.",
    )


class ServerAccountSshKeyRequest(BaseModel):
    """Тело POST /server-accounts/{id}/ssh_key — задать/заменить SSH-ключ.

    `generate` — сервер генерит Ed25519-пару (приватный возвращается один раз);
    `supply` — клиент передаёт `ssh_public_key`. После записи в БД ключ сразу
    раскатывается `update_on_host`-fan-out'ом на все привязанные серверы.
    """

    ssh_mode: str = Field(
        ...,
        pattern=r"^(generate|supply)$",
        description="`generate` — сгенерить Ed25519; `supply` — взять переданный `ssh_public_key`.",
    )
    ssh_public_key: str | None = Field(
        default=None,
        max_length=8192,
        description="OpenSSH public key для ssh_mode='supply'. Однострочный, валидируется на формат.",
    )
    ssh_private_key_b64: str | None = Field(
        default=None,
        max_length=16384,
        description=(
            "Опциональный приватный SSH-ключ в base64 — только при ssh_mode='supply'. "
            "Если передан, шифруется и сохраняется рядом с public'ом (консоль сможет "
            "ходить под аккаунтом по ключу). OpenSSH/PEM без passphrase, должен "
            "соответствовать ssh_public_key (иначе 422)."
        ),
    )

    @field_validator("ssh_public_key")
    @classmethod
    def _check_ssh_public_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_ssh_public_key(value)

    @model_validator(mode="after")
    def _check_ssh_combo(self) -> "ServerAccountSshKeyRequest":
        _check_ssh_mode_combo(self.ssh_mode, self.ssh_public_key, self.ssh_private_key_b64)
        if self.ssh_private_key_b64 is not None:
            _validate_ssh_private_key_b64(self.ssh_private_key_b64, self.ssh_public_key)
        return self

    def ssh_private_key(self) -> str | None:
        """Раскодированный PEM приватного ключа (или `None`)."""
        if self.ssh_private_key_b64 is None:
            return None
        return decode_b64(self.ssh_private_key_b64, "ssh_private_key_b64")


class AccountKeyFanoutResponse(BaseModel):
    """Ответ ssh_key / rotate_ssh_key — сводка fan-out'а + одноразовый приватный ключ.

    `tasks` — `update_on_host`-задачи, поставленные на серверы, где аккаунт
    присутствует (форма `{server_id, task_id}` — та же, что у rotate-dispatch'а).
    `ssh_private_key` присутствует только когда ключ генерировался сервером
    (`generate` / rotate) и отдаётся один раз; для `supply` он `null`.
    """

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    ssh_public_key: str = Field(description="Установленный/сгенерированный public key.")
    ssh_private_key: str | None = Field(
        default=None,
        description="Приватный ключ в PEM — один раз, только при генерации сервером. Иначе null.",
    )
    tasks: list["AccountRotateTask"] = Field(
        default_factory=list,
        description="Поставленные `update_on_host`-задачи (по серверу, где аккаунт present).",
    )
    skipped: list["AccountRotateSkipped"] = Field(
        default_factory=list,
        description="Серверы, на которые задача не поставлена (decommissioned / не present / worker недоступен).",
    )


class AccountApplyCredentialsResponse(BaseModel):
    """Ответ ручного `POST /server-accounts/{id}/apply` — сводка проброса кред.

    Ставит `account.update_on_host` (несёт пароль+ssh-ключ) на серверы, где
    аккаунт присутствует. `tasks` — поставленные задачи (`{server_id, task_id}`),
    `skipped` — серверы, на которые задача не поставлена (decommissioned / не
    present / worker недоступен). Тот же apply, что авто-запускается после
    set/rotate пароля/ключа.
    """

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    tasks: list["AccountRotateTask"] = Field(
        default_factory=list,
        description="Поставленные `update_on_host`-задачи (по серверу, где аккаунт present).",
    )
    skipped: list["AccountRotateSkipped"] = Field(
        default_factory=list,
        description="Серверы, на которые задача не поставлена (decommissioned / не present / worker недоступен).",
    )


class ServerAccountAdoptRequest(BaseModel):
    """Тело POST /server-accounts/{id}/adopt_from_host.

    Принять факт-состояние OS-пользователя с конкретного сервера в БД. Поля
    `has_sudo`/`unix_groups`/`shell` опциональны: применяются ТОЛЬКО
    присутствующие (значения = то, что оператор увидел в diff как `found`).
    UI шлёт `found`-значения по отмеченным чекбоксам. Обновляется ТОЛЬКО БД —
    fan-out `update_on_host` на серверы НЕ идёт (хост уже в этом состоянии).

    `server_id` обязателен — аккаунт может жить на нескольких серверах, а
    adopt привязан к факту с конкретного хоста; сервер обязан быть привязан к
    аккаунту, иначе 404.
    """

    server_id: str = Field(
        ...,
        min_length=1,
        description="Сервер, с чьего факт-состояния принимаем поля. Обязан быть привязан к аккаунту.",
    )
    has_sudo: bool | None = Field(
        default=None, description="Принять sudo-флаг с хоста (если отмечен в diff)."
    )
    unix_groups: list[str] | None = Field(
        default=None,
        max_length=64,
        description="Принять список групп с хоста (валидируется POSIX-паттерном).",
    )
    shell: str | None = Field(
        default=None, max_length=64, description="Принять shell с хоста."
    )

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_unix_groups(value)


class ServerAccountImportRequest(BaseModel):
    """Тело POST /server-accounts/import — импорт незнакомого OS-пользователя.

    Заводит аккаунт из атрибутов, найденных на боксе инвентаризацией (т.е. из
    `unknown_users` ответа коллбэка). Пароль НЕ задаётся: на боксе его значение
    нам неизвестно — по умолчанию `source=discovered` без пароля. Аккаунт
    сразу привязывается к `server_id` и помечается `present_on_server=True`
    (пользователь уже физически на сервере).
    """

    server_id: str = Field(
        ...,
        min_length=1,
        description="Сервер, на котором найден пользователь. Аккаунт привязывается к нему.",
    )
    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="OS-логин пользователя (как в getent passwd).",
    )
    has_sudo: bool = Field(default=False, description="Состоит ли в sudo/admin-группе (факт с бокса).")
    unix_groups: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="Unix-группы пользователя (факт с бокса, валидируются POSIX-паттерном).",
    )
    shell: str | None = Field(default=None, max_length=64, description="Login shell с бокса.")
    source: str = Field(
        default="discovered",
        pattern=r"^(managed|discovered)$",
        description=(
            "Происхождение аккаунта. По умолчанию `discovered` (пароль с бокса "
            "неизвестен, password_encrypted = NULL). `managed` — если оператор "
            "собирается завести пароль через rotate_password позже."
        ),
    )

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str]) -> list[str]:
        return _validate_unix_groups(value)


class IgnoredLoginCreate(BaseModel):
    """Тело POST /server-accounts/ignored-logins — заигнорить логин в отделе."""

    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="OS-логин, который инвентаризация не должна показывать как незнакомого.",
    )
    reason: str | None = Field(
        default=None,
        max_length=512,
        description="Свободный комментарий: зачем логин в игноре.",
    )


class IgnoredLoginResponse(BaseModel):
    """Карточка одной записи ignore-list'а."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="ID записи (prefix ign_).")
    department_id: str = Field(description="Отдел, в котором действует игнор.")
    login: str = Field(description="Заигноренный OS-логин.")
    reason: str | None = Field(default=None, description="Комментарий оператора.")
    created_by: str | None = Field(default=None, description="user_id, добавивший запись.")
    created_at: datetime = Field(description="Когда логин заигнорен.")


class ServerAccountResponse(BaseModel):
    """Карточка аккаунта в ответе.

    `server_ids` — список всех привязанных серверов. `password_b64`
    заполняется только когда вызывающий держит action `view_password` —
    тогда это base64(plaintext). У вызывающего без `view_password` (только
    `view`) поле остаётся `None`. Сырого `password_encrypted` в ответе нет
    никогда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Account ID (prefix acc_).")
    server_ids: list[str] = Field(description="Привязанные серверы.")
    department_id: str = Field(description="Department владельца аккаунта.")
    login: str = Field(description="OS-логин.")
    source: str = Field(
        default="managed",
        description=(
            "Происхождение: `managed` (заведён через API, пароль известен) "
            "или `discovered` (найден инвентаризацией, пароля у API нет)."
        ),
    )
    has_sudo: bool = Field(description="Есть ли sudo.")
    unix_groups: list[str] = Field(description="Unix-группы.")
    linked_user_id: str | None = Field(default=None, description="FK на platform user.")
    shell: str | None = Field(default=None, description="Login shell.")
    home_dir: str | None = Field(default=None, description="Home directory.")
    is_active: bool = Field(description="Активен ли аккаунт.")
    password_rotated_at: datetime | None = Field(
        default=None, description="Когда последний раз ротировался пароль."
    )
    password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext-пароль. Присутствует только если "
            "вызывающий держит action `view_password`; иначе `null`. "
            "Декодируется стандартным base64.b64decode перед использованием."
        ),
    )
    previous_password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext ПРЕЖНЕГО пароля — удерживается на время "
            "переходного периода ротации, пока новый пароль не раскатан на все "
            "привязанные серверы. Оператор может подключаться и старым, и новым "
            "паролем. Присутствует только при `view_password` И только пока "
            "переходный период активен; в остальных случаях `null`. "
            "Декодируется стандартным base64.b64decode."
        ),
    )
    previous_password_rotated_at: datetime | None = Field(
        default=None,
        description=(
            "Когда был установлен прежний (удерживаемый) пароль. `null`, если "
            "переходного периода нет."
        ),
    )
    ssh_public_key: str | None = Field(
        default=None,
        description="OpenSSH public key аккаунта (если задан). Не секрет, отдаётся всем по `view`.",
    )
    ssh_key_fingerprint: str | None = Field(
        default=None,
        description=(
            "SHA256-отпечаток публичного SSH-ключа (формат `SHA256:<base64>`, "
            "как у `ssh-keygen -lf`). Считается из `ssh_public_key`; `null`, если "
            "ключ не задан. Нужен UI, чтобы показывать «ключ есть» без выдачи "
            "самого ключа."
        ),
    )
    ssh_private_key: str | None = Field(
        default=None,
        description=(
            "Приватный SSH-ключ в PEM. Отдаётся РОВНО ОДИН РАЗ — в ответе "
            "create (ssh_mode='generate'), ssh_key и rotate_ssh_key. В GET-"
            "карточке всегда `null` (приватный ключ хранится зашифрованным и "
            "наружу повторно не отдаётся)."
        ),
    )
    created_at: datetime = Field(description="Когда аккаунт создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="user_id, создавший аккаунт.")


class ServerAccountSshPrivateKeyResponse(BaseModel):
    """Ответ reveal'а приватного SSH-ключа аккаунта.

    Гейтится тем же `view_password`, что и раскрытие пароля. `ssh_private_key`
    — PEM-текст приватного ключа (OpenSSH-формат), расшифрованный из
    `ssh_private_key_encrypted`. `ssh_public_key` отдаётся рядом для удобства
    скачивания пары. Раскрытие пишет CRITICAL-аудит
    `server_account.ssh_private_key_revealed`.
    """

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    ssh_private_key: str = Field(description="Приватный ключ в PEM (OpenSSH-формат).")
    ssh_public_key: str | None = Field(
        default=None, description="Публичный ключ той же пары (если есть)."
    )


class ServerAccountRotateRequest(BaseModel):
    """Тело POST /server-accounts/{id}/rotate_password.

    `password_b64` опционален: если передан — `base64.b64encode(plaintext)`,
    декодируется на приёме, к plaintext применяется политика (минимум 8
    символов, буквы и цифры) и он используется как новый пароль; если нет —
    сервер генерирует `secrets.token_urlsafe(32)`. Plaintext в ответ не
    возвращается ни в одном случае.
    """

    password_b64: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Новый пароль в base64 (`base64.b64encode(plaintext)`). Если пуст "
            "— сервер сгенерирует случайный. Декодируется на приёме; к "
            "раскодированному plaintext применяется политика: минимум 8 "
            "символов, буквы и цифры. Битый base64 → 422."
        ),
    )

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Политика — по раскодированному plaintext, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value

    def password(self) -> str | None:
        """Раскодированный plaintext нового пароля (или `None`)."""
        if self.password_b64 is None:
            return None
        return decode_b64(self.password_b64, "password_b64")


class ServerAccountRotateResponse(BaseModel):
    """Ответ на rotate_password — без plaintext'а наружу."""

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    rotated_at: datetime = Field(description="UTC timestamp ротации.")


class AccountRotateTask(BaseModel):
    """Одна per-server задача ротации в ответе worker-dispatch'а.

    `server_name` (display_name либо hostname) и `status` добавлены для UI:
    оператор видит, какая задача на какой сервер ушла, не делая отдельный
    lookup. `status` сейчас всегда `dispatched` — успешно поставленная в
    очередь задача; форма оставлена расширяемой под будущие per-task статусы.
    """

    server_id: str = Field(description="Сервер, на котором применяется новый пароль.")
    server_name: str | None = Field(
        default=None,
        description=(
            "Имя сервера (display_name либо hostname). Заполняется в mass-"
            "rotation; в fan-out ответах (ssh_key / update_on_host) может быть None."
        ),
    )
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(
        default="dispatched",
        description="Статус диспетчеризации задачи (dispatched).",
    )


class AccountRotateSkipped(BaseModel):
    """Сервер, на который задача не поставлена (пропуск при массовой ротации)."""

    server_id: str = Field(description="Сервер, для которого dispatch не выполнен.")
    server_name: str | None = Field(
        default=None,
        description="Имя сервера (display_name либо hostname); None, если сервер не загружен.",
    )
    reason: str = Field(
        description=(
            "Причина пропуска: decommissioned | idempotent_conflict | "
            "worker_unreachable | not_attempted | not_found_or_cross_dept."
        )
    )


class AccountRotateDispatchResponse(BaseModel):
    """Ответ worker-dispatch ротации аккаунта.

    `mode` — `single` (точечная, один сервер) или `all` (массовая, все
    привязанные). `tasks` — по одной задаче на успешно поставленный сервер.
    `skipped` — серверы, на которые задача не поставлена (decommissioned или
    отбита воркером); при массовой ротации один битый сервер не валит весь
    батч.

    `partial_failure` помечен явно для UX-consistency: `False` означает,
    что весь батч пролетел (или пропуски — это idempotent/decommissioned,
    они в `skipped`). `True` поднимается, когда в массовом режиме worker
    отбил ServiceUnavailable после K успешных dispatch'ей — тогда K задач
    уже в очереди, остаток не пытались. `next_action` подсказывает UI,
    что делать дальше: `retry_not_attempted` (повторить запрос после
    стабилизации воркера) или `manual_cancel_dispatched` (отменить уже
    поставленные через `/tasks/{id}/cancel`, если откатить ротацию важно).
    """

    batch_id: str = Field(
        description=(
            "ID батча массовой ротации (prefix bat_). Сквозной идентификатор, "
            "под которым UI собирает per-task статусы; точечная ротация тоже "
            "получает batch_id (батч из одной задачи)."
        ),
    )
    mode: str = Field(description="single | all.")
    status: str = Field(default="queued", description="Статус постановки в очередь.")
    dispatched: list[AccountRotateTask] = Field(
        default_factory=list,
        description=(
            "Успешно поставленные в очередь задачи с per-task деталями "
            "(task_id / server_id / server_name / status). Для трекинга и отмены."
        ),
    )
    failed: list[AccountRotateSkipped] = Field(
        default_factory=list,
        description=(
            "Серверы, на которые задача НЕ поставлена: синхронная ошибка "
            "диспетчеризации (worker_unreachable), либо пропуск "
            "(decommissioned / idempotent_conflict / not_attempted / "
            "not_found_or_cross_dept) с причиной в `reason`."
        ),
    )
    tasks: list[AccountRotateTask] = Field(
        default_factory=list,
        description="Алиас `dispatched` для обратной совместимости.",
    )
    skipped: list[AccountRotateSkipped] = Field(
        default_factory=list,
        description="Алиас `failed` для обратной совместимости.",
    )
    partial_failure: bool = Field(
        default=False,
        description=(
            "True, если массовая ротация частично применилась (worker отбил "
            "после K успешных dispatch'ей). Auto-cancel не выполняется — UI "
            "должен показать `tasks` (уже dispatched) и `next_action`."
        ),
    )
    next_action: str | None = Field(
        default=None,
        description=(
            "Подсказка UI на случай partial_failure: `retry_not_attempted` "
            "(повторить весь запрос) или `manual_cancel_dispatched` "
            "(отменить уже поставленные task_ids). None — partial_failure=False."
        ),
    )


class AccountProvisionDispatchResponse(BaseModel):
    """Ответ worker-dispatch provision/update/deprovision OS-пользователя.

    `operation` — `provision` (useradd), `update` (usermod) или
    `deprovision` (userdel). `server_id` — сервер, на котором применяется
    операция (один из привязанных). `task_id` — id поставленной задачи.
    """

    operation: str = Field(description="provision | update | deprovision.")
    server_id: str = Field(description="Сервер, на котором применяется операция.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(default="queued", description="Статус постановки в очередь.")


class AccountRecreateLoginDispatch(BaseModel):
    """Один per-server диспатч в сводке recreate_login."""

    server_id: str = Field(description="Сервер, на котором применяется операция.")
    operation: str = Field(description="deprovision | provision.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")


class AccountRecreateLoginResponse(BaseModel):
    """Ответ POST /server-accounts/{id}/recreate_login.

    Оркестрация: на каждый привязанный сервер ставится `account.deprovision`
    под СТАРЫМ логином, логин переименовывается в БД, затем на каждый сервер
    ставится `account.provision` под НОВЫМ логином. `deprovision`/`provision` —
    списки поставленных задач (форма `{server_id, operation, task_id}`).
    `skipped` — серверы, на которые задача не поставлена (decommissioned и т.п.).
    """

    id: str = Field(description="Account ID.")
    old_login: str = Field(description="Логин до переименования.")
    new_login: str = Field(description="Новый логин (уже записан в БД).")
    deprovision: list[AccountRecreateLoginDispatch] = Field(
        default_factory=list,
        description="Поставленные `account.deprovision`-задачи (под старым логином).",
    )
    provision: list[AccountRecreateLoginDispatch] = Field(
        default_factory=list,
        description="Поставленные `account.provision`-задачи (под новым логином).",
    )
    skipped: list["AccountRotateSkipped"] = Field(
        default_factory=list,
        description="Серверы, на которые часть диспатчей не поставлена.",
    )
