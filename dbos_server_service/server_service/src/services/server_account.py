"""Use cases для server_accounts — CRUD + rotate_password + M2M-линковка.

Аккаунт привязан к набору серверов (many-to-many через
`server_account_servers`). Пароль — общий на все привязанные серверы и
хранится на строке аккаунта. Карточка аккаунта доступна по `view`. Если
вызывающий вдобавок держит `view_password`, тот же GET доносит расшифрованный
пароль в base64 — отдельной reveal-ручки нет. Internal endpoint
(`internal_service`) для worker'а остаётся.

Visibility-check (cross-department) скрывает чужие аккаунты за 404, чтобы
не выдавать факт существования. Аккаунт видим, если его `department_id`
совпадает с caller'ом. Симметрично с `services/server.py`.
"""

import base64
import logging
import secrets
import string
import time
from collections import OrderedDict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import (
    SERVICE_NAME,
    Action,
    EntityType,
    PlatformRole,
    ServiceRole,
)
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import Server, ServerAccount, ServerAccountIgnoredLogin
from src.repositories import server_account as repo
from src.repositories import server_account_ignored_login as ignored_login_repo
from src.schemas.identity import IdentityContext
from src.schemas.server_account import (
    IgnoredLoginCreate,
    ServerAccountAdoptRequest,
    ServerAccountCreate,
    ServerAccountImportRequest,
    ServerAccountServersUpdate,
    ServerAccountUpdate,
)
from src.services import audit_context, audit_service, permissions, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.services.server import load_visible_server, load_visible_servers
from src.utils.ids import ignored_login_id
from src.utils.ids import server_account_id as new_id

logger = logging.getLogger(__name__)

# Окно throttling'а аудита раскрытия паролей. Ключ (actor_id, account_id) —
# (timestamp последнего CRITICAL'а, накопленный счётчик reveal'ов в окне).
# Пока в окне — повторные вызовы пишут INFO `password_revealed_throttled`,
# счётчик идёт в details обоих типов событий, чтобы SIEM мог фильтровать
# только по CRITICAL count и всё равно видеть общее число reveal'ов в окне.
# In-memory словарь живёт на процесс, при рестарте теряется (приемлемо:
# SIEM всё равно увидит первый CRITICAL после рестарта). Multi-worker деплой
# даст по одному CRITICAL на воркера, но не водопад.
#
# Bounded LRU: при штурме длинного списка уникальных (actor, account) пар
# словарь рос бы безгранично между sweep'ами (stale-cleanup срабатывает
# только на cache-miss того же окна). Кэп `_REVEAL_AUDIT_WINDOW_MAX`
# держит размер в пределе — на вытеснении уходит самый старый по
# обращению ключ (LRU). Throttle-семантика самой статистики не страдает:
# вытесненный actor получит ещё один CRITICAL на следующем reveal'е
# вместо INFO, что в случае реального шторма даёт SIEM больше сигнала,
# а не меньше.
_REVEAL_AUDIT_WINDOW_MAX = 10000
_REVEAL_AUDIT_WINDOW: "OrderedDict[tuple[str, str], tuple[float, int]]" = OrderedDict()


def _record_reveal_attempt(actor_id: str | None, account_id: str) -> tuple[bool, int]:
    """Зафиксировать reveal-вызов и вернуть `(should_emit_critical, total_in_window)`.

    `total_in_window` — накопленный счётчик reveal'ов для пары (actor, account)
    в рамках текущего окна (включает текущий вызов). Caller кладёт его в
    `details.total_reveals_in_window`, чтобы SIEM ловил bulk-reveal даже когда
    правило смотрит только на CRITICAL count.

    На первом вызове в окне эмитится CRITICAL и счётчик начинается с 1.
    Последующие в том же окне — INFO throttled, счётчик инкрементируется.
    Окно отсчитывается от первого CRITICAL'а (`last_critical_at`); счётчик
    сбрасывается, когда CRITICAL уезжает за горизонт окна.

    `actor_id is None` (анонимные / сервисные без identity) — всегда CRITICAL,
    счётчик не накапливается (мерджить разные `None`-bucket'ы в один опасно).
    Window=0 отключает throttle: всегда CRITICAL, счётчик не ведётся.
    """
    window = get_settings().password_reveal_audit_window_seconds
    if window <= 0 or actor_id is None:
        return True, 1
    now = time.monotonic()
    key = (actor_id, account_id)
    entry = _REVEAL_AUDIT_WINDOW.get(key)
    if entry is None or (now - entry[0]) >= window:
        _REVEAL_AUDIT_WINDOW[key] = (now, 1)
        _REVEAL_AUDIT_WINDOW.move_to_end(key)
        # Best-effort sweep устаревших ключей — иначе словарь распухает
        # на долгоживущем процессе. Линейный пробег, выполняется только
        # при miss'е (т.е. редко), для O(n) словаря допустимо.
        stale_cutoff = now - window
        stale_keys = [k for k, (ts, _cnt) in _REVEAL_AUDIT_WINDOW.items() if ts < stale_cutoff]
        for k in stale_keys:
            _REVEAL_AUDIT_WINDOW.pop(k, None)
        # LRU-eviction: после insert'а размер мог превысить кэп (хвост
        # старее всех — `popitem(last=False)` его сбросит).
        while len(_REVEAL_AUDIT_WINDOW) > _REVEAL_AUDIT_WINDOW_MAX:
            _REVEAL_AUDIT_WINDOW.popitem(last=False)
        return True, 1
    last_at, count = entry
    new_count = count + 1
    _REVEAL_AUDIT_WINDOW[key] = (last_at, new_count)
    _REVEAL_AUDIT_WINDOW.move_to_end(key)
    return False, new_count


def _should_emit_critical_reveal(actor_id: str | None, account_id: str) -> bool:
    """Legacy-обёртка над `_record_reveal_attempt` без счётчика.

    Сохранена для совместимости со старыми тестами, ожидающими bool-ответа
    и не интересующимися cumulative count'ом. Новый код должен звать
    `_record_reveal_attempt` напрямую и класть счётчик в audit-details.
    """
    should_emit, _count = _record_reveal_attempt(actor_id, account_id)
    return should_emit


def _added_sudo_groups(
    current_groups: list[str] | None, new_groups: list[str] | None
) -> set[str]:
    """Какие sudo-дающие группы ДОБАВЛЯЮТСЯ относительно текущего состояния.

    Сравниваем именно добавление (new − current), а не наличие: если
    привилегированная группа уже была у учётки, её сохранение под обычным
    `update` не должно требовать `grant_sudo`. Снятие группы тем более
    допустимо (понижение привилегии). Набор групп — из настройки
    `sudo_conferring_groups`.
    """
    conferring = get_settings().sudo_conferring_groups
    added = set(new_groups or []) - set(current_groups or [])
    return added & set(conferring)


def _grant_sudo_denied_error(sudo_groups: set[str]) -> AuthorizationError:
    """403, когда подъём sudo (флаг или привилегированная группа) без `grant_sudo`.

    Если триггер — добавление sudo-дающей группы, отдаём отдельный
    `SUDO_GROUP_REQUIRES_GRANT_SUDO`, чтобы клиент отличал «нельзя в эту
    группу» от обычного отказа по has_sudo. Только has_sudo→true оставляет
    привычный `PERMISSION_DENIED`.
    """
    if sudo_groups:
        return AuthorizationError(
            error_code="SUDO_GROUP_REQUIRES_GRANT_SUDO",
            message=(
                "Adding the account to a sudo-conferring group requires the "
                "grant_sudo action"
            ),
            details={
                "entity_type": "server_account",
                "action": "grant_sudo",
                "sudo_groups": sorted(sudo_groups),
            },
        )
    return AuthorizationError(
        error_code="PERMISSION_DENIED",
        message="Role does not grant 'grant_sudo' on 'server_account'",
        details={"entity_type": "server_account", "action": "grant_sudo"},
    )


