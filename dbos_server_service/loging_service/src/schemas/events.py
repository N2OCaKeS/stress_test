"""Pydantic-схемы запросов и ответов для событий аудита."""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.constants import Severity
from src.utils.normalization import normalize_service_name_preserve_case


# Top-level колонки audit-журнала, которые **идентифицируют actor'а**. Если бы
# их имена засветились внутри `details` (free-form JSONB), держатель
# `SERVICE_API_KEY` мог бы shadow'нуть/impersonate'нуть actor'а на некоторых
# downstream-маршрутах (ORM/сериалайзеры, которые сплющивают `details` поверх
# записи; JSON-path audit-rule matchers; log formatters, берущие первый ключ
# по имени, …).
#
# Решение: impersonation-поверхностью считаем только `actor_id` и
# `actor_type`. Остальные top-level поля (`service`,
# `action`, `status`, `department_id`, `request_id`, `severity`, `event_id`,
# `occurred_at`) легитимно используются внутри `details` как **target / scope /
# context** — например, permission-аудит пишет
# `details={"action": "delete", "entity_type": "server"}` (это granted action,
# а не event action); denied-аудит пишет
# `details={"reason": "department_isolation", "department_id": "<target>"}`
# (это department цели, а не actor'а). Запрет этих имён ломал audit-emit во
# всех 4 сервисах. Impersonation именно этих полей не даёт реального
# privilege gain — audit это read-only context, не authorization input.
#
# Сравнение идёт после `str.lower()`, чтобы трюки с регистром
# (`Actor_Id`, `ACTOR_TYPE`, …) не обходили гард.
_RESERVED_DETAIL_KEYS: frozenset[str] = frozenset({
    "actor_id",
    "actor_type",
})

# Charset для `request_id` против response-splitting. Middleware
# `main.attach_request_id` рефлектит значение в `X-Request-ID`; без
# проверки `request_id="abc\r\nSet-Cookie: hijack"` раскалывает ответ.
# Defence-in-depth (middleware тоже скрабит), плюс схема не пускает
# опасные байты до Postgres.
# Точка в charset'е — de-facto convention `req.<id>` / `trace.<span>`;
# middleware пропускает её (страйпит только CR/LF/NUL), асимметрия со
# схемой ловила бы legit-внешние request-id 422-кой.
_REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# Charset для текстовых полей audit-журнала. Любой downstream-экспорт
# (CSV/JSON log shipping, SIEM ingestion) сплющивает запись в строку;
# CRLF в `username` или `action` инжектится в эту строку и подделывает
# второе событие. Patterns:
#   * service — uses `[a-z_]` (snake_case по конвенции наименования сервисов).
#     `normalize_service_name` уже привёл к нижнему регистру, так что
#     uppercase сюда не доходит.
#   * action — dot-namespace `user.login.success` → разрешаем точку.
#     Цифры разрешены для версионирования (`provision_v2`, `http.4xx_error`).
#   * username — email-like, разрешаем `-_@.`.
_SERVICE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z_]{1,64}$")
_ACTION_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9_.]{1,128}$")
_USERNAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_\-@.]{1,128}$")
# ID-поля идентификаторов (`actor_id`, `target_id`, `department_id`) — это
# opaque-токены формата `usr_…` / `srv_…` / `dept_…` (см. `utils/ids.py`
# auth/server сервисов). Charset: латиница, цифры, `_` и `-`. Любой CRLF
# или control char здесь = log-injection через ту же поверхность, что и
# `username` — downstream-export сплющит в CSV и распилит запись.
_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")
# `target_type` — короткий handle типа сущности (`server`, `user`,
# `ipmi_controller`, `service_role`). По проекту snake_case + точка для
# namespaced-типов (`oauth.client`).
_TARGET_TYPE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z_.]{1,64}$")
# `idempotency_key` уходит в `audit_events.idempotency_key` (String(128)),
# смотрит на UNIQUE-индекс и засвечивается в логах при дедупе. CR/LF/TAB и
# другие control char'ы расщепили бы CSV-экспорт и испортили бы dedup-логику
# (значение хранится as-is, а нормализуется только NFKC). Charset покрывает
# UUID, opaque-токены и batch-id'ы (буквы, цифры, `_`, `-`, `.`).
_IDEMPOTENCY_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_\-.]{1,128}$")
# `actor_ip` — IPv4/IPv6, включая zone-id (`fe80::1%eth0`). Charset узкий:
# буквы/цифры (под hex-октеты и имя интерфейса в zone-id), `.`, `:`, `%`,
# `_`, `-`. Главное — отбить CR/LF/whitespace, которые инжектили бы вторую
# строку в CSV/SIEM-экспорт, как в `username`/`actor_id`.
_ACTOR_IP_PATTERN: re.Pattern[str] = re.compile(r"^[0-9A-Za-z:.%_\-]{1,64}$")

