"""Инвентаризация OS-пользователей сервера по SSH.

Параллель `inventory.sync`, но цель — пользователи, а не железо. Поток:

  1. если в payload есть `account_id` — тянем пароль аккаунта через
     server_service, иначе дефолтный `ssh_login` (`root`);
  2. `ssh_client.collect_os_users` снимает `getent passwd` / `getent group` /
     `/etc/login.defs`;
  3. `os_users_facts_to_payload` фильтрует системных по `UID_MIN` и собирает
     flat-список под `UsersInventoryCallbackRequest`;
  4. `submit_users_inventory` POST'ит его обратно — server_service reconcile'ит.

Submit-фейл (network / 4xx / 5xx) НЕ роняет task'у в FAILED: список юзеров
остаётся в `task.result`, а submit-fail идёт в audit как
`submit_status=submit_failed:<code>`.
"""

import logging

from src.core.exceptions import CredentialFetchError
from src.db.session import AsyncSessionLocal
from src.main import broker
from src.repositories import task as task_repo
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)


async def _account_creds(
    payload: dict, server_id: str, account_id: str, target_dept: str | None,
) -> dict:
    """Собрать credentials для привилегированной операции над аккаунтом.

    На не управляемом сервере сессия идёт под самим аккаунтом по паролю, поэтому
    пароль обязателен — тянем его из server_service, при отказе задача падает
    (без пароля на SSH не зайти).

    На управляемом сервере аутентификация по ключу под управляющим
    пользователем, пароль аккаунта для входа не нужен. `login` берём из payload
    (его кладёт server_service для provision/update/deprovision), пароль не
    запрашиваем — у discovered-аккаунта его может не быть вовсе.
    """
    login = payload.get("login")
    if payload.get("is_managed") and login:
        creds = {"login": login}
    else:
        # Self-сессия (или managed без login в payload — fallback на старое
        # поведение): пароль обязателен для входа под аккаунтом.
        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
    ssh_client.apply_session_hints(creds, payload)
    return creds


async def _fetch_password_to_set(
    server_id: str, account_id: str, target_dept: str | None,
) -> str | None:
    """Best-effort пароль аккаунта, чтобы выставить его на боксе (managed).

    На управляемом сервере вход по ключу, пароль нужен только чтобы прописать
    его пользователю. У discovered-аккаунта пароля нет — server_service вернёт
    `ACCOUNT_PASSWORD_UNAVAILABLE`, тогда возвращаем `None` и шаг chpasswd
    пропускается. Транспортная ошибка пробрасывается (это уже не штатное
    «пароля нет», а недоступность сервиса).
    """
    try:
        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
    except CredentialFetchError as exc:
        if exc.error_code == "ACCOUNT_PASSWORD_UNAVAILABLE":
            logger.info(
                "managed provision server_id=%s account_id=%s: no stored "
                "password, skipping chpasswd",
                server_id,
                account_id,
            )
            return None
        raise
    return creds.get("password")


# Whitelist для audit details.result. Сами логины/группы/home — потенциально
# чувствительный inventory, в audit кладём только счётчики и server_id.
# Полный список доступен админу через `Task.result`.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "user_count", "submit_status"}


