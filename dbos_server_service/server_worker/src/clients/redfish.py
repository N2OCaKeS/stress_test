"""Async Redfish-клиент для iDRAC/iLO/BMC.

Контракт целенаправленно узкий — только методы, нужные `power.*` и
`ipmi.rotate_password` handler'ам.

Архитектура:

  * Один `httpx.AsyncClient` инстанс на экземпляр `RedfishClient` —
    keep-alive на multiple roundtrip'ах к одному BMC (power_status →
    power_on → power_status).
  * Module-level singleton НЕ используется: clients per-host
    (caller'у дешевле получить новый client с host-specific
    Basic Auth, чем шарить pool и менять auth-headers per call).
  * `async with RedfishClient(...) as client:` рекомендуется — закрывает
    underlying connection pool. Без context manager pool остаётся
    подвешенным до GC.

Self-signed сертификаты iDRAC — стандарт; default `verify_tls=False`,
но override-able через env (`REDFISH_VERIFY_TLS=true` в production
с корпоративным CA).

Безопасность:

  * Basic Auth: `httpx` сам кладёт `Authorization: Basic <b64>` и не
    светит plaintext в repr ошибок. Дополнительно перед raise каждое
    исключение прогоняет URL через `_sanitize_url` — на случай если
    `httpx.HTTPStatusError` зашьёт URL с креды (теоретически возможно
    при `auth=` через URL, у нас не используется, но defence-in-depth).
  * Plaintext пароля из `rotate_user_password` уходит в body PATCH-
    запроса — не логируется и не возвращается caller'у.
"""

from __future__ import annotations

import json
import logging
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

# Литерал ResetType — Redfish ComputerSystem.Reset Actions.
PowerAction = Literal[
    "On",
    "ForceOff",
    "GracefulShutdown",
    "GracefulRestart",
    "PowerCycle",
    "ForceRestart",
    "Nmi",
]


# Per-kind manager-id маппинг. iDRAC использует embedded slot, HP iLO
# держит manager под номером `1`. Остальные kind (ipmi / redfish) оставляют
# manager_id="" — клиент сделает discovery через коллекцию /Managers.
KIND_MANAGER_IDS: dict[str, str] = {
    "idrac": "iDRAC.Embedded.1",
    "ilo": "1",
}

# Дефолтный путь к Manager-ресурсу. У Dell-моделей это `iDRAC.Embedded.1`,
# у HPE iLO — `1`. Caller может передать override. Берём из `KIND_MANAGER_IDS`,
# чтобы не было дублирующегося литерала на правку при добавлении новых iDRAC.
_DEFAULT_MANAGER_ID = KIND_MANAGER_IDS["idrac"]

# Per-kind System-id маппинг. Dell iDRAC держит ComputerSystem под
# `System.Embedded.1`, HPE iLO — под `1`. Без этого хардкод `Systems/1`
# на iDRAC отдаёт 404 (там нет системы с id `1`). Остальные kind оставляют
# system_id="" — клиент сделает discovery через коллекцию /Systems.
KIND_SYSTEM_IDS: dict[str, str] = {
    "idrac": "System.Embedded.1",
    "ilo": "1",
}

# Дефолтный System ID для клиента, поднятого без kind (legacy-payload без
# поля). `1` — наиболее переносимый id для one-node-серверов; iDRAC, у
# которого система лежит под `System.Embedded.1`, приходит с kind="idrac"
# и резолвится через KIND_SYSTEM_IDS в get_bmc_client.
_DEFAULT_SYSTEM_ID = "1"


def resolve_manager_id(kind: str | None) -> str:
    """Подобрать Manager-id Redfish-path по типу BMC (`kind`).

    Пустая строка ('') для generic-kind (ipmi / redfish) или неизвестного
    значения сигнализирует клиенту, что нужно сделать discovery через
    коллекцию `/redfish/v1/Managers` перед использованием Manager-эндпоинтов.
    """
    if not kind:
        return ""
    return KIND_MANAGER_IDS.get(kind, "")


def resolve_system_id(kind: str | None) -> str:
    """Подобрать System-id Redfish-path по типу BMC (`kind`).

    Симметрично `resolve_manager_id`: пустая строка ('') для generic-kind
    (ipmi / redfish) или неизвестного значения сигнализирует клиенту, что
    нужно сделать discovery через коллекцию `/redfish/v1/Systems` перед
    обращением к ComputerSystem-эндпоинтам.
    """
    if not kind:
        return ""
    return KIND_SYSTEM_IDS.get(kind, "")


class RedfishError(Exception):
    """Сводная ошибка Redfish-вызова.

    `status_code` — HTTP-код ответа (None для transport-error'а).
    `message` — короткое описание для логов / audit details.
    `redfish_error_code` — `error.code` из ExtendedInfo (например
    `Base.1.0.PropertyValueNotInList`); None если ответ не содержал
    стандартный Redfish error-body.
    """

    def __init__(
        self,
        status_code: int | None,
        message: str,
        redfish_error_code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.redfish_error_code = redfish_error_code
        super().__init__(self._format())

    def _format(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.redfish_error_code:
            parts.append(f"redfish_code={self.redfish_error_code}")
        return " | ".join(parts)


def _sanitize_url(url: str) -> str:
    """Убрать userinfo (`user:pass@`) из URL перед записью в исключение.

    `httpx` сам по себе credentials в URL не использует (мы передаём
    `auth=(u, p)`), но при пропуске чего-то вроде
    `RedfishClient(host="https://root:Calvin@idrac")` urlsplit вернёт
    userinfo, и repr исключения утечёт в логи. Превращаем в
    `https://<USER>:<PASSWORD>@idrac/...`.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.username is None and parts.password is None:
        return url
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    if parts.username or parts.password:
        netloc = f"<USER>:<PASSWORD>@{netloc}"
    return urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
    )


def _extract_redfish_error(body_text: str) -> tuple[str | None, str | None]:
    """Распарсить Redfish error-body, вернуть `(code, human_message)`.

    Redfish-стандарт: `{"error": {"code": "...", "message": "...",
    "@Message.ExtendedInfo": [{"MessageId": "...", "Message": "..."}, ...]}}`.

    Берём `error.code` (top-level) как машинно-читаемый код и первый
    `ExtendedInfo[0].Message` (или `error.message`) как human-readable.
    Если парс провалился — возвращаем (None, None), caller использует
    голый HTTP-код.
    """
    if not body_text:
        return None, None
    try:
        data = json.loads(body_text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    err = data.get("error")
    if not isinstance(err, dict):
        return None, None
    code = err.get("code") if isinstance(err.get("code"), str) else None
    # Сначала ExtendedInfo[0].Message — Dell кладёт сюда конкретику.
    msg: str | None = None
    ext_info = err.get("@Message.ExtendedInfo")
    if isinstance(ext_info, list) and ext_info:
        first = ext_info[0]
        if isinstance(first, dict):
            m = first.get("Message")
            if isinstance(m, str):
                msg = m
    if msg is None:
        top_msg = err.get("message")
        if isinstance(top_msg, str):
            msg = top_msg
    return code, msg


class RedfishClient:
    """Async Redfish-клиент к одному BMC.

    Параметры конструктора:

      * `host` — base URL контроллера (`https://idrac.example.com`).
        Trailing slash trimming делается автоматически.
      * `username`/`password` — Basic Auth-пара (root/Calvin для Dell
        out-of-box, либо пользовательский credential).
      * `verify_tls` — проверять сертификат. Default False — iDRAC по
        умолчанию идёт с self-signed сертификатом; включать только
        когда BMC получил cert от внутреннего CA.
      * `timeout` — секунды до httpx-таймаута (read + connect).

    Использование::

        async with RedfishClient("https://idrac.example.com",
                                  "root", "Calvin") as client:
            state = await client.get_power_state()
            await client.power_action("ForceOff")

    Можно и без `async with` — тогда caller сам обязан вызвать
    `await client.aclose()`, иначе underlying connection pool останется
    подвешенным.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = False,
        timeout: float = 30.0,
        manager_id: str = _DEFAULT_MANAGER_ID,
        system_id: str = _DEFAULT_SYSTEM_ID,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # `host` принимает либо полный URL (`https://idrac.example.com`),
        # либо «голый» hostname (`idrac.example.com`) — во втором случае
        # дописываем `https://`. iDRAC всегда HTTPS-only, plain-HTTP
        # допустим только в тестах через `http://...`.
        self._base_url = self._normalize_host(host)
        self._manager_id = manager_id
        self._system_id = system_id
        self._accounts_base = ""
        # Shared transport — если передан, connection pool разделяется с
        # другими RedfishClient'ами того же verify-уровня (см. http_pool
        # `get_bmc_redfish_transport`). `verify=` тогда задан на транспорте,
        # клиенту его передавать нельзя — httpx ругается на конфликт. Без
        # transport — standalone-клиент с собственным пулом (legacy-путь
        # для тестов с MockTransport через подмену `_client` напрямую).
        client_kwargs: dict = {
            "base_url": self._base_url,
            "auth": (username, password),
            "timeout": timeout,
            "headers": {"Accept": "application/json"},
        }
        if transport is None:
            client_kwargs["verify"] = verify_tls
        else:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)
        self._owns_transport = transport is None
        self._closed = False

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Нормализовать `host` → base URL с явным scheme.

        * `idrac.example.com` → `https://idrac.example.com`
        * `https://idrac.example.com/` → `https://idrac.example.com`
        * `http://localhost:8000` → `http://localhost:8000` (как есть)
        """
        stripped = host.strip().rstrip("/")
        if "://" not in stripped:
            stripped = f"https://{stripped}"
        return stripped

    # ── lifecycle ────────────────────────────────────────────────────

    async def __aenter__(self) -> "RedfishClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Закрыть underlying connection pool. Идемпотентно.

        Если клиент построен на shared transport (см. `http_pool
        .get_bmc_redfish_transport`), сам transport не закрываем —
        он живёт до WORKER_SHUTDOWN и переиспользуется другими
        RedfishClient'ами. httpx.AsyncClient.aclose() сначала помечает
        клиента как закрытого и потом вызывает transport.aclose(); для
        shared-случая мы пропускаем сам aclose клиента и руками выставляем
        флаг is_closed через приватный атрибут — иначе следующий вызов
        запроса на shared transport отвалится «Cannot send request after
        client has been closed».
        """
        if self._closed:
            return
        self._closed = True
        if not self._owns_transport:
            # Shared transport — клиент one-shot per BMC, но transport общий.
            # Закрывать клиент целиком значило бы закрыть transport,
            # ломая остальные RedfishClient'ы. Просто отпускаем ссылку;
            # `_closed=True` выше превратит дальнейшие request'ы в явный
            # RedfishError вместо голого AttributeError на `None.request`.
            self._client = None  # type: ignore[assignment]
            return
        await self._client.aclose()

    async def _resolve_manager_id(self) -> str:
        """Discovery Manager-id через коллекцию /redfish/v1/Managers.

        Используется, когда конструктор получил `manager_id=""` (generic-kind
        ipmi / redfish): спрашиваем коллекцию и берём первый элемент.
        Результат кэшируется в `self._manager_id` — повторного round-trip'а
        для последующих вызовов не будет.

        Любая ошибка (нет Members / пустой массив / non-2xx) → `RedfishError`.
        """
        if self._manager_id:
            return self._manager_id
        data = await self._get_json("/redfish/v1/Managers")
        members = data.get("Members")
        if not isinstance(members, list) or not members:
            raise RedfishError(
                status_code=200,
                message="Redfish /Managers collection is empty",
            )
        first = members[0]
        if not isinstance(first, dict):
            raise RedfishError(
                status_code=200,
                message="Redfish /Managers Members[0] is not an object",
            )
        odata_id = first.get("@odata.id")
        if not isinstance(odata_id, str) or not odata_id:
            raise RedfishError(
                status_code=200,
                message="Redfish /Managers Members[0] missing @odata.id",
            )
        # odata_id выглядит как `/redfish/v1/Managers/1` — берём last segment
        manager_id = odata_id.rstrip("/").rsplit("/", 1)[-1]
        if not manager_id:
            raise RedfishError(
                status_code=200,
                message=f"Cannot extract manager_id from {odata_id!r}",
            )
        self._manager_id = manager_id
        return manager_id

    async def _resolve_system_id(self) -> str:
        """Discovery System-id через коллекцию /redfish/v1/Systems.

        Используется, когда конструктор получил `system_id=""` (generic-kind
        ipmi / redfish либо неизвестный kind): спрашиваем коллекцию и берём
        первый элемент. Результат кэшируется в `self._system_id` — повторного
        round-trip'а для последующих вызовов не будет.

        Любая ошибка (нет Members / пустой массив / non-2xx) → `RedfishError`.
        """
        if self._system_id:
            return self._system_id
        data = await self._get_json("/redfish/v1/Systems")
        members = data.get("Members")
        if not isinstance(members, list) or not members:
            raise RedfishError(
                status_code=200,
                message="Redfish /Systems collection is empty",
            )
        first = members[0]
        if not isinstance(first, dict):
            raise RedfishError(
                status_code=200,
                message="Redfish /Systems Members[0] is not an object",
            )
        odata_id = first.get("@odata.id")
        if not isinstance(odata_id, str) or not odata_id:
            raise RedfishError(
                status_code=200,
                message="Redfish /Systems Members[0] missing @odata.id",
            )
        # odata_id выглядит как `/redfish/v1/Systems/System.Embedded.1` —
        # берём last segment.
        system_id = odata_id.rstrip("/").rsplit("/", 1)[-1]
        if not system_id:
            raise RedfishError(
                status_code=200,
                message=f"Cannot extract system_id from {odata_id!r}",
            )
        self._system_id = system_id
        return system_id

    async def _resolve_accounts_base(self) -> str:
        """Найти коллекцию аккаунтов BMC через AccountService.

        Путь к аккаунтам у вендоров разный: Dell iDRAC отдаёт их и под
        `/Managers/{id}/Accounts`, а HPE iLO5 держит только под
        `/redfish/v1/AccountService/Accounts` — под Manager'ом коллекции нет
        (GET отдаёт 404). Берём канонический путь из `AccountService.Accounts`,
        он работает на обоих вендорах. Результат кэшируется.

        Если AccountService недоступен или без ссылки `Accounts` — fallback на
        legacy `/redfish/v1/Managers/{manager_id}/Accounts`.
        """
        if self._accounts_base:
            return self._accounts_base
        try:
            svc = await self._get_json("/redfish/v1/AccountService")
            accounts = svc.get("Accounts")
            odata_id = accounts.get("@odata.id") if isinstance(accounts, dict) else None
            if isinstance(odata_id, str) and odata_id:
                self._accounts_base = odata_id.rstrip("/")
                return self._accounts_base
        except RedfishError:
            pass
        manager_id = await self._resolve_manager_id()
        self._accounts_base = f"/redfish/v1/Managers/{manager_id}/Accounts"
        return self._accounts_base

    # ── low-level HTTP helpers ───────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        expected_status: tuple[int, ...] = (200, 201, 202, 204),
    ) -> httpx.Response:
        """Унифицированный httpx-вызов с маппингом ошибок в `RedfishError`.

        `expected_status` — какие коды считаем успехом. Всё остальное
        → `RedfishError(status_code, message, redfish_error_code)` с
        попыткой вытащить ExtendedInfo.

        Transport-ошибки (timeout/connect/...) → `RedfishError(None, ...)` —
        caller-handler различит по `status_code is None`, что это «BMC
        unreachable», а не «отказ BMC».
        """
        if self._closed:
            # Use-after-aclose: на shared transport `_client` уже None,
            # на standalone — закрыт. Без явной проверки caller получает
            # либо AttributeError, либо httpx-исключение про closed client.
            # RedfishError(None, ...) единая семантика с другими transport
            # failures.
            raise RedfishError(
                status_code=None,
                message=f"Redfish client already closed (path={path})",
            )
        try:
            response = await self._client.request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            # URL может содержать креды если caller передал `host=
            # https://u:p@bmc`. Не должно случаться, но defence-in-depth.
            sanitized_url = _sanitize_url(f"{self._base_url}{path}")
            raise RedfishError(
                status_code=None,
                message=(
                    f"Redfish transport error to {sanitized_url}: "
                    f"{type(exc).__name__}"
                ),
            ) from exc

        if response.status_code not in expected_status:
            code, msg = _extract_redfish_error(response.text)
            human = msg or (
                f"Redfish {method} {path} failed with HTTP {response.status_code}"
            )
            raise RedfishError(
                status_code=response.status_code,
                message=human,
                redfish_error_code=code,
            )
        return response

    async def _get_json(self, path: str) -> dict:
        """GET → JSON dict. Любая не-2xx → RedfishError."""
        response = await self._request("GET", path)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise RedfishError(
                status_code=response.status_code,
                message=f"Redfish {path} returned non-JSON body",
            ) from exc
        if not isinstance(data, dict):
            raise RedfishError(
                status_code=response.status_code,
                message=f"Redfish {path} returned non-object JSON: {type(data).__name__}",
            )
        return data

    # ── high-level operations ────────────────────────────────────────

    async def get_power_state(self) -> str:
        """Текущее состояние питания сервера.

        Возвращает Redfish-строку: `On` / `Off` / `PoweringOn` / `PoweringOff`
        / `Unknown`. Caller может смэппить в свой формат.

        Запрос: `GET /redfish/v1/Systems/{system_id}` → `PowerState`.
        """
        system_id = await self._resolve_system_id()
        data = await self._get_json(f"/redfish/v1/Systems/{system_id}")
        state = data.get("PowerState")
        if not isinstance(state, str):
            raise RedfishError(
                status_code=200,
                message=(
                    f"Redfish Systems/{system_id} did not include "
                    f"PowerState field"
                ),
            )
        return state

    async def power_action(self, action: PowerAction) -> None:
        """Послать ComputerSystem.Reset action.

        BMC принимает 204 (no-content) или 200 (с ExtendedInfo). 202 —
        long-running, тоже OK (вернётся `@odata.id` на task-ресурс,
        но мы не следим за ним — caller сам опросит `get_power_state()`).
        """
        system_id = await self._resolve_system_id()
        await self._request(
            "POST",
            f"/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset",
            json_body={"ResetType": action},
        )

    async def rotate_user_password(self, user_id: int, new_password: str) -> None:
        """PATCH password на iDRAC user-аккаунте.

        `user_id` — slot account'а (у Dell root обычно 2 или 3, у HPE iLO
        Administrator — 1; зависит от заводской конфигурации). PATCH идёт на
        `{accounts_base}/{user_id}` с `{"Password": ...}`, где `accounts_base`
        резолвится через `AccountService.Accounts` (на iLO Manager-путь не
        существует, см. `_resolve_accounts_base`).

        BMC отвечает 200/204 при успехе; новый пароль вступает в силу
        немедленно — следующий запрос с старым паролем получит 401.

        Контракт `tasks/passwords.py::ipmi_rotate_password` — три шага в
        строгом порядке: **apply on BMC → verify → submit to
        server_service**. Этот метод закрывает только первый шаг (apply).
        Перед вызовом caller обязан положить `new_password` в Redis-stash
        (`_store_ipmi_rotate_password`), чтобы при крэше worker'а между
        apply и submit'ом следующий retry знал, какой пароль уже на BMC.
        После apply caller делает verify (read-only Redfish-вызов новым
        паролем) и только после успешного verify шлёт ciphertext в
        server_service. Round-trip к server_service выполняется ПОСЛЕ
        apply+verify, не до и не вместо них.
        """
        base = await self._resolve_accounts_base()
        await self._request(
            "PATCH",
            f"{base}/{user_id}",
            json_body={"Password": new_password},
            expected_status=(200, 204),
        )