# Допустимый дрифт timestamp'а ingest-payload'а относительно now() сервера.
# Окно зависит от `actor_type`: user/bot/anonymous/oauth_client получают
# узкое окно (UI-flow, сессии короче часа), service-token caller'ы — широкое
# (outbox-retry после длительного outage может прислать событие через часы
# после реального возникновения). Конкретные значения берутся из настроек
# `EVENT_TIMESTAMP_SKEW_SECONDS_USER` / `EVENT_TIMESTAMP_SKEW_SECONDS_SERVICE`,
# дефолты — 1ч и 24ч соответственно.


class EventCreate(BaseModel):
    """Payload, который сервис шлёт чтобы записать событие аудита."""

    timestamp: datetime = Field(description="Когда событие произошло (ISO 8601 с TZ)")
    service: str = Field(max_length=64, description="Имя сервиса-источника")
    action: str = Field(max_length=128, description="Action в dot-namespace, напр. 'user.login'")

    actor_id: str | None = Field(default=None, max_length=48)
    actor_type: Literal["user", "bot", "service", "anonymous", "oauth_client"] = Field(default="user")
    username: str | None = Field(default=None, max_length=128, description="Человекочитаемое имя actor'а")
    department_id: str | None = Field(default=None, max_length=48)
    # Имя отдела актора — денормализация `department_id` для отображения,
    # симметрично паре `actor_id`/`username`. Строго опционально (None по
    # умолчанию), чтобы не ломать эмиттеры, которые его не шлют.
    department_name: str | None = Field(
        default=None, max_length=128, description="Человекочитаемое имя отдела actor'а"
    )

    target_id: str | None = Field(default=None, max_length=48)
    target_type: str | None = Field(default=None, max_length=64)

    # `warning` живёт рядом с `success/failure/denied` — используется в
    # soft-mode гардах (server_service::internal_service._check_target_department).
    # Семантика: операция прошла (`allowed=True`), но что-то пахнет —
    # missing header в soft mode, dept mismatch без strict-fail и т.п.
    status: Literal["success", "failure", "denied", "warning"] = Field(description="Исход действия")
    allowed: bool = Field(description="Было ли действие авторизовано")
    severity: Severity | None = Field(
        default=None,
        description="Важность события. Если опущено — loging_service подставит из defaults и правил.",
    )

    request_id: str | None = Field(default=None, max_length=64)

    # Кто и откуда. Оба опциональны (None по умолчанию), чтобы не ломать
    # эмиттеры, которые их не шлют. `actor_ip` — IP клиента, `user_agent` —
    # заголовок User-Agent. Заполняются сервисами из request-контекста.
    actor_ip: str | None = Field(default=None, max_length=64, description="IP клиента (actor'а)")
    user_agent: str | None = Field(default=None, max_length=512, description="User-Agent actor'а")

    details: dict = Field(
        default_factory=dict,
        description="Технический контекст — без секретов и паролей",
    )

    # Опциональный dedup-ключ для outbox-retry. Когда задан, ingest
    # дедупит по `(service, idempotency_key)`. Outbox publisher worker'а
    # может ретраить тот же row после network drop — без ключа мы получили
    # бы две записи. Паттерн как у Stripe/GitHub idempotency: caller выбирает
    # opaque-токен, сервер гарантирует single-write. Старые caller'ы без
    # ключа работают без дедупа.
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Opaque-ключ для дедупликации. Два POST'а с одинаковой парой "
            "(service, idempotency_key) считаются одним событием "
            "(outbox-retry safe). Опускайте для legacy / one-shot ingest."
        ),
    )

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp_tz(cls, v: datetime) -> datetime:
        """Naive datetime → UTC. Сами bounds считаются в model-валидаторе ниже —
        для них нужен `actor_type`, а field_validator его не видит.
        """
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def _bound_timestamp(self) -> "EventCreate":
        """Окно дрифта `timestamp` относительно `now()` зависит от `actor_type`.

        Service-token caller'ы (`actor_type=service`) пишут через outbox-retry
        и могут реплеить событие через часы после реального возникновения
        (внешний outage, рестарт worker'а). Узкое ±1ч окно теряло такие
        события навсегда. User/bot/anonymous/oauth_client идут через UI-flow,
        там сессии короче часа — для них узкое окно остаётся.

        Backdating-атаки: ограничены retention-окнами (минимум сутки), так что
        24-часовой service-bound не открывает «событие год назад»-вектор.
        Forward-dating (за `to_time` SOC'а) — то же ограничение.

        Значения окон тянутся из настроек (`EVENT_TIMESTAMP_SKEW_SECONDS_USER`,
        `EVENT_TIMESTAMP_SKEW_SECONDS_SERVICE`); `get_settings()` lru-кэширован,
        чтение на каждый payload — практически бесплатно.
        """
        # Локальный импорт ради разрыва цикла: `core.config` не должен зависеть
        # от схем, схемы исторически не тянули config — оставляем эту инверсию
        # точечно в одном валидаторе.
        from src.core.config import get_settings

        settings = get_settings()
        if self.actor_type == "service":
            skew_seconds = settings.event_timestamp_skew_seconds_service
        else:
            skew_seconds = settings.event_timestamp_skew_seconds_user
        skew = timedelta(seconds=skew_seconds)

        now = datetime.now(timezone.utc)
        if self.timestamp < now - skew:
            raise ValueError(
                f"timestamp too far in the past (>{skew_seconds}s from now "
                f"for actor_type={self.actor_type!r}): {self.timestamp.isoformat()}"
            )
        if self.timestamp > now + skew:
            raise ValueError(
                f"timestamp too far in the future (>{skew_seconds}s from now "
                f"for actor_type={self.actor_type!r}): {self.timestamp.isoformat()}"
            )
        return self

    @field_validator("service")
    @classmethod
    def _normalize_service(cls, v: str) -> str:
        """Канонизирует имя сервиса ДО любой проверки на равенство downstream.

        Без этого атакующий с `SERVICE_API_KEY` мог бы прислать
        `service="loging_service​"` (каноническое имя + U+200B
        zero-width space) или `service="lоging_service"` (кириллическая `о`)
        и обойти reserved-name гард в ingest-эндпоинте плюс retention-исключение
        в `apply_active`. Свёртка здесь означает, что downstream видит только
        канонический ASCII-form.

        Charset валидируем ДО `.lower()`, чтобы uppercase ASCII (`"ABC"`)
        отбивался — service-names по конвенции snake_case, и если пустить
        `.lower()` первым, `"ABC"` → `"abc"` тихо проскочит regex.
        """
        # Security-нормализация (NFKC + invisibles + confusables + strip),
        # но без кейсфолда — charset должен видеть исходный регистр.
        pre_lower = normalize_service_name_preserve_case(v)
        if not _SERVICE_PATTERN.match(pre_lower):
            raise ValueError(
                "service must match [a-z_]{1,64} after NFKC normalisation "
                "(lowercase only — service names are snake_case by convention; "
                "no CR/LF, no digits, no Unicode)"
            )
        # После charset'а — финальный кейсфолд (для `"ABC"` сюда уже не дойдём,
        # но `normalize_service_name` всё равно вернёт `.lower()` для других
        # caller'ов: ingest endpoint, retention WHERE-clause).
        return pre_lower.lower()

    @field_validator("action")
    @classmethod
    def _action_charset(cls, v: str) -> str:
        """Action идёт в JSON-логи + CSV-экспорты без escape'а CRLF.

        `action="user.login\\r\\n[ALERT] fake"` подделывает вторую строку в
        log-shipping pipeline'е. Разрешаем нижний регистр, цифры (для
        версий — `provision_v2`, `http.4xx_error`) и точку.
        """
        if not _ACTION_PATTERN.match(v):
            raise ValueError(
                "action must match [a-z0-9_.]{1,128} "
                "(no CR/LF, no uppercase, no Unicode)"
            )
        return v

    @field_validator("username")
    @classmethod
    def _username_charset(cls, v: str | None) -> str | None:
        """`username` рефлектится в audit-export'ы (CSV/JSON/SIEM).

        Без charset'а держатель `SERVICE_API_KEY` мог бы прислать
        `username="evil\\r\\n[ALERT] hijacked"` → запись в csv-отчёте
        раскалывается на две строки. Email-like чары разрешены потому,
        что auth_service пишет `username` как login-логин (включая email-форму).
        """
        if v is None:
            return v
        if not _USERNAME_PATTERN.match(v):
            raise ValueError(
                "username must match [A-Za-z0-9_\\-@.]{1,128} "
                "(no CR/LF, no whitespace, no Unicode)"
            )
        return v

    @field_validator("actor_id", "target_id", "department_id")
    @classmethod
    def _id_charset(cls, v: str | None) -> str | None:
        """ID-поля рефлектятся в audit-export'ы и идут в WHERE-clause запросов.

        `actor_id="usr_1\\r\\n[ALERT] fake"` инжектит вторую строку в CSV/JSON
        log shipping, симметрично `username`. Кроме того, `target_id`
        используется retention-rule matcher'ом и SUPPRESS-правилами по
        `match_target_id` — control-chars там могут спутать regex / glob.
        """
        if v is None:
            return v
        if not _ID_PATTERN.match(v):
            raise ValueError(
                "id fields must match [A-Za-z0-9_-]{1,48} "
                "(no CR/LF, no whitespace, no Unicode)"
            )
        return v

    @field_validator("target_type")
    @classmethod
    def _target_type_charset(cls, v: str | None) -> str | None:
        """`target_type` — handle типа сущности; snake_case + точка."""
        if v is None:
            return v
        if not _TARGET_TYPE_PATTERN.match(v):
            raise ValueError(
                "target_type must match [a-z_.]{1,64} "
                "(no CR/LF, no digits, no Unicode)"
            )
        return v

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, v):
        """Канонизирует ключ до UNIQUE-сравнения: NFKC + trim, пустое → None.

        Без этого `"abc"` и `"abc "` (trailing space) или `"abc"` и
        `"ａｂｃ"` (fullwidth) считались бы разными ключами на PostgreSQL'овском
        partial UNIQUE индексе — два «логически одинаковых» retry'я outbox'а
        записывались бы как два разных события, вместо дедупа.

        Применяем NFKC + strip ДО length-check'а (max_length=128 в Field),
        чтобы атакующий не мог обойти cap раздуванием через compatibility-form'у.
        Пустая строка после strip'а → None (тот же legacy-режим, что и
        опущенное поле — без дедупа).
        """
        if v is None:
            return v
        if not isinstance(v, str):
            # Не str → пускаем дальше как есть. Пайдантик-уровневый type-coerce
            # на str-аннотации отбьёт не-строку с `string_type`-ошибкой ДО
            # того, как value уедет в БД, так что NFKC/charset нам здесь делать
            # не на чем. Возвращаем raw, чтобы caller получил стандартный
            # `Input should be a valid string`, а не наше специальное
            # «match [A-Za-z0-9_.-]».
            return v
        normalised = unicodedata.normalize("NFKC", v).strip()
        if not normalised:
            return None
        # Charset-гард после NFKC. NFKC сворачивает fullwidth/совместимые
        # формы, но не убирает CR/LF/TAB и прочие control-байты — те
        # расщепили бы CSV-экспорт и сломали бы dedup на partial UNIQUE
        # индексе.
        if not _IDEMPOTENCY_KEY_PATTERN.match(normalised):
            raise ValueError(
                "idempotency_key must match [A-Za-z0-9_\\-.]{1,128} "
                "after NFKC normalisation (no CR/LF, no control chars)"
            )
        return normalised

    @field_validator("request_id")
    @classmethod
    def _request_id_charset(cls, v: str | None) -> str | None:
        """Ограничивает `request_id` безопасным charset'ом против response-splitting.

        Middleware рефлектит значение в `X-Request-ID`. С `\\r\\n` в payload'е
        держатель `SERVICE_API_KEY` мог бы инжектить дополнительные headers
        (или целый второй ответ) на уязвимых версиях uvicorn/h11. Проверка
        здесь не пускает байты ни до store'а, ни до middleware reflection.
        """
        if v is None:
            return v
        if not _REQUEST_ID_PATTERN.match(v):
            raise ValueError(
                "request_id must match [A-Za-z0-9_-]{1,64} "
                "(no CR/LF, no control chars, no Unicode)"
            )
        return v

    @field_validator("actor_ip")
    @classmethod
    def _actor_ip_charset(cls, v: str | None) -> str | None:
        """`actor_ip` рефлектится в audit-export'ы и WHERE-clause GET /events.

        Charset держим узким (hex/`.`/`:`/`%`) — CR/LF тут расщепили бы CSV/SIEM
        строку, как в `username`/`actor_id`. Пустую строку приводим к None,
        чтобы сервисы могли слать `""` для «IP не определён» без лишней ветки.
        """
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not _ACTOR_IP_PATTERN.match(v):
            raise ValueError(
                "actor_ip must match [0-9A-Za-z:.%_-]{1,64} "
                "(IPv4/IPv6 with optional zone-id, no CR/LF, no whitespace)"
            )
        return v

    @field_validator("user_agent")
    @classmethod
    def _user_agent_scrub(cls, v: str | None) -> str | None:
        """`user_agent` — free-form клиентский заголовок; скрабим control-байты.

        UA не из доверенного источника, поэтому charset-whitelist тут не
        подходит (легитимные UA несут скобки, слэши, точки с запятой). Вместо
        этого вырезаем CR/LF/NUL и прочие control-символы, которые расщепили бы
        строку в CSV/SIEM-экспорте, и режем по max_length схемой. Пустую строку
        приводим к None.
        """
        if v is None:
            return v
        v = "".join(ch for ch in v if ch == "\t" or ord(ch) >= 0x20)
        v = v.strip()
        return v or None

    @field_validator("department_name")
    @classmethod
    def _department_name_scrub(cls, v: str | None) -> str | None:
        """`department_name` — free-form имя отдела; скрабим control-байты.

        В отличие от `username` (login-like, узкий charset), имя отдела —
        человекочитаемая строка с пробелами, кириллицей, дефисами и т.п.,
        поэтому charset-whitelist тут не подходит. Вместо этого вырезаем
        CR/LF/NUL и прочие control-символы, которые расщепили бы строку в
        CSV/SIEM-экспорте, ровно как в `user_agent`. Пустую строку приводим
        к None.
        """
        if v is None:
            return v
        v = "".join(ch for ch in v if ch == "\t" or ord(ch) >= 0x20)
        v = v.strip()
        return v or None

    @field_validator("details")
    @classmethod
    def _details_shadow_keys(cls, v: dict) -> dict:
        """Запрет рекурсивного использования top-level имён колонок + NUL-байтов.

        Закрывает два вектора audit-injection через JSONB:

        1. **Shadow keys.** Top-level колонки (`actor_id`, `service`,
           `action`, …) — это то, на чём диспатчит downstream-аналитика и
           SUPPRESS-правила. Если атакующий (держатель `SERVICE_API_KEY`)
           кладёт то же имя внутрь `details`, то всё, что сплющивает
           запись (некоторые ORM-сериалайзеры, log formatters по имени,
           JSON-path audit-rule matchers), может схватить *shadow*-значение
           вместо канонической колонки — позволяя impersonate'нуть любого
           actor'а / сервис / action *без* того, чтобы это записалось в
           саму колонку. Запрещаем имена на ЛЮБОЙ глубине, чтобы даже
           вложенная маскировка (`{"a": {"actor_id": "hijack"}}`) не
           проходила.

        2. **NUL-байты.** Текстовые колонки PostgreSQL не могут хранить
           `\\x00` (валится `DataError`), а JSONB — может. После записи
           любой downstream text-export (CSV-отчёты, `COPY TO`, log shipping
           в file-sinks) либо молча роняет байт (data corruption), либо
           давится на нём (DoS на pipeline экспорта). Проще отбить на
           ingest, чем потом помнить про strip везде.

        Рекурсивный обход — итеративный (стек), потому что сама рекурсия
        была бы DoS-вектором на патологически глубоком payload'е (см.
        `_details_depth` ниже).
        """
        stack: list[object] = [v]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k, sub in node.items():
                    if isinstance(k, str):
                        if k.lower() in _RESERVED_DETAIL_KEYS:
                            raise ValueError(
                                f"details must not contain reserved top-level "
                                f"audit-column key {k!r} at any nesting depth"
                            )
                        if "\x00" in k:
                            raise ValueError(
                                "details must not contain NUL bytes in keys"
                            )
                    if isinstance(sub, str) and "\x00" in sub:
                        raise ValueError(
                            "details must not contain NUL bytes in string values"
                        )
                    if isinstance(sub, (dict, list)):
                        stack.append(sub)
            elif isinstance(node, list):
                for sub in node:
                    if isinstance(sub, str) and "\x00" in sub:
                        raise ValueError(
                            "details must not contain NUL bytes in string values"
                        )
                    if isinstance(sub, (dict, list)):
                        stack.append(sub)
        return v

    @field_validator("details")
    @classmethod
    def _details_depth(cls, v: dict) -> dict:
        """Лимит вложенности 10 уровней — блокирует RecursionError DoS.

        Без него атакующий с `SERVICE_API_KEY` мог бы прислать
        `details = {"a": {"a": {"a": ... 10_000 уровней ...}}}` — payload
        маленький (под 64 KB), но любой downstream-consumer с рекурсией
        (audit rule matcher, JSON-сериалайзер JSONB-драйвера, log formatter)
        ловит `RecursionError` и воркер умирает. Даже `json.dumps` падает с
        `RecursionError` выше `sys.getrecursionlimit() // 2` ≈ 500 — но body
        запроса уже распарсен FastAPI'евским JSON-загрузчиком (C-accel,
        recursion-safe), так что взрыв происходит downstream, а не на
        десериализации.

        Глубина 10 покрывает любой realistic audit use case: самое глубокое,
        что встречалось в реальных событиях — 4-5 уровней (`request.headers
        .x_forwarded_for[0]` ≈ 3 уровня). 10 оставляет комфортный запас на
        будущий рост схемы без открытия abuse-вектора.

        ВАЖНО: этот валидатор объявлен ПЕРЕД `_details_size` намеренно —
        pydantic v2 запускает field_validator'ы в порядке объявления, а
        `json.dumps` в size-check сам падает с RecursionError при ~500
        уровнях (CPython recursion limit / 2). Если пропустить size-валидатор
        первым, attacker с глубоким payload получит 500 InternalError
        вместо чистого 422 — и воркер запишет traceback в логи, что само
        по себе DoS-вектор (log flood).
        """
        _MAX_DEPTH = 10

        # ИТЕРАТИВНЫЙ обход через explicit stack — НЕ рекурсивная функция.
        # Рекурсивный _walk сам падал бы с RecursionError на attacker'ом
        # построенной структуре в 5000+ уровней (по умолчанию
        # sys.getrecursionlimit ≈ 1000, на CPython ≈ 500 практически из-за
        # overhead'а). Итеративный подход гарантирует константный
        # stack-frame consumption и детерминированный fail на >_MAX_DEPTH.
        #
        # Семантика depth: счётчик «вложенных контейнеров» на пути от корня.
        # `details = {}` → depth=1; `{"a": {"b": 1}}` → depth=2;
        # `{"a": [{"b": 1}]}` → depth=3 (dict → list → dict).
        # Leaf-значения (str, int, None, bool) глубину не увеличивают.
        # Лимит 10 значит «до 10 вложенных контейнеров»; 11-й вызывает
        # ValueError.
        stack: list[tuple[object, int]] = [(v, 1)]
        while stack:
            node, depth = stack.pop()
            if isinstance(node, dict):
                if depth > _MAX_DEPTH:
                    raise ValueError(
                        f"details nesting must not exceed {_MAX_DEPTH} levels"
                    )
                for sub in node.values():
                    # Только контейнеры идут в стек — leaves скипаем.
                    if isinstance(sub, (dict, list)):
                        stack.append((sub, depth + 1))
            elif isinstance(node, list):
                if depth > _MAX_DEPTH:
                    raise ValueError(
                        f"details nesting must not exceed {_MAX_DEPTH} levels"
                    )
                for sub in node:
                    if isinstance(sub, (dict, list)):
                        stack.append((sub, depth + 1))
        return v

    @field_validator("details")
    @classmethod
    def _details_size(cls, v: dict) -> dict:
        if len(json.dumps(v, default=str)) > 65_536:
            raise ValueError("details must not exceed 64 KB")
        return v


class EventResponse(BaseModel):
    """Ответ после успешного приёма события."""

    id: str
    received_at: datetime

    model_config = {"from_attributes": True}


class EventDetail(BaseModel):
    """Полная запись события — возвращается query-эндпоинтами."""

    id: str
    timestamp: datetime
    received_at: datetime
    service: str
    action: str
    actor_id: str | None
    actor_type: str
    username: str | None
    department_id: str | None
    department_name: str | None
    target_id: str | None
    target_type: str | None
    status: str
    allowed: bool
    severity: str
    request_id: str | None
    actor_ip: str | None
    user_agent: str | None
    details: dict

    model_config = {"from_attributes": True}


class EventStatsResponse(BaseModel):
    """Агрегаты audit-журнала за временное окно.

    `by_severity` — счётчики по каждому из шести уровней severity; ключи, под
    которые в окне не попало ни одного события, в map отсутствуют (нулей не
    подставляем — фронт сам решает, показывать ли отсутствующий уровень нулём).
    `by_service` — счётчики по имени сервиса-источника. `by_status` — по исходу
    (`success`/`failure`/`denied`/`warning`). `total` — всего событий под фильтр.

    `from_time` / `to_time` — фактическое окно, по которому считали (UTC),
    чтобы фронт показал оператору границы агрегата без обратного пересчёта
    `window_hours`. `truncated` поднимается в True, если хоть один из
    `GROUP BY`-проходов был отменён по `statement_timeout` — счётчики тогда
    неполны.
    """

    total: int
    by_severity: dict[str, int]
    by_service: dict[str, int]
    by_status: dict[str, int]
    from_time: datetime
    to_time: datetime
    truncated: bool = False


class EventListResponse(BaseModel):
    """Постраничный список событий аудита.

    `total` заполняется только когда запрос пришёл с `include_total=true` —
    точный COUNT по журналу в миллионы строк дорог, поэтому по умолчанию он
    не считается и поле равно `null`. Для навигации по страницам используйте
    `has_more`: он определяется выборкой одной лишней строки и не требует
    второго прохода по таблице.
    """

    items: list[EventDetail]
    total: int | None = None
    has_more: bool = False
    limit: int
    offset: int
