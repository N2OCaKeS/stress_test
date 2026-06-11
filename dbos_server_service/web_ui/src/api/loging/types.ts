/**
 * Типы request/response для loging_service API.
 *
 * Источник истины — Pydantic-схемы в `loging_service/src/schemas/`
 * (events.py, rules.py, retention.py, services.py). Backend под
 * `/api/logging/v1/` (одна 'g'-каталог `loging`, URL-префикс `logging`).
 *
 * Орфография каталога — `loging` (одна g) — намеренная, см.
 * `obsidian/services/loging_service.md`.
 */

// ── shared ──────────────────────────────────────────────────────────────────

/** ISO-8601 timestamp в UTC (`2026-06-11T12:34:56Z`). */
export type Iso8601 = string;

/**
 * Шесть уровней важности (`core/constants.py::Severity`).
 * `TRACE` < `DEBUG` < `INFO` < `WARNING` < `ERROR` < `CRITICAL`.
 */
export type Severity =
  | "TRACE"
  | "DEBUG"
  | "INFO"
  | "WARNING"
  | "ERROR"
  | "CRITICAL";

/** Исход действия (`EventCreate.status` / query-фильтр). */
export type EventStatus = "success" | "failure" | "denied" | "warning";

// ── events ────────────────────────────────────────────────────────────────────

/** Полная запись события (ответ `GET /events`, `EventDetail`). */
export interface EventDetail {
  id: string;
  timestamp: Iso8601;
  received_at: Iso8601;
  service: string;
  action: string;
  actor_id: string | null;
  actor_type: string;
  username: string | null;
  department_id: string | null;
  target_id: string | null;
  target_type: string | null;
  status: string;
  allowed: boolean;
  severity: string;
  request_id: string | null;
  details: Record<string, unknown>;
}

/**
 * Постраничный список событий (`EventListResponse`).
 *
 * `total` приходит только при `include_total=true`, иначе `null`. Для
 * навигации по страницам — `has_more` (backend выбирает `limit+1`).
 */
export interface EventListResponse {
  items: EventDetail[];
  total: number | null;
  has_more: boolean;
  limit: number;
  offset: number;
}

/**
 * Фильтры `GET /events` — all-AND. Любая комбинация опциональна.
 * Маппинг 1:1 на query-params backend'а (`endpoints/events.py::list_events`).
 */
export interface ListEventsQuery {
  department_id?: string;
  service?: string;
  severity?: Severity;
  action?: string;
  actor_id?: string;
  target_id?: string;
  status?: EventStatus;
  request_id?: string;
  from_time?: Iso8601;
  to_time?: Iso8601;
  limit?: number;
  offset?: number;
  include_total?: boolean;
}

// ── rules ─────────────────────────────────────────────────────────────────────

/**
 * Эффект правила. Канон в БД/ответах — `SUPPRESS`/`ALLOW`/`OVERRIDE_SEVERITY`.
 * На вход backend дополнительно принимает алиас `DROP` (нормализуется в
 * `SUPPRESS`).
 */
export type RuleEffect = "SUPPRESS" | "ALLOW" | "OVERRIDE_SEVERITY";

/** Карточка правила (`RuleResponse`). */
export interface Rule {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  priority: number;
  match_service: string | null;
  match_action: string | null;
  match_status: string | null;
  match_severity: string | null;
  match_allowed: boolean | null;
  effect: string;
  effect_severity: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Постраничный список правил (`RuleListResponse`). */
export interface RuleListResponse {
  items: Rule[];
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
}

/**
 * Тело `POST /rules` (`RuleCreate`).
 *
 * `effect` обязателен. `effect_severity` обязателен и допустим ТОЛЬКО при
 * `effect=OVERRIDE_SEVERITY` (иначе 422 `EFFECT_SEVERITY_REQUIRED` /
 * `EFFECT_SEVERITY_NOT_ALLOWED`). Поля `match_*` опциональны — None = «любое».
 */
export interface RuleCreateRequest {
  name: string;
  description?: string | null;
  is_active?: boolean;
  priority?: number;
  match_service?: string | null;
  match_action?: string | null;
  match_status?: EventStatus | null;
  match_severity?: Severity | null;
  match_allowed?: boolean | null;
  effect: RuleEffect;
  effect_severity?: Severity | null;
}

/** Тело `PATCH /rules/{id}` (`RuleUpdate`). Все поля опциональны. */
export interface RuleUpdateRequest {
  name?: string | null;
  description?: string | null;
  is_active?: boolean | null;
  priority?: number | null;
  match_service?: string | null;
  match_action?: string | null;
  match_status?: EventStatus | null;
  match_severity?: Severity | null;
  match_allowed?: boolean | null;
  effect?: RuleEffect | null;
  effect_severity?: Severity | null;
}

// ── retention ─────────────────────────────────────────────────────────────────

/** Активная retention-политика (`RetentionPolicyResponse`). */
export interface RetentionPolicy {
  id: string;
  retain_days: number;
  description: string | null;
  is_active: boolean;
  severity: string | null;
  service: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/**
 * Тело `PUT /retention` (`RetentionPolicyCreate`).
 *
 * `retain_days` обязателен, диапазон [30, 3650]. `severity_filter` /
 * `service_filter` опциональны (None / [] = ко всем). `loging_service` в
 * `service_filter` отбивается 422.
 */
export interface RetentionPolicyPutRequest {
  retain_days: number;
  description?: string | null;
  is_active?: boolean;
  severity_filter?: Severity[] | null;
  service_filter?: string[] | null;
}

// ── services ──────────────────────────────────────────────────────────────────

/** Агрегат по сервису-источнику (`ServiceInfo`). */
export interface ServiceInfo {
  service: string;
  event_count: number;
  last_event_at: Iso8601;
}

/** Список сервисов, писавших события (`ServiceListResponse`). */
export interface ServiceListResponse {
  items: ServiceInfo[];
  total: number;
  has_more: boolean;
  limit: number | null;
  offset: number | null;
}

/** Зарегистрированный action сервиса (`ServiceEventDetail`). */
export interface ServiceEventDetail {
  action: string;
  description: string | null;
  default_severity: string | null;
  registered_at: Iso8601;
  updated_at: Iso8601;
}

/** Каталог action'ов сервиса (`ServiceEventsResponse`). */
export interface ServiceEventsResponse {
  service: string;
  items: ServiceEventDetail[];
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
}