async def _authorize_account_action(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    action: str,
    audit_action: str,
    *,
    for_update: bool = False,
    extra_details: dict | None = None,
) -> ServerAccount:
    """Загрузить учётку и авторизовать `action` на ней (роль ИЛИ per-account грант).

    Единая точка для всех account-targeted операций. Решает две задачи разом:
    permission-check (аддитивная модель `has_account_action`) и visibility.

    Порядок и enumeration-резистентность:

    * Сначала грузим учётку (raw, без dept-фильтра) — per-account грант нельзя
      проверить, не зная конкретную учётку.
    * Держатель бланкетной роли отдела ведёт себя как раньше: видит 404 на
      невидимую (cross-dept / отсутствующую) учётку, иначе проходит.
    * Caller без бланкетной роли проходит, только если у него есть прямой грант
      на эту (видимую ему) учётку с этим действием; во всех остальных случаях —
      одинаковый 403 PERMISSION_DENIED (невидимая учётка, чужой dept,
      существующая-без-гранта неотличимы — нет existence-oracle'а).

    provision/deprovision гейтятся одноимёнными действиями (ролевая матрица
    ИЛИ per-account флаг) в worker-dispatch'е и сюда не приходят.
    """
    has_role = await permissions.has_action(
        db, identity, EntityType.SERVER_ACCOUNT, action
    )
    account = await (
        repo.get_for_update(db, account_id) if for_update else repo.get_by_id(db, account_id)
    )
    visible = account is not None and account.department_id == identity.department_id

    if has_role:
        # Ролевой путь: поведение как раньше — visibility-404 на невидимую цель.
        if not visible:
            audit_service.emit(
                audit_action,
                target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", **(extra_details or {})},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found"
            )
        return account

    # Без бланкетной роли — единственный путь это прямой грант на видимую учётку.
    if visible and await permissions.has_account_action(db, identity, account, action):
        return account

    # Нет ни роли, ни гранта (или цель невидима) — 403 без раскрытия
    # существования. denied-аудит зеркалит emit_denied_on_authz_error.
    details: dict = dict(extra_details) if extra_details else {}
    details["reason"] = "permission_denied"
    if identity.subject_type is not None:
        details.setdefault("subject_type", identity.subject_type)
    audit_service.emit(
        audit_action,
        target_id=account_id, target_type="server_account",
        status="denied", allowed=False,
        details=details,
    )
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message=f"No access to action '{action}' on this server account",
        details={"entity_type": "server_account", "action": action},
    )


async def _authorize_account_action_any(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    actions: tuple[str, ...],
    audit_action: str,
) -> ServerAccount:
    """Как `_authorize_account_action`, но проходит при ЛЮБОМ из `actions`.

    Нужно для карточки (`view` ИЛИ `view_password`): держатель только одного
    из двух не должен ловить лишний denied-аудит на промахе по другому.
    Семантика visibility/enumeration та же.
    """
    account = await repo.get_by_id(db, account_id)
    visible = account is not None and account.department_id == identity.department_id
    has_any_role = False
    for a in actions:
        if await permissions.has_action(db, identity, EntityType.SERVER_ACCOUNT, a):
            has_any_role = True
            break
    if has_any_role:
        if not visible:
            audit_service.emit(
                audit_action,
                target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found"
            )
        return account
    if visible:
        for a in actions:
            if await permissions.has_account_action(db, identity, account, a):
                return account
    details: dict = {"reason": "permission_denied"}
    if identity.subject_type is not None:
        details.setdefault("subject_type", identity.subject_type)
    audit_service.emit(
        audit_action,
        target_id=account_id, target_type="server_account",
        status="denied", allowed=False,
        details=details,
    )
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message="No access to this server account",
        details={"entity_type": "server_account", "actions": list(actions)},
    )