@broker.task("users.inventory")
async def users_inventory(task_id: str) -> None:
    """Снять список OS-пользователей сервера по SSH и сдать в server_service.

    Параметры: `task_id`. Payload — `server_id`, опционально `account_id`,
    `ssh_login`, `target_department_id`.

    Возвращает: `{server_id, user_count, submit_status}` — в audit уходят
    только эти поля (см. AUDIT_SAFE_FIELDS), сам список юзеров — нет.

    Возможные ошибки: `CredentialFetchError` (если `account_id` задан, но
    server_service не отдал пароль), ошибки SSH-клиента.

    Связано с: `server_account.users_inventory` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload.get("account_id")
        target_dept = payload.get("target_department_id")
        is_managed = bool(payload.get("is_managed"))

        # На управляемом сервере сессия идёт по ключу под управляющим
        # пользователем — пароль аккаунта не нужен (его может и не быть у
        # discovered-аккаунта). Тянем только когда сессия пойдёт под аккаунтом.
        if account_id and not is_managed:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        else:
            creds = {"login": payload.get("ssh_login", "root")}
        ssh_client.apply_session_hints(creds, payload)

        facts = await ssh_client.collect_os_users(creds, server_id)
        users_payload = ssh_client.os_users_facts_to_payload(facts)
        user_count = len(users_payload.get("users", []))

        submit_status: str
        try:
            await server_service_client.submit_users_inventory(
                server_id, users_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "users inventory submit failed server_id=%s error_code=%s; "
                "result kept in task.result",
                server_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        return {
            "server_id": server_id,
            "users": users_payload.get("users", []),
            "user_count": user_count,
            "submit_status": submit_status,
        }

    await run_task(
        task_id,
        audit_action="server_account.users_inventory",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


# Whitelist для audit details.result у provision/update/deprovision. Login и
# server_id — не секрет; пароль и состав групп в audit не уходят.
AUDIT_SAFE_FIELDS_PROVISION: set[str] = {
    "server_id", "account_id", "operation", "present_on_server",
}


@broker.task("account.provision")
async def account_provision(task_id: str) -> None:
    """Завести OS-пользователя на сервере (`useradd`) и подтвердить статус.

    Поток: собираем сессию (`_account_creds`) → `ssh_client.provision_user`
    (useradd + опц. chpasswd, groups/sudo/shell/home из payload) →
    `submit_provision_status(present=True)`.

    На управляемом сервере вход по ключу: пароль для аутентификации не нужен,
    `login` берём из payload. Пароль тянем best-effort только чтобы выставить
    его пользователю; у discovered-аккаунта пароля нет — заводим без смены
    пароля. На не управляемом сервере пароль обязателен (self-сессия).

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    `has_sudo`, `unix_groups`, `shell`, `home_dir`, опц. `target_department_id`,
    `is_managed`, `management_user`.

    Idempotent: уже существующий пользователь синхронизируется, не падает.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.provision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        # F23-B: discovered-сценарий — server_service кладёт сгенерированные
        # креды (`password_plaintext` + `ssh_public_key` + `force_replace`)
        # прямо в payload. Тот же канал используется для re-provision после
        # переустановки ОС (`force_replace=True`). Если поля есть — берём
        # их без отдельного fetch'а internal-ручки.
        inline_password = payload.get("password_plaintext")
        inline_public_key = payload.get("ssh_public_key")
        force_replace = bool(payload.get("force_replace"))

        creds = await _account_creds(payload, server_id, account_id, target_dept)
        # На управляемом сервере пароль для входа не нужен (ключ), но если у
        # аккаунта есть хранимый пароль — ставим его на боксе. Тянем best-effort:
        # discovered-аккаунт без пароля заводим без смены пароля, не падаем.
        new_password = inline_password or creds.get("password")
        if payload.get("is_managed") and new_password is None:
            new_password = await _fetch_password_to_set(
                server_id, account_id, target_dept,
            )
        await ssh_client.provision_user(
            creds, server_id,
            login=creds["login"],
            new_password=new_password,
            groups=payload.get("unix_groups") or [],
            has_sudo=bool(payload.get("has_sudo")),
            shell=payload.get("shell"),
            home_dir=payload.get("home_dir"),
            public_key=inline_public_key,
            force_replace=force_replace,
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "provision", True, target_dept,
        )
        # Defense-in-depth: inline-креды из discovered-сценария (F23-B) —
        # `password_plaintext` и `ssh_private_key_plaintext` — приходят
        # прямо в payload и без явной зачистки остаются в `tasks.payload`
        # до retention cleanup'а (до 30 дней). После успешного callback'а
        # они уже не нужны: задача SUCCEEDED, retry не понадобится. Стираем
        # их из строки, чтобы оператор с SELECT на worker.tasks не получил
        # plaintext password и private SSH key. `ssh_public_key` — не секрет,
        # его не трогаем (полезно для форенсики). Best-effort: если scrub
        # упал, главную транзакцию не валим — task уже завершилась успешно.
        try:
            async with AsyncSessionLocal() as scrub_session:
                await task_repo.scrub_payload_keys(
                    scrub_session, task_id,
                    ["password_plaintext", "ssh_private_key_plaintext"],
                )
                await scrub_session.commit()
        except Exception:  # noqa: BLE001
            logger.warning(
                "failed to scrub inline provision creds from payload",
                exc_info=True,
                extra={"task_id": task_id},
            )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "provision",
            "present_on_server": True,
        }

    await run_task(
        task_id,
        audit_action="server_account.provision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.update_on_host")
async def account_update_on_host(task_id: str) -> None:
    """Синхронизировать атрибуты OS-пользователя на сервере (`usermod`).

    Поток: собираем сессию (`_account_creds`) → `ssh_client.modify_user`
    (usermod groups/sudo/shell) → `submit_provision_status(present=True)`.
    Пароль не меняется, поэтому на управляемом сервере он не запрашивается
    вовсе — `login` берётся из payload.

    Параметры/payload — как у `account_provision`.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.update_on_host` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        creds = await _account_creds(payload, server_id, account_id, target_dept)
        await ssh_client.modify_user(
            creds, server_id,
            login=creds["login"],
            groups=payload.get("unix_groups") or [],
            has_sudo=bool(payload.get("has_sudo")),
            shell=payload.get("shell"),
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "update", True, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "update",
            "present_on_server": True,
        }

    await run_task(
        task_id,
        audit_action="server_account.update_on_host",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.deprovision")
async def account_deprovision(task_id: str) -> None:
    """Удалить OS-пользователя с сервера (`userdel`) и подтвердить статус.

    Поток: собираем сессию (`_account_creds`) → `ssh_client.delete_user`
    (userdel, опц. --remove) → `submit_provision_status(present=False)`.
    Пароль не меняется — на управляемом сервере он не запрашивается, `login`
    берётся из payload.

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    опц. `remove_home`, `target_department_id`.

    Idempotent: отсутствующий пользователь — не ошибка.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.deprovision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")
        remove_home = bool(payload.get("remove_home"))

        creds = await _account_creds(payload, server_id, account_id, target_dept)
        await ssh_client.delete_user(
            creds, server_id, login=creds["login"], remove_home=remove_home,
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "deprovision", False, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "deprovision",
            "present_on_server": False,
        }

    await run_task(
        task_id,
        audit_action="server_account.deprovision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )
