"""Тестовая учётка отдела.

Под этой учёткой тест исполняется на стенде: `server_service` заводит её на
подготовке (`prepare-for-test`), `testing_worker` входит под ней по SSH-ключу.
Легаси работало под `u` с общим паролем `srv_pass`
(`emm/allta_app_full/libs/liballta.py:113-114`); на общем пароле держатся
межстендовые сценарии, поэтому пароль и ключ теперь задаёт администратор, а
не генерирует пайплайн на каждую подготовку.

Хранение. Одна credential на отдел в secret_service:

* `scope="service"`, `service="test_account"`, `owner_dept_id=<отдел>` —
  владеет отдел, а читать (reveal) может любой платформенный сервис-бот
  (`secret_service/.../access_service.py::_check_service`): и
  testing_service (claim), и server_service (prepare-for-test);
* `login` — логин учётки;
* секрет — JSON `{"v": 1, "password", "private_key", "public_key"}`
  (`encode_secret`/`decode_secret`). Приватный ключ — OpenSSH Ed25519 без
  passphrase, тот же формат, что у `server_service.server_account`.

В `department_test_settings` лежат только ссылка `test_account_credential_id`
 и нечувствительный шаблон домашнего каталога
`test_account_home_template`. `test_username` остаётся подсказкой логина и
зеркалом логина credential.

Запись credential идёт bearer'ом пользователя (см. `secret_client._user_call`):
значит, сохранить учётку может тот, кому secret_service разрешает заводить
сервисные credential отдела, — department_admin или admin secret_service
отдела. Чтение (логин и публичный ключ для UI, приватный ключ для claim) —
bot-токеном сервиса.

Ротация действует со следующей подготовки стенда: server_service раскрывает
credential в момент провижна учётки, testing_service — в момент claim.

Источник `test_account` глобальных переменных: механизма
`source_ref` в коде ещё нет, поэтому здесь только резолвер
`resolve_field(db, department_id, field)`; подключает его в
`services/variable_resolver.py` для `{"field": "login" | "password" | "home"}`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.repositories import department_test_settings as settings_repo
from src.schemas.department_test_account import DepartmentTestAccountUpdate
from src.services import audit_service, permissions, secret_client
from src.utils.ids import department_test_settings_id

CREDENTIAL_SERVICE = "test_account"
CREDENTIAL_SCOPE = "service"
CREDENTIAL_NAME = "test-account"
SECRET_FORMAT_VERSION = 1

HOME_PLACEHOLDER = "{TEST_USER}"
# Легаси `/home/u`: `emm/allta_app_full/starter.sh:54` (`cd /home/u/git`).
DEFAULT_HOME_TEMPLATE = "/home/{TEST_USER}"
# Легаси `user = 'u'` (`emm/allta_app_full/libs/liballta.py:113`).
DEFAULT_LOGIN_HINT = "u"

# Поля источника `test_account` и какие из них секретны.
FIELDS: tuple[str, ...] = ("login", "password", "home")
SENSITIVE_FIELDS: frozenset[str] = frozenset({"password"})

AUDIT_ACTION_UPDATE = "department_test_account.update"


@dataclass(frozen=True)
class AccountSecret:
    """Раскрытая учётка. `repr` не печатает секреты — объект попадает в логи исключений."""

    login: str
    password: str = field(repr=False)
    private_key: str = field(repr=False)
    public_key: str


def encode_secret(*, password: str, private_key: str, public_key: str) -> str:
    """Секрет credential: версия формата + пароль + SSH-пара."""
    return json.dumps(
        {
            "v": SECRET_FORMAT_VERSION,
            "password": password,
            "private_key": private_key,
            "public_key": public_key,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_secret(login: str | None, secret: str) -> AccountSecret:
    """Разобрать секрет credential. Неполная запись — `TEST_ACCOUNT_INVALID`."""
    try:
        data = json.loads(secret)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise DomainValidationError(
            error_code="TEST_ACCOUNT_INVALID",
            message="Test account credential has an unexpected format; save the account again",
        )
    values = {key: data.get(key) for key in ("password", "private_key", "public_key")}
    if not login or not all(isinstance(v, str) and v for v in values.values()):
        raise DomainValidationError(
            error_code="TEST_ACCOUNT_INVALID",
            message="Test account credential lacks login, password or SSH key; save the account again",
        )
    return AccountSecret(login=login, **values)


def generate_ssh_keypair() -> tuple[str, str]:
    """Ed25519-пара `(private_openssh, public_openssh)` — формат, который понимает asyncssh воркера."""
    private = ed25519.Ed25519PrivateKey.generate()
    private_openssh = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_openssh = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_openssh, f"{public_openssh} test-account"


def render_home(template: str | None, login: str) -> str:
    """Развернуть шаблон домашнего каталога для логина."""
    return (template or DEFAULT_HOME_TEMPLATE).replace(HOME_PLACEHOLDER, login)


# ── Потребители: очередь и резолвер переменных ──────────────────────────────


async def get_credential_id(db: AsyncSession, department_id: str) -> str | None:
    """Ссылка на credential тестовой учётки отдела либо None."""
    row = await settings_repo.get_by_department(db, department_id)
    return row.test_account_credential_id if row is not None else None


async def require_credential_id(db: AsyncSession, department_id: str) -> str:
    """То же, но без учётки — понятная ошибка `TEST_ACCOUNT_NOT_CONFIGURED`."""
    credential_id = await get_credential_id(db, department_id)
    if not credential_id:
        raise DomainValidationError(
            error_code="TEST_ACCOUNT_NOT_CONFIGURED",
            message=(
                "тестовая учётка отдела не настроена "
                "(Администрирование → «Тестовая учётка»)"
            ),
            details={"department_id": department_id},
        )
    return credential_id


async def reveal_account(credential_id: str) -> AccountSecret:
    """Раскрыть учётку bot-токеном сервиса (claim, резолвер, карточка UI)."""
    login, secret = await secret_client.reveal_credential(credential_id)
    return decode_secret(login, secret)


async def resolve_account(db: AsyncSession, department_id: str) -> AccountSecret:
    """Учётка отдела целиком. Не настроена — `TEST_ACCOUNT_NOT_CONFIGURED`."""
    return await reveal_account(await require_credential_id(db, department_id))


async def resolve_field(db: AsyncSession, department_id: str, field_name: str) -> str:
    """Значение поля источника `test_account` для отдела стенда.

    `login` → `TEST_USER`, `password` → `TEST_PASSWORD` (секрет, см.
    `SENSITIVE_FIELDS`), `home` → `TEST_HOME` (шаблон учётки, по умолчанию
    `/home/{TEST_USER}`). Точка подключения для резолвера.
    """
    if field_name not in FIELDS:
        raise DomainValidationError(
            error_code="TEST_ACCOUNT_FIELD_UNKNOWN",
            message=f"Unknown test_account field {field_name!r}",
            details={"field": field_name, "allowed": list(FIELDS)},
        )
    account = await resolve_account(db, department_id)
    if field_name == "login":
        return account.login
    if field_name == "password":
        return account.password
    row = await settings_repo.get_by_department(db, department_id)
    return render_home(row.test_account_home_template if row is not None else None, account.login)


# ── HTTP: страница «Тестовая учётка» ────────────────────────────────────────


def _view(department_id: str, row, account: AccountSecret | None, *, credential_missing: bool) -> dict:
    template = (row.test_account_home_template if row is not None else None) or DEFAULT_HOME_TEMPLATE
    return {
        "department_id": department_id,
        "configured": account is not None,
        "credential_id": row.test_account_credential_id if row is not None else None,
        "credential_missing": credential_missing,
        "login": account.login if account else None,
        "login_hint": (row.test_username if row is not None else None) or DEFAULT_LOGIN_HINT,
        "has_password": bool(account and account.password),
        "ssh_public_key": account.public_key if account else None,
        "home_template": template,
        "home": render_home(template, account.login) if account else None,
        "updated_at": row.updated_at if row is not None else None,
    }


async def _load(db: AsyncSession, department_id: str):
    """Строка настроек + раскрытая учётка. Удалённый в secret_service credential — не 404, а `credential_missing`."""
    row = await settings_repo.get_by_department(db, department_id)
    credential_id = row.test_account_credential_id if row is not None else None
    if not credential_id:
        return row, None, False
    try:
        return row, await reveal_account(credential_id), False
    except NotFoundError:
        return row, None, True


async def get_for(db: AsyncSession, identity: Identity, department_id: str) -> dict:
    """GET карточки. Гейт — `(department_test_account, view)` своего отдела."""
    await permissions.require_department_action(
        db, identity, department_id, EntityType.DEPARTMENT_TEST_ACCOUNT, Action.VIEW,
    )
    row, account, missing = await _load(db, department_id)
    return _view(department_id, row, account, credential_missing=missing)


async def _create_or_adopt(token: str, department_id: str, login: str, secret: str) -> str:
    """Завести credential отдела; если запись с этим именем уже есть — переиспользовать её."""
    try:
        created = await secret_client.create_credential(
            token, name=CREDENTIAL_NAME, service=CREDENTIAL_SERVICE, scope=CREDENTIAL_SCOPE,
            owner_dept_id=department_id, login=login, secret=secret,
        )
        return created["id"]
    except ConflictError as exc:
        if exc.error_code != "NAME_DUPLICATE":
            raise
    existing = await secret_client.find_credential(
        token, name=CREDENTIAL_NAME, service=CREDENTIAL_SERVICE, scope=CREDENTIAL_SCOPE,
        owner_dept_id=department_id,
    )
    if existing is None or not existing.get("id"):
        raise ConflictError(
            error_code="TEST_ACCOUNT_CREDENTIAL_CONFLICT",
            message="secret_service already has a test-account credential of this department that is not visible to you",
            details={"department_id": department_id},
        )
    await secret_client.update_credential(token, existing["id"], login=login, secret=secret)
    return existing["id"]


async def upsert(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    payload: DepartmentTestAccountUpdate,
    token: str,
) -> dict:
    """PUT — завести или сменить учётку. Действует со следующей подготовки стенда.

    Пароль и SSH-пара хранятся только в secret_service; незаданный пароль
    оставляет прежний, `regenerate_ssh_key` выпускает новую пару (на первой
    настройке — всегда). Аудит перечисляет изменённые поля, не значения.
    """
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.DEPARTMENT_TEST_ACCOUNT, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            AUDIT_ACTION_UPDATE,
            target_id=department_id, target_type="department_test_account",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    row, current, _missing = await _load(db, department_id)
    credential_id = row.test_account_credential_id if row is not None and current is not None else None

    login = payload.login or (current.login if current else None) or (
        row.test_username if row is not None else DEFAULT_LOGIN_HINT
    )
    password = payload.password or (current.password if current else None)
    if not password:
        raise DomainValidationError(
            error_code="TEST_ACCOUNT_PASSWORD_REQUIRED",
            message="Password is required when the test account is configured for the first time",
            details={"department_id": department_id},
        )
    if current is None or payload.regenerate_ssh_key:
        private_key, public_key = generate_ssh_keypair()
    else:
        private_key, public_key = current.private_key, current.public_key

    fields: list[str] = []
    if current is None or login != current.login:
        fields.append("login")
    if current is None or password != current.password:
        fields.append("password")
    if current is None or private_key != current.private_key:
        fields.append("ssh_key")

    secret = encode_secret(password=password, private_key=private_key, public_key=public_key)
    try:
        if credential_id is None:
            credential_id = await _create_or_adopt(token, department_id, login, secret)
        elif fields:
            await secret_client.update_credential(token, credential_id, login=login, secret=secret)
    except AppException as exc:
        audit_service.emit(
            AUDIT_ACTION_UPDATE,
            target_id=department_id, target_type="department_test_account",
            status="denied" if isinstance(exc, AuthorizationError) else "failure",
            allowed=not isinstance(exc, AuthorizationError),
            details={"reason": exc.error_code, "fields": fields},
        )
        raise

    changes: dict = {"test_account_credential_id": credential_id, "test_username": login}
    if payload.home_template is not None and (
        row is None or payload.home_template != row.test_account_home_template
    ):
        changes["test_account_home_template"] = payload.home_template
        fields.append("home_template")
    if row is None:
        row = await settings_repo.create(db, {
            "id": department_test_settings_id(),
            "department_id": department_id,
            "test_account_home_template": DEFAULT_HOME_TEMPLATE,
            **changes,
        })
    else:
        await settings_repo.update(db, row, changes)
    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        AUDIT_ACTION_UPDATE,
        target_id=department_id, target_type="department_test_account",
        status="success", allowed=True,
        details={"credential_id": credential_id, "created": current is None, "fields": fields},
    )
    account = AccountSecret(login=login, password=password, private_key=private_key, public_key=public_key)
    return _view(department_id, row, account, credential_missing=False)


__all__ = [
    "AccountSecret",
    "CREDENTIAL_NAME",
    "CREDENTIAL_SCOPE",
    "CREDENTIAL_SERVICE",
    "DEFAULT_HOME_TEMPLATE",
    "FIELDS",
    "SENSITIVE_FIELDS",
    "decode_secret",
    "encode_secret",
    "generate_ssh_keypair",
    "get_credential_id",
    "get_for",
    "render_home",
    "require_credential_id",
    "resolve_account",
    "resolve_field",
    "reveal_account",
    "upsert",
]