async def _load_account_visible(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """SELECT аккаунта + dept-isolation. 404 во всех ambiguity-ветках.

    Видимость теперь строится на `account.department_id` (аккаунт может
    жить сразу на нескольких серверах одного отдела), а не на одиночном
    server'е.
    """
    account = await repo.get_by_id(db, account_id)
    if account is None:
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    if identity.department_id != account.department_id:
        # Скрываем существование аккаунта чужого dept за тем же 404, что и
        # для несуществующего id — иначе по разнице ответов утечёт enumeration.
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    return account


async def _load_account_visible_for_update(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """То же, что `_load_account_visible`, но берёт row-lock на аккаунте.

    Используется write-операциями (`update_account`, `link_servers`,
    `unlink_servers`) — лок сериализует параллельные write'ы по одному
    account_id, чтобы PATCH полей не уезжал на fan-out со stale links и
    наоборот. Read-операции (`get_account`, `delete_account`,
    `rotate_password`) этим не пользуются.
    """
    account = await repo.get_for_update(db, account_id)
    if account is None:
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    if identity.department_id != account.department_id:
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    return account


def _generate_password() -> str:
    """Дефолтный генератор паролей для новых аккаунтов и rotate'а."""
    return secrets.token_urlsafe(32)


# Алфавит для авто-сгенерированных кред под provision: digit/letter/symbol —
# чтобы результат сразу удовлетворял `is_strong` (16+ chars + три класса).
_STRONG_PWD_SYMBOLS = "!@#$%^&*-_=+?"
_STRONG_PWD_LENGTH = 24


_STRONG_PWD_MAX_ATTEMPTS = 32


def _generate_strong_password() -> str:
    """Сгенерировать пароль под provision: 24 символа, буква + цифра + символ.

    Длина с запасом над `MIN_STRONG_PASSWORD_LENGTH` (16) — энтропии хватает,
    а полисная проверка проходит при любой перестановке. Алфавит — латиница +
    цифры + ограниченный набор спецсимволов: те, что не ломают shell-цитирование
    (исключены кавычки, бэктики, $ и обратный слэш).

    На 24 символах вероятность не получить все три класса за один candidate
    исчезающе мала (~10^-30), но цикл всё равно ограничен `_STRONG_PWD_MAX_ATTEMPTS`,
    чтобы при сломанном `secrets`-источнике или странной перенастройке
    алфавита не уйти в бесконечный цикл.
    """
    alphabet = string.ascii_letters + string.digits + _STRONG_PWD_SYMBOLS
    for _ in range(_STRONG_PWD_MAX_ATTEMPTS):
        candidate = "".join(secrets.choice(alphabet) for _ in range(_STRONG_PWD_LENGTH))
        has_letter = any(ch.isalpha() for ch in candidate)
        has_digit = any(ch.isdigit() for ch in candidate)
        has_symbol = any(ch in _STRONG_PWD_SYMBOLS for ch in candidate)
        if has_letter and has_digit and has_symbol:
            return candidate
    raise RuntimeError("could not generate password matching policy")


def _generate_ssh_keypair() -> tuple[str, str]:
    """Сгенерировать Ed25519-пару. Возвращает `(private_pem, public_openssh)`.

    Ed25519 короче и быстрее RSA, и большинство современных sshd его понимает.
    Private — OpenSSH-формат без passphrase (ключ дальше шифруется AES-GCM на
    стороне server_service'а перед записью в БД, отдельный wrap паролем смысла
    не имеет). Public — однострочная строка, готовая к укладке в authorized_keys.
    """
    private = ed25519.Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_openssh = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_pem, public_openssh


async def ensure_provision_credentials(
    db: AsyncSession,
    account: ServerAccount,
) -> tuple[ServerAccount, dict, bool]:
    """Гарантировать наличие пары `(password, ssh_keypair)` у аккаунта.

    Sticky по каждому секрету в отдельности: если у аккаунта уже есть пароль —
    оставляем именно его (и расшифровываем для caller'а); если есть SSH-ключ —
    оставляем существующую пару. Отсутствующий секрет догенерируем, шифруем и
    сохраняем — flush в этой же транзакции, commit делает caller. Это нужно
    для legacy-аккаунтов до миграции b9c2e7d4a8f1: там пароль уже мог быть
    выставлен через rotate_password или create_account, а ssh-пары ещё нет.

    Возвращает `(account, creds, generated)`:
      * `creds = {password, ssh_public_key, ssh_private_key}` (plaintext);
      * `generated=True`, если хотя бы один секрет был сгенерирован в этом
        вызове (caller использует флаг для `force_replace` в worker payload —
        новый материал на боксе надо принудительно перезаписать поверх того,
        что там лежит).

    Если нужно принудительно сменить и пароль и SSH (переустановка ОС) —
    caller сначала зовёт `reset_provision_credentials`, потом этот метод.
    """
    generated = False
    was_already_pending = bool(account.credentials_pending_apply)

    if account.password_encrypted is not None:
        password = secrets_service.decrypt(
            account.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(account.id),
        )
    else:
        password = _generate_strong_password()
        account.password_encrypted = secrets_service.encrypt(
            password,
            aad=secrets_service.aad_for_server_account_password(account.id),
        )
        generated = True

    if account.ssh_public_key is not None:
        public_openssh = account.ssh_public_key
        if not account.ssh_private_key_encrypted:
            # Legacy / corrupted row: public_key есть, private — нет. Отдать
            # worker'у пустой private_pem нельзя — он залил бы на бокс
            # authorized_keys без матчающего private key, последующий SSH под
            # этим ключом отвалится. Бьём 422, оператор разбирается вручную
            # (либо сбрасывает через `reset_provision_credentials`, либо чинит
            # restore из бэкапа).
            raise DomainValidationError(
                error_code="ACCOUNT_SSH_KEY_INCONSISTENT",
                message=(
                    "Account has ssh_public_key but no ssh_private_key_encrypted "
                    "— legacy row needs to be reset before provision"
                ),
                details={"account_id": account.id},
            )
        private_pem = secrets_service.decrypt(
            account.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(account.id),
        )
    else:
        private_pem, public_openssh = _generate_ssh_keypair()
        account.ssh_public_key = public_openssh
        account.ssh_private_key_encrypted = secrets_service.encrypt(
            private_pem,
            aad=secrets_service.aad_for_server_account_ssh_key(account.id),
        )
        generated = True

    # На любом dispatch'е provision'а сохранённый в БД ciphertext перестаёт
    # считаться подтверждённым до прихода callback'а worker'а — он либо
    # дольёт его на бокс, либо нет. Между этими событиями БД ≠ бокс,
    # retry должен форсить overwrite.
    account.credentials_pending_apply = True

    # Flush только когда реально есть, что слить: либо сгенерили новый
    # секрет, либо подняли pending_apply с False на True. Когда оба секрета
    # уже на месте и флаг был True ещё до вызова — это no-op-ensure
    # (sticky-decrypt), лишний flush выносил бы in-flight мутации caller'а
    # в транзакцию раньше времени.
    if generated or not was_already_pending:
        await db.flush()

    creds = {
        "password": password,
        "ssh_public_key": public_openssh,
        "ssh_private_key": private_pem,
    }
    return account, creds, generated


async def reset_provision_credentials(
    db: AsyncSession,
    account: ServerAccount,
) -> ServerAccount:
    """Снести password+ssh-keypair у аккаунта — следующий `ensure_provision_credentials`
    сгенерирует новые.

    Используется при переустановке ОС: сервер начинает с чистого листа, старые
    креды (которые могут утечь через образ) теряют смысл, и worker должен
    залить свежий public_key и chpasswd на боксе.
    """
    account.password_encrypted = None
    account.ssh_public_key = None
    account.ssh_private_key_encrypted = None
    # Reset делает caller только перед свежим ensure → следующий dispatch
    # выйдет с pending_apply=True. Здесь явно ставим True, чтобы между
    # reset'ом и ensure'ом (или если ensure упадёт до flush'а) состояние
    # не оставалось «применено» — на боксе всё равно ничего нового нет.
    account.credentials_pending_apply = True
    await db.flush()
    return account


async def _resolve_same_dept_servers(
    db: AsyncSession,
    identity: IdentityContext,
    server_ids: list[str],
    audit_action: str,
) -> list[Server]:
    """Подгрузить все серверы из списка с dept-isolation.

    Любой server чужого/несуществующего dept'а → 404 (скрываем факт
    существования). На вход уже идёт дедуплицированный список.

    Один SQL-запрос `WHERE id IN (...)` через `load_visible_servers` — раньше
    был цикл `for sid in server_ids: load_visible_server` (N+1). Поведение
    идентичное: при отсутствии хотя бы одного видимого id поднимаем
    `NotFoundError` для первого пропавшего sid (в порядке исходного списка)
    с тем же audit-эмитом, что и старая ветка.
    """
    if not server_ids:
        return []
    visible = await load_visible_servers(db, identity, server_ids)
    missing = [sid for sid in server_ids if sid not in visible]
    if missing:
        first_missing = missing[0]
        audit_service.emit(
            audit_action,
            target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found_or_cross_dept",
                "server_id": first_missing,
            },
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    return [visible[sid] for sid in server_ids]


async def create_account(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerAccountCreate,
) -> tuple[ServerAccount, str | None]:
    """INSERT нового аккаунта + привязка к списку серверов.

    Порядок проверок:
      1. CREATE permission.
      2. Каждый сервер из `server_ids` существует + dept caller'а совпадает
         (иначе 404 — скрываем факт существования чужого сервера).
      3. has_sudo=True → дополнительно требует GRANT_SUDO action.
      4. Шифруем password (переданный или сгенерированный).
      5. INSERT аккаунта + связок. UNIQUE(server_id, login) на join →
         409 ACCOUNT_DUPLICATE (login занят на одном из серверов).

    SSH-ключ опционален (`ssh_mode`): `generate` — генерим Ed25519, храним
    public + зашифрованный private, и возвращаем приватный ОДИН раз вторым
    элементом кортежа; `supply` — сохраняем переданный public (private у нас
    нет). Без ssh_mode ключ не задаётся (догенерится при первом provision'е).
    Второй элемент кортежа — приватный ключ (только для `generate`), иначе None.
    """
    with emit_denied_on_authz_error(
        "server_account.create",
        target_type="server_account",
        extra_details={"server_ids": payload.server_ids},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.CREATE
        )

    servers = await _resolve_same_dept_servers(
        db, identity, payload.server_ids, "server_account.create"
    )
    # Все серверы из одного отдела (caller'а) — department аккаунта берём
    # из caller'а; load_visible_server уже гарантировал совпадение.
    department_id = identity.department_id

    # has_sudo=True ИЛИ заведение учётки сразу в sudo-дающей группе — эскалация
    # привилегий, требует `grant_sudo`. На create текущее состояние пустое,
    # поэтому любая привилегированная группа в payload считается добавлением.
    sudo_groups = _added_sudo_groups(None, list(payload.unix_groups))
    if payload.has_sudo or sudo_groups:
        if not await permissions.has_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.GRANT_SUDO
        ):
            audit_service.emit(
                "server_account.create",
                target_type="server_account",
                status="denied", allowed=False,
                details={
                    "reason": "grant_sudo_denied",
                    "server_ids": payload.server_ids,
                    "login": payload.login,
                    "sudo_groups": sorted(sudo_groups),
                },
            )
            raise _grant_sudo_denied_error(sudo_groups)

    provided = payload.password()
    plaintext = provided if provided is not None else _generate_password()
    account_id = new_id()
    encrypted = secrets_service.encrypt(
        plaintext,
        aad=secrets_service.aad_for_server_account_password(account_id),
    )

    # SSH-ключ на создании. generate → Ed25519, public + зашифрованный private
    # (private отдаём один раз в ответе). supply → сохраняем переданный public,
    # приватного у нас нет. Без ssh_mode оба поля остаются NULL.
    ssh_public_key: str | None = None
    ssh_private_key_encrypted: str | None = None
    generated_private_key: str | None = None
    if payload.ssh_mode == "generate":
        private_pem, public_openssh = _generate_ssh_keypair()
        ssh_public_key = public_openssh
        ssh_private_key_encrypted = secrets_service.encrypt(
            private_pem,
            aad=secrets_service.aad_for_server_account_ssh_key(account_id),
        )
        generated_private_key = private_pem
    elif payload.ssh_mode == "supply":
        ssh_public_key = payload.ssh_public_key

    data = {
        "id": account_id,
        "department_id": department_id,
        "login": payload.login,
        "password_encrypted": encrypted,
        "ssh_public_key": ssh_public_key,
        "ssh_private_key_encrypted": ssh_private_key_encrypted,
        "has_sudo": payload.has_sudo,
        "unix_groups": list(payload.unix_groups),
        "linked_user_id": payload.linked_user_id,
        "shell": payload.shell,
        "home_dir": payload.home_dir,
        # `is_active` берётся из дефолта колонки (True). Поле зарезервировано
        # под disable-аккаунта в будущем — пока не читается в выборках.
        "created_by": identity.user_id,
    }
    try:
        obj = await repo.create(db, data, [s.id for s in servers])
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании аккаунта: %s", type(exc.orig).__name__)
        audit_service.emit(
            "server_account.create",
            target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "duplicate",
                "server_ids": payload.server_ids,
                "login": payload.login,
            },
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account with this login already exists on one of the servers",
            details={"hint": "уникальный ключ (server_id, login) на join-таблице"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.create",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": [s.id for s in servers],
            "login": obj.login,
            "has_sudo": obj.has_sudo,
            "department_id": department_id,
            "ssh_mode": payload.ssh_mode,
        },
    )
    return obj, generated_private_key


async def get_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> tuple[ServerAccount, str | None, str | None]:
    """SELECT по PK + dept-isolation, опционально с расшифрованным паролем.

    Карточка доступна по `view` или `view_password`: держателю `view_password`
    голый `view` не нужен (так worker_bot, у которого только secret-access,
    тоже читает карточку с паролем). Кортеж — `(account, password_b64,
    previous_password_b64)`: при наличии `view_password` оба пароля приходят
    в base64(plaintext), иначе оба `None`. `previous_password_b64` непуст
    только когда идёт переходный период ротации (старый пароль ещё удерживается).
    Раскрытие пароля пишет отдельный CRITICAL-аудит
    `server_account.password_revealed`.
    """
    # Карточка доступна по view ИЛИ view_password (ролевой бланк или
    # per-account грант). Держатель ТОЛЬКО view_password (без view) тоже
    # открывает карточку — поэтому авторизуем по объединению двух действий
    # одним вызовом (без преждевременного denied-аудита на первом промахе).
    account = await _authorize_account_action_any(
        db, identity, account_id, (Action.VIEW, Action.VIEW_PASSWORD),
        "server_account.view",
    )

    has_password_action = await permissions.has_account_action(
        db, identity, account, Action.VIEW_PASSWORD
    )

    # view-success эмитим ПОСЛЕ reveal'а: при сломанном ciphertext'е SIEM
    # иначе видит success+failure на один зов. Симметрично с
    # `ipmi_controller.get_controller`.
    revealed: str | None = None
    previous_revealed: str | None = None
    if has_password_action:
        revealed = await _reveal_account_password(db, account)
        previous_revealed = _reveal_previous_password(account)

    audit_service.emit(
        "server_account.view",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={"login": account.login, "department_id": account.department_id},
    )

    return account, revealed, previous_revealed


async def list_accounts_cursor(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    *,
    limit: int,
    after: str | None,
) -> tuple[list[ServerAccount], str | None, bool]:
    """Keyset-страница аккаунтов сервера. Возвращает `(items, next_cursor, has_more)`.

    `limit + 1` row-fetch — детектит наличие следующей страницы без COUNT'а.
    """
    from src.utils.cursor import (
        decode_cursor,
        encode_cursor,
        normalize_limit,
        parse_cursor_datetime,
    )

    with emit_denied_on_authz_error(
        "server_account.list",
        target_type="server_account",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW
        )
    try:
        await load_visible_server(db, identity, server_id)
    except NotFoundError:
        # Visibility-404: server невидим (cross-dept / нет row), caller прошёл VIEW.
        audit_service.emit(
            "server_account.list",
            target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    page_size = normalize_limit(limit)
    after_created_at = None
    after_id = None
    if after:
        cur = decode_cursor(after)
        after_created_at = parse_cursor_datetime(cur.sort_value)
        after_id = cur.row_id
    rows = await repo.list_for_server_after(
        db,
        server_id,
        limit=page_size + 1,
        after_created_at=after_created_at,
        after_id=after_id,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
    )
    return items, next_cursor, has_more


async def list_accounts(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    limit: int,
    offset: int,
) -> tuple[list[ServerAccount], int]:
    """List + count привязанных к серверу аккаунтов. Cross-dept сервер скрыт за 404."""
    with emit_denied_on_authz_error(
        "server_account.list",
        target_type="server_account",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW
        )
    try:
        await load_visible_server(db, identity, server_id)
    except NotFoundError:
        # Visibility-404: server невидим (cross-dept / нет row), caller прошёл VIEW.
        audit_service.emit(
            "server_account.list",
            target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    items = await repo.list_for_server(db, server_id, limit=limit, offset=offset)
    total = await repo.count_for_server(db, server_id)
    return items, total


async def update_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountUpdate,
) -> tuple[ServerAccount, set[str]]:
    """PATCH-обновление. Пустой диф → возврат без UPDATE.

    `has_sudo=True` дополнительно требует GRANT_SUDO. Изменение пароля
    через PATCH не предусмотрено — только через `/rotate_password`.
    Привязка серверов — через `/servers` под-операции.

    Загрузка аккаунта идёт через `SELECT ... FOR UPDATE OF server_accounts`
    (`_load_account_visible_for_update`). Лок держится до commit'а и
    сериализует PATCH с параллельными `link_servers`/`unlink_servers` —
    fan-out не уходит со смешанным snapshot'ом (stale атрибуты + свежие
    links и наоборот). Read-операции (`get_account`) лок не берут.

    Возвращает `(obj, applied_fields)` — `applied_fields` это набор полей, по
    которым актуальное значение реально отличалось от прежнего и было
    обновлено в БД. Caller использует его, чтобы решать, надо ли пускать
    fanout на серверы (no-op PATCH не должен генерировать `update_on_host`).
    """
    obj = await _authorize_account_action(
        db, identity, account_id, Action.UPDATE, "server_account.update",
        for_update=True,
    )

    raw_changes = payload.model_dump(exclude_unset=True, mode="json")
    if not raw_changes:
        return obj, set()

    # Сравниваем с текущим состоянием — PATCH `{has_sudo: True}` на уже-True
    # аккаунт оседает как no-op, fanout его не должен запускать. Для
    # коллекций сравниваем как множества (порядок групп не значим).
    #
    # `raw_changes = payload.model_dump(mode="json")` — enum'ы/datetime
    # уезжают как строки/iso-8601. На сегодняшней схеме (`has_sudo: bool`,
    # `unix_groups: list[str]`, `shell: str`, `home_dir: str`) сравнение
    # `current != new_value` корректно. При добавлении non-string поля
    # (datetime / enum / decimal) обновить этот comparator — иначе str-vs-
    # native сравнение даст false-positive «изменилось», PATCH станет no-op
    # с лишним fanout'ом.
    def _is_changed(field: str, new_value) -> bool:
        current = getattr(obj, field, None)
        if field == "unix_groups":
            return set(current or []) != set(new_value or [])
        return current != new_value

    changes = {
        field: value
        for field, value in raw_changes.items()
        if _is_changed(field, value)
    }
    if not changes:
        return obj, set()

    # Смена логина — отдельный путь: DB-only rename, допустимый только пока
    # аккаунта физически нет ни на одном сервере. Вынимаем `login` из общего
    # `changes` (его двигает `repo.rename_login`, синхронно с денормализованной
    # копией на связках; обычный `repo.update` тронул бы только строку аккаунта
    # и разъехался с `uq_server_login`).
    new_login = changes.pop("login", None)
    if new_login is not None:
        present_somewhere = any(
            link.present_on_server for link in obj.server_links
        )
        if present_somewhere:
            audit_service.emit(
                "server_account.update",
                target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "login_locked",
                    "old_login": obj.login,
                    "new_login": new_login,
                    "department_id": obj.department_id,
                },
            )
            raise ConflictError(
                error_code="LOGIN_LOCKED",
                message=(
                    "Account is present on at least one server; rename via "
                    "POST /server-accounts/{id}/recreate_login (deprovision → "
                    "rename → provision) instead of PATCH login"
                ),
            )

    # Подъём has_sudo=False→true ИЛИ добавление sudo-дающей группы в unix_groups
    # — эскалация привилегии, требует GRANT_SUDO (роль ИЛИ per-account грант).
    # Снятие флага / снятие такой группы допустимо обычным UPDATE (понижение).
    # Группы меняются под обычным `update`, поэтому без этой проверки учётку
    # можно было закинуть в sudo-группу в обход has_sudo-гейта.
    raising_sudo = changes.get("has_sudo") is True and not obj.has_sudo
    added_sudo_groups: set[str] = set()
    if "unix_groups" in changes:
        added_sudo_groups = _added_sudo_groups(
            list(obj.unix_groups), changes["unix_groups"]
        )
    if (raising_sudo or added_sudo_groups) and not await permissions.has_account_action(
        db, identity, obj, Action.GRANT_SUDO
    ):
        audit_service.emit(
            "server_account.update",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={
                "reason": "grant_sudo_denied",
                "fields": list(changes.keys()),
                "sudo_groups": sorted(added_sudo_groups),
            },
        )
        raise _grant_sudo_denied_error(added_sudo_groups)

    # Набор реально применяемых полей (для аудита и applied_fields-возврата):
    # changes без login + login отдельно, если он меняется.
    applied = set(changes.keys())
    if new_login is not None:
        applied.add("login")
    old_login = obj.login

    try:
        if changes:
            await repo.update(db, obj, changes)
        if new_login is not None:
            await repo.rename_login(db, obj, new_login)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении аккаунта %s: %s", account_id, type(exc.orig).__name__)
        audit_service.emit(
            "server_account.update",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(applied)},
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Update collides with an existing account",
        ) from exc
    await db.refresh(obj)
    details = {
        "fields": list(applied),
        "changed_fields": list(applied),
        "department_id": obj.department_id,
    }
    if new_login is not None:
        details["old_login"] = old_login
        details["new_login"] = new_login
    audit_service.emit(
        "server_account.update",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details=details,
    )
    return obj, applied


# Поля, которые adopt_from_host может принять в БД. Совпадает с
# `_account_attr_drift` в internal_service: ровно те атрибуты, по которым
# инвентаризация считает drift и предлагает оператору `found`-значения.
_ADOPTABLE_FIELDS = ("has_sudo", "unix_groups", "shell")


async def adopt_from_host(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountAdoptRequest,
) -> ServerAccount:
    """Принять факт-состояние OS-пользователя с конкретного хоста в БД.

    Право: `(server_account, *, adopt_from_host)` — отдельный grant
    update-уровня (operator/admin). Оператор инициирует руками, пополевно:
    в payload едут только те из `has_sudo`/`unix_groups`/`shell`, что он
    отметил в drift'е, со значениями = `found` (фактом с бокса).

    Отличие от `update_account`: обновляем ТОЛЬКО БД, fan-out
    `update_on_host` на серверы НЕ запускаем — хост `server_id` уже в этом
    состоянии, а push разнёс бы drift одного сервера на остальные привязки.

    Аккаунт берётся под row-lock (`_load_account_visible_for_update`),
    `server_id` обязан быть привязан к аккаунту (иначе 404). Cross-dept
    аккаунт скрыт за 404. Возвращает обновлённый аккаунт.
    """
    with emit_denied_on_authz_error(
        "server_account.adopted_from_host",
        target_id=account_id,
        target_type="server_account",
        extra_details={"server_id": payload.server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ADOPT_FROM_HOST
        )
    try:
        obj = await _load_account_visible_for_update(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.adopted_from_host",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept", "server_id": payload.server_id},
        )
        raise

    # server_id обязан быть привязан к аккаунту — adopt привязан к факту с
    # конкретного хоста. Непривязанный сервер прячем за тем же 404, что и
    # cross-dept аккаунт (не утекаем, какие серверы держит аккаунт).
    if not await repo.is_linked(db, account_id, payload.server_id):
        audit_service.emit(
            "server_account.adopted_from_host",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "server_not_linked", "server_id": payload.server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )

    raw = payload.model_dump(exclude_unset=True, mode="json")
    raw.pop("server_id", None)
    if not raw:
        # Нечего принимать — ни одно поле не отмечено. Не трогаем БД, но это
        # клиентская ошибка (запрос без полей), а не no-op success.
        raise DomainValidationError(
            error_code="NO_FIELDS_TO_ADOPT",
            message="At least one of has_sudo/unix_groups/shell must be provided",
        )

    def _is_changed(field: str, new_value) -> bool:
        current = getattr(obj, field, None)
        if field == "unix_groups":
            return set(current or []) != set(new_value or [])
        return current != new_value

    # old→new фиксируем по реально изменившимся полям. No-op (оператор принял
    # значение, которое уже в БД) пишем как success без UPDATE — БД уже в
    # целевом состоянии.
    changes: dict = {}
    old_new: dict = {}
    for field in _ADOPTABLE_FIELDS:
        if field not in raw:
            continue
        new_value = raw[field]
        if _is_changed(field, new_value):
            old_new[field] = {
                "old": getattr(obj, field, None),
                "new": new_value,
            }
            changes[field] = new_value

    if changes:
        await repo.update(db, obj, changes)
        await db.commit()
        await db.refresh(obj)

    audit_service.emit(
        "server_account.adopted_from_host",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": payload.server_id,
            "adopted_fields": list(changes.keys()),
            "changes": old_new,
            "department_id": obj.department_id,
        },
    )
    return obj


async def import_from_host(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerAccountImportRequest,
) -> ServerAccount:
    """Импортировать незнакомый OS-пользователь с бокса в БД новым аккаунтом.

    Право: `(server_account, *, create)` — это обычное создание аккаунта,
    только из фактов инвентаризации (`unknown_users`), а не из ручного ввода.
    has_sudo=True дополнительно требует `grant_sudo`, как и в `create_account`.

    Аккаунт сразу привязывается к `server_id` и помечается
    `present_on_server=True` (пользователь уже на боксе). По умолчанию
    `source=discovered` без пароля — на боксе он нам неизвестен. Сервер обязан
    быть в отделе caller'а (иначе 404). Конфликт по (server_id, login) → 409.
    """
    with emit_denied_on_authz_error(
        "server_account.imported_from_host",
        target_type="server_account",
        extra_details={"server_id": payload.server_id, "login": payload.login},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.CREATE
        )

    servers = await _resolve_same_dept_servers(
        db, identity, [payload.server_id], "server_account.imported_from_host"
    )
    server = servers[0]

    # Импорт фиксирует факт-состояние пользователя с бокса, но завести в БД
    # запись с sudo (флаг или привилегированная группа) без `grant_sudo`
    # нельзя — иначе через import можно было бы обойти sudo-гейт.
    sudo_groups = _added_sudo_groups(None, list(payload.unix_groups))
    if payload.has_sudo or sudo_groups:
        if not await permissions.has_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.GRANT_SUDO
        ):
            audit_service.emit(
                "server_account.imported_from_host",
                target_type="server_account",
                status="denied", allowed=False,
                details={
                    "reason": "grant_sudo_denied",
                    "server_id": payload.server_id,
                    "login": payload.login,
                    "sudo_groups": sorted(sudo_groups),
                },
            )
            raise _grant_sudo_denied_error(sudo_groups)

    account_id = new_id()
    data = {
        "id": account_id,
        "department_id": server.department_id,
        "login": payload.login,
        "password_encrypted": None,
        "source": payload.source,
        "has_sudo": payload.has_sudo,
        "unix_groups": list(payload.unix_groups),
        "shell": payload.shell,
        # `is_active` берётся из дефолта колонки (True).
        "created_by": identity.user_id,
    }
    try:
        # `create_discovered` ставит present_on_server=True + свежий
        # last_inventory_at на связке — пользователь уже физически на боксе.
        obj = await repo.create_discovered(db, data, payload.server_id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на импорте аккаунта: %s", type(exc.orig).__name__
        )
        audit_service.emit(
            "server_account.imported_from_host",
            target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "duplicate",
                "server_id": payload.server_id,
                "login": payload.login,
            },
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account with this login already exists on this server",
            details={"hint": "уникальный ключ (server_id, login) на join-таблице"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.imported_from_host",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": payload.server_id,
            "login": obj.login,
            "has_sudo": obj.has_sudo,
            "source": obj.source,
            "department_id": obj.department_id,
        },
    )
    return obj


# ── Ignore-list логинов (скоуп — отдел) ──────────────────────────────────────


async def list_ignored_logins(
    db: AsyncSession,
    identity: IdentityContext,
) -> list[ServerAccountIgnoredLogin]:
    """Список игнор-логинов отдела caller'а.

    Право: `(server_account, *, manage_ignored_logins)`. Скоуп — отдел
    caller'а (dept-изоляция: возвращаются только записи его department_id).
    """
    with emit_denied_on_authz_error(
        "server_account.ignored_logins_listed",
        target_type="server_account",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_IGNORED_LOGINS
        )
    return await ignored_login_repo.list_for_department(db, identity.department_id)


async def add_ignored_login(
    db: AsyncSession,
    identity: IdentityContext,
    payload: IgnoredLoginCreate,
) -> ServerAccountIgnoredLogin:
    """Заигнорить логин в отделе caller'а.

    Право: `(server_account, *, manage_ignored_logins)`. UNIQUE(department_id,
    login) — повторно заигнорить тот же логин → 409. Аудит WARNING.
    """
    with emit_denied_on_authz_error(
        "server_account.ignored_login_added",
        target_type="server_account",
        extra_details={"login": payload.login},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_IGNORED_LOGINS
        )
    data = {
        "id": ignored_login_id(),
        "department_id": identity.department_id,
        "login": payload.login,
        "reason": payload.reason,
        "created_by": identity.user_id,
    }
    try:
        obj = await ignored_login_repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "server_account.ignored_login_added",
            target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "duplicate", "login": payload.login},
        )
        raise ConflictError(
            error_code="IGNORED_LOGIN_DUPLICATE",
            message="This login is already ignored in the department",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.ignored_login_added",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": obj.login,
            "department_id": obj.department_id,
        },
    )
    return obj


async def remove_ignored_login(
    db: AsyncSession,
    identity: IdentityContext,
    login: str,
) -> None:
    """Снять игнор с логина в отделе caller'а.

    Право: `(server_account, *, manage_ignored_logins)`. Если логина нет в
    списке отдела — 404. Аудит INFO.
    """
    with emit_denied_on_authz_error(
        "server_account.ignored_login_removed",
        target_type="server_account",
        extra_details={"login": login},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_IGNORED_LOGINS
        )
    removed = await ignored_login_repo.delete(db, identity.department_id, login)
    if removed == 0:
        audit_service.emit(
            "server_account.ignored_login_removed",
            target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found", "login": login},
        )
        raise NotFoundError(
            error_code="IGNORED_LOGIN_NOT_FOUND",
            message="This login is not in the department ignore-list",
        )
    await db.commit()
    audit_service.emit(
        "server_account.ignored_login_removed",
        target_type="server_account",
        status="success", allowed=True,
        details={"login": login, "department_id": identity.department_id},
    )


async def link_servers(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountServersUpdate,
) -> ServerAccount:
    """Привязать аккаунт к дополнительным серверам.

    Линковка гейтится `update`. Все новые серверы обязаны быть в том же
    department'е, что и аккаунт (cross-dept → 404, как при create). Если
    login уже занят на одном из серверов другим аккаунтом — 409.
    """
    with emit_denied_on_authz_error(
        "server_account.link_servers",
        target_id=account_id,
        target_type="server_account",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.UPDATE
        )
    try:
        obj = await _load_account_visible_for_update(db, identity, account_id)
    except NotFoundError:
        # Visibility-404: account невидим (cross-dept / нет row), permission уже прошёл.
        audit_service.emit(
            "server_account.link_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    await _resolve_same_dept_servers(
        db, identity, payload.server_ids, "server_account.link_servers"
    )

    try:
        await repo.add_servers(db, obj, payload.server_ids)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на линковке аккаунта %s: %s", account_id, type(exc.orig).__name__)
        audit_service.emit(
            "server_account.link_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "duplicate", "server_ids": payload.server_ids},
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account login already exists on one of the target servers",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.link_servers",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": payload.server_ids,
            "department_id": obj.department_id,
        },
    )
    return obj


async def unlink_servers(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountServersUpdate,
) -> ServerAccount:
    """Отвязать аккаунт от серверов.

    Отвязка гейтится `update`. Нельзя снять последнюю связку — аккаунт всегда
    живёт хотя бы на одном сервере (иначе → 409 ACCOUNT_NO_SERVERS).
    """
    with emit_denied_on_authz_error(
        "server_account.unlink_servers",
        target_id=account_id,
        target_type="server_account",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.UPDATE
        )
    try:
        obj = await _load_account_visible_for_update(db, identity, account_id)
    except NotFoundError:
        # Visibility-404: account невидим (cross-dept / нет row), permission уже прошёл.
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    # Берём FOR UPDATE на M2M-строках аккаунта — сериализует с параллельными
    # правками связок (link_servers, link inventory). selectin-загруженный
    # `obj.server_links` мог отстать от БД, под лок'ом перечитываем live-state.
    live_links = await repo.lock_links_for_account(db, account_id)
    current = {link.server_id for link in live_links}
    unknown = [sid for sid in payload.server_ids if sid not in current]
    if unknown:
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "server_not_linked",
                "server_ids": payload.server_ids,
                "unknown_server_ids": unknown,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_SERVER_LINK_NOT_FOUND",
            message="Account is not linked to one or more of the requested servers",
            details={"unknown_server_ids": unknown},
        )
    remaining = current - set(payload.server_ids)
    if not remaining:
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "would_orphan_account", "server_ids": payload.server_ids},
        )
        raise ConflictError(
            error_code="ACCOUNT_NO_SERVERS",
            message="Cannot unlink the last server — account must stay on at least one",
        )

    # `remove_servers` идёт по selectin-загруженному `obj.server_links`. После
    # `lock_links_for_account` session-кэш может расходиться с live-state —
    # обновляем relationship, чтобы DELETE'ил по тому же набору, что и
    # snapshot-check выше.
    await db.refresh(obj, attribute_names=["server_links"])
    removed = await repo.remove_servers(db, obj, payload.server_ids)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server_account.unlink_servers",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": payload.server_ids,
            "removed": removed,
            "department_id": obj.department_id,
        },
    )
    return obj


async def delete_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> None:
    """Hard-delete аккаунта (связки уходят каскадом)."""
    obj = await _authorize_account_action(
        db, identity, account_id, Action.DELETE, "server_account.delete",
    )
    login = obj.login
    server_ids = repo.linked_server_ids(obj)
    department_id = obj.department_id
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "server_account.delete",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": server_ids,
            "login": login,
            "department_id": department_id,
        },
    )


async def rotate_password(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    new_password: str | None = None,
) -> ServerAccount:
    """User-инициированная ротация общего пароля. Новый пароль НЕ возвращается клиенту.

    Пароль общий на все привязанные серверы — эта ручка меняет ciphertext в
    БД без SSH-apply'я. Для apply'я на конкретный сервер или на все — см.
    worker-dispatch (`/rotate` точечный/массовый).

    Старый пароль не теряется: текущий ciphertext переезжает в
    `previous_password_encrypted` и удерживается, пока новый пароль не доедет
    хотя бы до одного сервера (provision-callback снимает
    `credentials_pending_apply` и заодно зануляет previous). На время
    переходного периода оператор может подключаться и старым, и новым паролем.

    Если `new_password` передан — он уже прошёл парольную политику на схеме
    (`ServerAccountRotateRequest`) и сохраняется как есть. Если нет —
    генерируем серверной стороной (`secrets.token_urlsafe(32)`).
    """
    obj = await _authorize_account_action(
        db, identity, account_id, Action.ROTATE_PASSWORD,
        "server_account.rotate_password",
    )
    plaintext = new_password if new_password is not None else _generate_password()
    encrypted = secrets_service.encrypt(
        plaintext,
        aad=secrets_service.aad_for_server_account_password(obj.id),
    )
    updated = await repo.update_password(db, obj, encrypted)
    await db.commit()
    await db.refresh(updated)
    audit_service.emit(
        "server_account.rotate_password",
        target_id=updated.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": updated.login,
            "department_id": updated.department_id,
            "reason": "user_provided" if new_password is not None else "user_initiated",
            "rotated_at": updated.password_rotated_at.isoformat() if updated.password_rotated_at else None,
        },
    )
    return updated


def _is_account_recreate_admin(
    identity: IdentityContext, account: ServerAccount
) -> bool:
    """True, если caller вправе пересоздать логин аккаунта.

    Контракт: только platform `department_admin` своего отдела ИЛИ носитель
    service-роли `admin` в server_service. Обычный operator/update-грант сюда
    не проходит — recreate сносит и заводит OS-пользователя заново, это
    разрушительнее обычного update'а.
    """
    if (
        identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and identity.department_id is not None
        and identity.department_id == account.department_id
    ):
        return True
    return ServiceRole.ADMIN in identity.roles_for_service(SERVICE_NAME)


async def authorize_recreate_login(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """Загрузить аккаунт под row-lock + авторизовать recreate_login.

    Доступ — dep_admin отдела аккаунта или service-admin (см.
    `_is_account_recreate_admin`). Невидимый / чужой / отсутствующий аккаунт
    скрыт за 404 для держателя доступа; без доступа — 403 без раскрытия
    существования (как у `_authorize_account_action`).
    """
    account = await repo.get_for_update(db, account_id)
    visible = account is not None and account.department_id == identity.department_id

    if account is not None and _is_account_recreate_admin(identity, account):
        if not visible:
            audit_service.emit(
                "server_account.recreate_login",
                target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found"
            )
        return account

    details: dict = {"reason": "permission_denied"}
    if identity.subject_type is not None:
        details.setdefault("subject_type", identity.subject_type)
    audit_service.emit(
        "server_account.recreate_login",
        target_id=account_id, target_type="server_account",
        status="denied", allowed=False,
        details=details,
    )
    raise AuthorizationError(
        error_code="PERMISSION_DENIED",
        message="Only department admin or service admin can recreate an account login",
        details={"entity_type": "server_account", "action": "recreate_login"},
    )


async def rename_login_in_db(
    db: AsyncSession,
    account: ServerAccount,
    new_login: str,
) -> ServerAccount:
    """Переименовать логин в БД (+ денормализованные копии на связках).

    Вызывается оркестрацией recreate_login между deprovision'ом и provision'ом.
    Конфликт по (server_id, login) → 409 ACCOUNT_DUPLICATE. commit — на caller'е
    (он держит её в одной транзакции с остальными мутациями).
    """
    try:
        await repo.rename_login(db, account, new_login)
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на переименовании логина %s: %s",
            account.id, type(exc.orig).__name__,
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="New login already exists on one of the linked servers",
            details={"hint": "уникальный ключ (server_id, login) на join-таблице"},
        ) from exc
    return account


def audit_recreate_login(account: ServerAccount, result: dict) -> None:
    """CRITICAL-аудит успешной оркестрации recreate_login.

    Зовётся endpoint'ом после deprovision → rename → provision. Сводит в
    details состав диспатчей: число снесённых/заведённых серверов и пропуски.
    """
    audit_service.emit(
        "server_account.recreate_login",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "old_login": result["old_login"],
            "new_login": result["new_login"],
            "deprovision_count": len(result["deprovision"]),
            "provision_count": len(result["provision"]),
            "skipped": result["skipped"],
            "department_id": account.department_id,
        },
    )


async def set_ssh_key(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    *,
    ssh_mode: str,
    ssh_public_key: str | None,
) -> tuple[ServerAccount, str, str | None]:
    """Задать/заменить SSH-ключ аккаунта в БД (без fan-out — его делает endpoint).

    Гейт — `update` (роль ИЛИ per-account грант). `generate` — Ed25519, храним
    public + зашифрованный private, возвращаем приватный один раз; `supply` —
    сохраняем переданный public, зашифрованный private сбрасываем (его у нас нет).

    Возвращает `(account, ssh_public_key, private_pem_or_None)`. Приватный
    ключ непустой только при `generate`.
    """
    obj = await _authorize_account_action(
        db, identity, account_id, Action.UPDATE, "server_account.ssh_key_set",
        for_update=True,
    )
    private_pem: str | None = None
    if ssh_mode == "generate":
        private_pem, public_openssh = _generate_ssh_keypair()
        encrypted = secrets_service.encrypt(
            private_pem,
            aad=secrets_service.aad_for_server_account_ssh_key(obj.id),
        )
        await repo.update_ssh_key(
            db, obj, ssh_public_key=public_openssh, ssh_private_key_encrypted=encrypted
        )
    else:  # supply — public_key уже провалидирован схемой
        public_openssh = ssh_public_key  # type: ignore[assignment]
        await repo.update_ssh_key(
            db, obj, ssh_public_key=public_openssh, ssh_private_key_encrypted=None
        )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server_account.ssh_key_set",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": obj.login,
            "ssh_mode": ssh_mode,
            "department_id": obj.department_id,
        },
    )
    return obj, public_openssh, private_pem


async def rotate_ssh_key(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> tuple[ServerAccount, str, str]:
    """Перегенерить Ed25519-ключ аккаунта (кейс компрометации) — без fan-out.

    Гейт — `update`. Всегда генерит новую пару, сохраняет public + зашифрованный
    private, возвращает новый приватный один раз. Аудит CRITICAL (ротация
    секрета).
    """
    obj = await _authorize_account_action(
        db, identity, account_id, Action.UPDATE, "server_account.ssh_key_rotate",
        for_update=True,
    )
    private_pem, public_openssh = _generate_ssh_keypair()
    encrypted = secrets_service.encrypt(
        private_pem,
        aad=secrets_service.aad_for_server_account_ssh_key(obj.id),
    )
    await repo.update_ssh_key(
        db, obj, ssh_public_key=public_openssh, ssh_private_key_encrypted=encrypted
    )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server_account.ssh_key_rotate",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": obj.login,
            "department_id": obj.department_id,
        },
    )
    return obj, public_openssh, private_pem


async def _reveal_account_password(
    db: AsyncSession, account: ServerAccount,
) -> str | None:
    """Расшифровать пароль аккаунта в base64 и записать аудит раскрытия.

    Вызывается из `get_account` только после успешной проверки `view_password`,
    поэтому permission тут уже не проверяется. Возвращает `None`, если у
    аккаунта нет сохранённого пароля (карточка всё равно отдаётся без пароля).

    Аудит раскрытия пишется в двух режимах per (actor, account):

    * первый успех в окне `password_reveal_audit_window_seconds` →
      CRITICAL `server_account.password_revealed`;
    * повторы в окне → INFO `server_account.password_revealed_throttled`.

    Это не даёт UI-tooltip'у с автообновлением каждые N секунд забивать
    SIEM CRITICAL'ом на один и тот же reveal. Failure-ветки (no_password /
    decrypt_failed) всегда пишут CRITICAL — это не штатный refresh.
    Window=0 отключает throttle.

    Сломанный ciphertext поднимает `DECRYPT_FAILED` (422 — input-error со
    стороны secrets_service: битый AEAD-формат) + failure-аудит.
    Непредвиденный сбой crypto-стека оборачивается в `DECRYPT_FAILED` (500)
    исключением ниже в `except Exception` (см. wrapper-комментарий).
    """
    actor_id = audit_context.get_context().actor_id

    if account.password_encrypted is None:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "no_password_stored",
                "department_id": account.department_id,
            },
        )
        return None

    aad = secrets_service.aad_for_server_account_password(account.id)
    old_blob = account.password_encrypted
    try:
        result = secrets_service.decrypt_with_meta(old_blob, aad=aad)
    except AppException:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "department_id": account.department_id,
            },
        )
        raise
    except Exception as exc:
        # secrets_service.decrypt сам поднимает AppException(DECRYPT_FAILED),
        # но если в crypto-pipeline'е всплывёт что-то нестандартное (например,
        # ASYNC/threadpool wrapper уронит RuntimeError), голый exception
        # утечёт наружу как 500 без error_code. Оборачиваем под тот же ключ,
        # чтобы FastAPI handler вернул стандартный envelope.
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "department_id": account.department_id,
            },
        )
        raise AppException(
            error_code="DECRYPT_FAILED",
            message=f"Failed to decrypt account password: {type(exc).__name__}",
            http_status=500,
        ) from exc
    plain = result.plaintext
    if result.needs_reencrypt:
        # Lazy миграция под активный ключ. Не блокирует ответ: при любых
        # ошибках обновления (CAS-проигрыш, БД-lock, encrypt-фейл) reveal
        # всё равно отдаёт корректный plaintext, а outbox-flow дочистит
        # остальное.
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id=account.id,
            old_blob=old_blob,
            plaintext=plain,
            aad=aad,
        )

    should_emit_critical, total_in_window = _record_reveal_attempt(actor_id, account.id)
    if should_emit_critical:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="success", allowed=True,
            details={
                "login": account.login,
                "department_id": account.department_id,
                "total_reveals_in_window": total_in_window,
            },
        )
    else:
        audit_service.emit(
            "server_account.password_revealed_throttled",
            target_id=account.id, target_type="server_account",
            status="success", allowed=True,
            details={
                "login": account.login,
                "department_id": account.department_id,
                "window_seconds": get_settings().password_reveal_audit_window_seconds,
                "total_reveals_in_window": total_in_window,
            },
        )
    return base64.b64encode(plain.encode()).decode("ascii")


def _reveal_previous_password(account: ServerAccount) -> str | None:
    """Расшифровать удержанный прежний пароль в base64 (или `None`).

    Зовётся из `get_account` только после успешной проверки `view_password`,
    рядом с раскрытием текущего пароля. Отдельного аудита не пишет: reveal уже
    зафиксирован для текущего пароля тем же вызовом, а previous — лишь
    дополнительное поле той же карточки.

    Прежний пароль шифровался тем же `secrets_service.encrypt` и тем же AAD,
    что и `password_encrypted` (AAD привязан к id строки, не к колонке) — при
    ротации ciphertext просто переехал между колонками. Сломанный ciphertext
    здесь не должен валить всю карточку: переходное поле менее критично, чем
    текущий пароль, поэтому при ошибке расшифровки возвращаем `None`, а не 500.
    """
    if account.previous_password_encrypted is None:
        return None
    aad = secrets_service.aad_for_server_account_password(account.id)
    try:
        plain = secrets_service.decrypt(account.previous_password_encrypted, aad=aad)
    except AppException:
        return None
    return base64.b64encode(plain.encode()).decode("ascii")


async def resolve_bootstrap_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    server,
) -> dict[str, str | None]:
    """Расшифровать креды привязанного аккаунта для bootstrap-режима prepare.

    Контракт «выбрать аккаунт вместо ручного ввода»: caller указывает
    `account_id`, server_service сам достаёт логин/пароль (+ приватный SSH-ключ,
    если он сохранён) и кладёт их в Redis как bootstrap — UI пароль не шлёт.

    Проверки (в порядке безопасности):

    * `view_password` на SERVER_ACCOUNT — то же право, что у reveal'а пароля;
      кто может посмотреть пароль, тот может им забутстрапить;
    * аккаунт виден (dept-isolation, иначе 404 ACCOUNT_NOT_FOUND);
    * аккаунт привязан к этому серверу (иначе 404 ACCOUNT_NOT_LINKED);
    * у аккаунта есть сохранённый пароль (иначе 409 ACCOUNT_HAS_NO_PASSWORD).

    Возвращает `{"login", "password", "ssh_private_key"}` (ssh_private_key —
    None, если у аккаунта ключа нет). Эти значения кладёт в Redis-stash
    вызывающий endpoint; в task-payload едет только ссылка.
    """
    account = await _load_account_visible(db, identity, account_id)
    if not await permissions.has_account_action(
        db, identity, account, Action.VIEW_PASSWORD
    ):
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="No view_password access to this server account",
            details={"entity_type": "server_account", "action": "view_password"},
        )
    if server.id not in repo.linked_server_ids(account):
        raise NotFoundError(
            error_code="ACCOUNT_NOT_LINKED",
            message="Server account is not linked to this server",
        )
    if account.password_encrypted is None:
        raise ConflictError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message=(
                "Selected account has no stored password; rotate it first or "
                "use manual bootstrap credentials"
            ),
        )
    password = secrets_service.decrypt(
        account.password_encrypted,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    ssh_private_key: str | None = None
    if account.ssh_private_key_encrypted is not None:
        ssh_private_key = secrets_service.decrypt(
            account.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(account.id),
        )
    audit_service.emit(
        "server_account.bootstrap_resolved",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": account.login,
            "server_id": server.id,
            "department_id": account.department_id,
            "has_ssh_private_key": ssh_private_key is not None,
        },
    )
    return {
        "login": account.login,
        "password": password,
        "ssh_private_key": ssh_private_key,
    }


async def resolve_console_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    server,
    *,
    allow_via_server_console: bool = False,
) -> dict[str, str | None]:
    """Расшифровать креды учётки для интерактивной консоли.

    Отличие от `resolve_bootstrap_credentials`: «подключаться к консоли» —
    отдельное право учётки. Проходит, если у caller'а есть per-account грант
    `console` на эту учётку ЛИБО доступ `view_password` (роль или грант —
    кто видит пароль, тот и так может им подключиться). Это делает консоль
    доступной без бланкетного view_password при наличии узкого console-гранта.

    `allow_via_server_console=True` — caller уже держит ролевой
    `(server, console)`; он проходит без отдельного права на учётке (сохраняем
    существующие серверные console-гранты после расширения гейта на
    `view_password`).

    Остальные проверки (видимость учётки, привязка к серверу, наличие пароля)
    и форма результата — те же, что у bootstrap-резолва.
    """
    account = await _load_account_visible(db, identity, account_id)
    allowed = (
        allow_via_server_console
        or await permissions.has_account_action(
            db, identity, account, Action.CONSOLE,
        )
        or await permissions.has_account_action(
            db, identity, account, Action.VIEW_PASSWORD,
        )
    )
    if not allowed:
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="No console access to this server account",
            details={"entity_type": "server_account", "action": "console"},
        )
    if server.id not in repo.linked_server_ids(account):
        raise NotFoundError(
            error_code="ACCOUNT_NOT_LINKED",
            message="Server account is not linked to this server",
        )
    if account.password_encrypted is None:
        raise ConflictError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message=(
                "Selected account has no stored password; rotate it first or "
                "use manual bootstrap credentials"
            ),
        )
    password = secrets_service.decrypt(
        account.password_encrypted,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    ssh_private_key: str | None = None
    if account.ssh_private_key_encrypted is not None:
        ssh_private_key = secrets_service.decrypt(
            account.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(account.id),
        )
    audit_service.emit(
        "server_account.bootstrap_resolved",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": account.login,
            "server_id": server.id,
            "department_id": account.department_id,
            "has_ssh_private_key": ssh_private_key is not None,
            "via": "console",
        },
    )
    return {
        "login": account.login,
        "password": password,
        "ssh_private_key": ssh_private_key,
    }
