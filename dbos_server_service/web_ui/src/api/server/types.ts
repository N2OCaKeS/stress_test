/**
 * Типы request/response для server_service API.
 *
 * Файл расшарен между всеми wrapper'ами раздела `/api/server/*`. Каждый
 * подраздел держит свою секцию между разделителями. Источник истины —
 * Pydantic-схемы в `server_service/src/schemas/`.
 */

// ── shared ──────────────────────────────────────────────────────────────────

/** ISO-8601 UTC timestamp (`2026-06-11T12:34:56Z`). */
export type Iso8601 = string;

/** Cursor envelope, используемый list-эндпоинтами c keyset-пагинацией. */
export interface CursorPaginatedResponse<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

/** Offset envelope, остаётся legacy-форматом для list-эндпоинтов. */
export interface OffsetPaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Стандартный ответ на dispatch worker-task'и (`{task_id, status}`). */
export interface TaskDispatchResponse {
  task_id: string;
  status: string;
}

/** Body `{reason: string}` — используется для DELETE-операций и busy-захвата. */
export interface ReasonBody {
  reason: string;
}

// ── servers ─────────────────────────────────────────────────────────────────

/** ServerStatus enum (`servers.status`). */
export type ServerStatus =
  | "unknown"
  | "online"
  | "offline"
  | "maintenance"
  | "decommissioned";

/** BusyState enum (`servers.busy_state`). */
export type BusyState = "free" | "busy" | "testing";

/** PowerState enum (`servers.power_state`). */
export type PowerState = "on" | "off" | "unknown";

/** IpmiKind enum (`ipmi_controllers.kind`). */
export type IpmiKind = "idrac" | "ilo" | "ipmi" | "redfish";

/** Спецификация диска в `ServerCreate.storage` / `ServerUpdate.storage`. */
export interface DiskSpec {
  slot: string;
  size_gb: number;
  model?: string | null;
  is_system?: boolean;
}

/** Карточка диска внутри `Server.storage`. */
export interface DiskResponse {
  id: string;
  slot: string;
  size_gb: number;
  model: string | null;
  is_system: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/**
 * IPMI-блок, вкладываемый в `ServerCreateRequest.ipmi`. `password_b64` —
 * base64(plaintext); бэкенд декодирует и шифрует at-rest.
 */
export interface ServerIpmiCreate {
  kind: IpmiKind;
  endpoint_url: string;
  username: string;
  password_b64: string;
}

/** Карточка сервера (ответ GET/POST/PATCH /servers). */
export interface Server {
  id: string;
  hostname: string;
  display_name: string | null;
  ip_address: string;
  mgmt_ip_address: string | null;
  ssh_port: number;
  os_version_id: string | null;
  os_last_synced_at: Iso8601 | null;
  department_id: string;
  status: ServerStatus;
  power_state: PowerState;
  busy_state: BusyState;
  busy_user_id: string | null;
  busy_since: Iso8601 | null;
  busy_note: string | null;
  serial_number: string | null;
  asset_tag: string | null;
  location: string | null;
  cpu_brand: string | null;
  cpu_model: string | null;
  cpu_cores: number | null;
  cpu_threads: number | null;
  cpu_frequency_ghz: number | null;
  ram_total_mb: number | null;
  network_interface_name: string | null;
  decommissioned_at: Iso8601 | null;
  is_managed: boolean;
  management_user: string | null;
  prepared_at: Iso8601 | null;
  /**
   * Управляющие креды per-server (фича #3). На момент написания backend
   * (`server_service/src/schemas/server.py::ServerResponse`) ещё не сериализует
   * эти поля — они есть в ORM-модели, но не в response-схеме. Поэтому держим их
   * опциональными: блок «Управляющие креды» деградирует (fingerprint «—»,
   * pending не показан), пока схема не отдаст значения. fingerprint считаем на
   * фронте из `mgmt_ssh_public_key` (см. `@/lib/sshFingerprint`).
   */
  mgmt_ssh_public_key?: string | null;
  mgmt_creds_rotated_at?: Iso8601 | null;
  mgmt_creds_pending_apply?: boolean;
  storage: DiskResponse[];
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело POST /servers. */
export interface ServerCreateRequest {
  hostname: string;
  display_name?: string | null;
  ip_address: string;
  mgmt_ip_address?: string | null;
  ssh_port?: number;
  department_id: string;
  os_version_id?: string | null;
  cpu_brand?: string | null;
  cpu_model?: string | null;
  cpu_cores?: number | null;
  cpu_threads?: number | null;
  cpu_frequency_ghz?: number | null;
  ram_total_mb?: number | null;
  network_interface_name?: string | null;
  serial_number?: string | null;
  asset_tag?: string | null;
  location?: string | null;
  storage?: DiskSpec[];
  ipmi?: ServerIpmiCreate | null;
}

/** Тело PATCH /servers/{id}. Все поля опциональны. */
export interface ServerUpdateRequest {
  display_name?: string | null;
  ip_address?: string | null;
  mgmt_ip_address?: string | null;
  ssh_port?: number | null;
  os_version_id?: string | null;
  cpu_brand?: string | null;
  cpu_model?: string | null;
  cpu_cores?: number | null;
  cpu_threads?: number | null;
  cpu_frequency_ghz?: number | null;
  ram_total_mb?: number | null;
  network_interface_name?: string | null;
  serial_number?: string | null;
  asset_tag?: string | null;
  location?: string | null;
  storage?: DiskSpec[] | null;
}

/** Тело POST /servers/{id}/busy — захват сервера. */
export interface ServerAcquireRequest {
  lease_until?: Iso8601 | null;
  purpose?: string | null;
}

/**
 * Тело POST /servers/prepare/bulk — массовый prepare.
 *
 * Backend ждёт креды per-server: на каждый сервер свой `username_b64` /
 * `password_b64` (base64 plaintext) и опциональный приватный SSH-ключ.
 */
export interface BulkPrepareItem {
  server_id: string;
  username_b64: string;
  password_b64: string;
  ssh_private_key_b64?: string;
}

/** Тело POST /servers/prepare/bulk. */
export interface BulkPrepareRequest {
  items: BulkPrepareItem[];
}

/** Один исход per-server в ответе массового prepare. */
export interface BulkPrepareResult {
  server_id: string;
  /** queued — задача поставлена; skipped — пропущен (см. reason). */
  status: "queued" | "skipped" | (string & {});
  task_id?: string | null;
  reason?: string | null;
}

/** Ответ POST /servers/prepare/bulk (202). */
export interface BulkPrepareResponse {
  results: BulkPrepareResult[];
  queued_count: number;
  skipped_count: number;
}

/**
 * Тело POST /servers/{id}/prepare — bootstrap-креды.
 *
 * Два взаимоисключающих режима (ровно один):
 *  - `{account_id}` — server_service сам резолвит привязанный server_account
 *    и расшифровывает его пароль; UI пароль не шлёт;
 *  - ручной `{username_b64, password_b64, ssh_private_key_b64?}` — логин/пароль
 *    (+ опц. приватный SSH-ключ) в base64.
 */
export interface ServerPrepareRequest {
  account_id?: string;
  username_b64?: string;
  password_b64?: string;
  ssh_private_key_b64?: string;
}

/** Ответ POST /servers/{id}/prepare. */
export interface ServerPrepareResponse {
  task_id: string;
  status: string;
}

// ── prepare-batch (массовый prepare с per-server режимом кред) ────────────────

/**
 * Один сервер в массовом prepare-batch: `server_id` + те же поля, что у
 * single-prepare (`ServerPrepareRequest`). Ровно один режим на сервер:
 * `account_id` (привязанная учётка) либо ручной `username_b64`+`password_b64`
 * (+ опц. `ssh_private_key_b64`).
 */
export interface ServerPrepareBatchItem extends ServerPrepareRequest {
  server_id: string;
}

/** Тело POST /servers/prepare-batch. Дубли `server_id` запрещены (422). */
export interface ServerPrepareBatchRequest {
  items: ServerPrepareBatchItem[];
}

/** Успешно поставленная per-server задача в batch-ответе. */
export interface ServerBatchDispatched {
  server_id: string;
  server_name: string | null;
  task_id: string;
  status: string;
}

/** Коды причин отказа в prepare-batch (`ServerBatchFailed.reason`). */
export type ServerPrepareBatchReason =
  | "not_found_or_cross_dept"
  | "decommissioned"
  | "idempotent_conflict"
  | "idempotency_key_reuse_conflict"
  | "account_has_no_password"
  | "account_not_found"
  | "account_not_linked"
  | "permission_denied"
  | "worker_unreachable"
  | "not_attempted"
  | (string & {});

/** Сервер, на который задача в batch'е не поставлена. */
export interface ServerBatchFailed {
  server_id: string;
  server_name: string | null;
  reason: ServerPrepareBatchReason;
}

/** Ответ POST /servers/prepare-batch (202). */
export interface ServerPrepareBatchResponse {
  batch_id: string;
  dispatched: ServerBatchDispatched[];
  failed: ServerBatchFailed[];
}

// ── clean (оркестрация очистки после переустановки ОС) ────────────────────────

/**
 * Статус per-action итога clean'а: `done` (синхронное действие выполнено —
 * unbind / os-version), `dispatched` (worker-задача поставлена, есть `task_id`),
 * `skipped` (действие не выбрано), `failed` (причина в `reason`).
 */
export type ServerCleanActionStatus =
  | "done"
  | "dispatched"
  | "skipped"
  | "failed"
  | (string & {});

/** Per-action итог clean'а. */
export interface ServerCleanActionResult {
  status: ServerCleanActionStatus;
  task_id?: string | null;
  reason?: string | null;
  detail?: Record<string, unknown> | null;
}

/**
 * Тело POST /servers/{id}/clean — оркестрация очистки после переустановки ОС.
 *
 * Минимум один флаг должен быть выбран. `os_version_id` учитывается только при
 * `update_os_version`. `prepare` обязателен при `rerun_prepare` — та же форма
 * bootstrap-кред, что у single-prepare (account-режим либо ручной ввод).
 */
export interface ServerCleanRequest {
  unbind_accounts: boolean;
  update_os_version: boolean;
  rerun_prepare: boolean;
  run_inventory_sync: boolean;
  os_version_id?: string | null;
  prepare?: ServerPrepareRequest | null;
}

/** Ответ POST /servers/{id}/clean — per-action итоги. */
export interface ServerCleanResponse {
  server_id: string;
  unbind_accounts: ServerCleanActionResult;
  rerun_prepare: ServerCleanActionResult;
  update_os_version: ServerCleanActionResult;
  run_inventory_sync: ServerCleanActionResult;
}

// ── ipmi ────────────────────────────────────────────────────────────────────

/** Результат последнего probe BMC (`ipmi_controllers.last_status`). */
export type IpmiProbeStatus = "ok" | "unreachable" | "auth_failed";

/**
 * Карточка IPMI-контроллера (response `IpmiControllerResponse`).
 *
 * `password_b64` — base64(plaintext BMC-пароля). Поле присутствует только
 * когда вызывающий держит action `view_credentials`; иначе `null`. Сырого
 * `password_encrypted` в ответе нет никогда.
 */
export interface IpmiController {
  id: string;
  server_id: string;
  kind: IpmiKind;
  endpoint_url: string;
  username: string;
  password_rotated_at: Iso8601 | null;
  password_b64: string | null;
  last_probed_at: Iso8601 | null;
  last_status: IpmiProbeStatus | string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/**
 * Тело POST /servers/{server_id}/ipmi — регистрация BMC.
 *
 * `password_b64` — base64(plaintext); бэкенд декодирует и шифрует через
 * `secrets_service.encrypt()` до записи в БД. Действует политика на plaintext:
 * минимум 8 символов, буквы и цифры.
 */
export interface IpmiCreateRequest {
  kind: IpmiKind;
  endpoint_url: string;
  username: string;
  password_b64: string;
}

/**
 * Тело PATCH /servers/{server_id}/ipmi — частичное обновление BMC.
 *
 * Все поля опциональны. `password` через PATCH не меняется — ротация
 * проходит отдельным потоком через `rotateIpmi`/worker dispatch.
 */
export interface IpmiUpdateRequest {
  kind?: IpmiKind | null;
  endpoint_url?: string | null;
  username?: string | null;
}

/**
 * Ответ `GET /servers/{server_id}/ipmi/credentials` — метаданные без пароля.
 *
 * Plaintext-пароль через этот endpoint не отдаётся ни при каких условиях;
 * для него есть только internal endpoint под worker'ом.
 */
export interface IpmiCredentials {
  id: string;
  server_id: string;
  kind: IpmiKind;
  endpoint_url: string;
  username: string;
  password_rotated_at: Iso8601 | null;
}

/**
 * Ответ `GET /servers/{server_id}/ipmi/power` — кэшированный power_state.
 *
 * TTL/инвалидации у `power_state` нет: значение перетирается worker'ом при
 * очередном `power.{on,off,reboot}` callback'е, между обновлениями может
 * быть сколь угодно устаревшим. Live-опрос — через `dispatchPowerStatus`.
 */
export interface PowerStatus {
  server_id: string;
  power_state: PowerState;
  last_probed_at: Iso8601 | null;
}

// ── server-accounts ─────────────────────────────────────────────────────────

/**
 * Происхождение аккаунта в БД.
 *
 * `managed` — заведён через API, пароль известен сервису и может ротироваться;
 * `discovered` — найден инвентаризацией бокса, пароля у сервиса может не быть
 * (поле `password_encrypted` IS NULL), provision требует `force_password=true`.
 */
export type ServerAccountSource = "managed" | "discovered";

/** Карточка аккаунта в ответе GET/POST/PATCH /server-accounts. */
export interface ServerAccount {
  id: string;
  server_ids: string[];
  department_id: string;
  login: string;
  source: ServerAccountSource;
  has_sudo: boolean;
  unix_groups: string[];
  linked_user_id: string | null;
  shell: string | null;
  home_dir: string | null;
  is_active: boolean;
  password_rotated_at: Iso8601 | null;
  /**
   * Base64(plaintext) пароля. Присутствует только когда у вызывающего есть
   * action `view_password`; иначе backend возвращает `null`. Сырого
   * `password_encrypted` в ответе нет никогда.
   */
  password_b64: string | null;
  /**
   * Base64(plaintext) ПРЕДЫДУЩего пароля. Backend отдаёт его (тоже под
   * `view_password`), пока аккаунт в переходном состоянии
   * `credentials_pending_apply` — то есть пароль уже сменён в БД, но ещё не
   * раскатан на часть серверов. Нужен оператору, чтобы подключиться к ещё не
   * обновлённым боксам. Когда переходного периода нет — `null`/отсутствует.
   */
  previous_password_b64?: string | null;
  /**
   * True, пока новый пароль не раскатан на все привязанные серверы
   * (есть `previous_password_b64`, действующий для части боксов).
   */
  credentials_pending_apply?: boolean;
  /** Отпечаток публичного SSH-ключа аккаунта (если ключ выдан). */
  ssh_key_fingerprint?: string | null;
  /** Публичный SSH-ключ (формат authorized_keys), если выдан. */
  ssh_public_key?: string | null;
  /**
   * Приватный SSH-ключ — приходит ТОЛЬКО в ответе на создание с
   * `ssh_mode: "generate"`, ровно один раз. В обычных GET его нет.
   */
  ssh_private_key?: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело POST /server-accounts. */
export interface ServerAccountCreateRequest {
  /**
   * Серверы для привязки. Можно не передавать или передать пустой массив —
   * тогда аккаунт заводится без привязок (хранимый креден), серверы
   * добавляются позже через linkAccountServers.
   */
  server_ids?: string[];
  login: string;
  /**
   * base64(plaintext). Если не задан — backend сгенерирует
   * `secrets.token_urlsafe(32)`.
   */
  password_b64?: string | null;
  has_sudo?: boolean;
  unix_groups?: string[];
  linked_user_id?: string | null;
  shell?: string | null;
  home_dir?: string | null;
  /**
   * Режим SSH-ключа при создании.
   *  - `generate` — backend генерирует пару, приватный ключ возвращает один раз
   *    в `ssh_private_key` ответа;
   *  - `supply` — публичный ключ передаётся в `ssh_public_key`, приватный
   *    остаётся у оператора;
   *  - `null` / отсутствует — аккаунт заводится без SSH-ключа.
   */
  ssh_mode?: "generate" | "supply" | null;
  /** Публичный ключ для `ssh_mode: "supply"` (формат authorized_keys). */
  ssh_public_key?: string | null;
  /**
   * Приватный ключ в base64(plaintext) — опционально и только при
   * `ssh_mode: "supply"`. Нужен, чтобы платформа могла подключаться к аккаунту
   * через веб-консоль; без него консоль для этого аккаунта недоступна. Backend
   * хранит его в зашифрованном виде и наружу больше не отдаёт.
   */
  ssh_private_key_b64?: string | null;
}

/**
 * Способ выдачи SSH-ключа: сгенерировать новую пару на стороне сервиса либо
 * принять готовый публичный ключ оператора.
 */
export type SshKeyMode = "generate" | "supply";

/**
 * Тело POST /server-accounts/{id}/ssh_key.
 *
 * `generate` — backend создаёт пару, в ответе один раз отдаёт приватный ключ.
 * `supply` — оператор передаёт готовый публичный ключ, приватный сервису не
 * известен (поле в ответе пустое). Раскатка на привязанные серверы — на
 * стороне backend автоматически. `/rotate_ssh_key` тела не принимает (см.
 * `rotateAccountSshKey`).
 */
export interface SshKeyRequest {
  ssh_mode: SshKeyMode;
  /** Обязателен для `ssh_mode: "supply"`; формат authorized_keys. */
  ssh_public_key?: string | null;
}

/** Один задиспатченный per-server элемент в fan-out ответе SSH-ключа. */
export interface AccountKeyTask {
  server_id: string;
  task_id: string;
}

/** Сервер, на который раскатка SSH-ключа не поставлена. */
export interface AccountKeySkipped {
  server_id: string;
  reason: string;
}

/**
 * Ответ на выдачу/ротацию SSH-ключа (`AccountKeyFanoutResponse`).
 *
 * `ssh_private_key` присутствует при `ssh_mode: "generate"` (ssh_key) и всегда
 * при ротации — РОВНО ОДИН РАЗ, повторно backend его не отдаёт. `tasks` —
 * задиспатченная раскатка на привязанные серверы, `skipped` — серверы, на
 * которые задача не поставлена. Поля `fingerprint` backend не возвращает.
 */
export interface SshKeyResponse {
  id: string;
  login: string;
  ssh_public_key: string | null;
  /** Plaintext приватного ключа — только при generate / ротации, один раз. */
  ssh_private_key?: string | null;
  tasks: AccountKeyTask[];
  skipped: AccountKeySkipped[];
}

/**
 * Тело POST /server-accounts/{id}/recreate_login.
 *
 * Запрашивается, когда обычный PATCH `login` отбит 409 `LOGIN_LOCKED`
 * (аккаунт уже present на сервере). Backend удаляет OS-аккаунт со всех
 * привязанных серверов (вместе с `$HOME`) и заводит заново под новым login.
 */
export interface RecreateLoginRequest {
  login: string;
}

/** Одна задиспатченная per-server операция в сводке recreate_login. */
export interface RecreateLoginTask {
  server_id: string;
  operation: string;
  task_id: string;
}

/** Сервер, на который операция recreate_login не поставлена. */
export interface RecreateLoginSkipped {
  server_id: string;
  reason: string;
}

/**
 * Ответ POST /server-accounts/{id}/recreate_login (202).
 *
 * Сводка диспатча: `deprovision` сносит старый OS-аккаунт (с `$HOME`),
 * `provision` заводит заново под `new_login`. `skipped` — серверы, на которые
 * операция не поставлена. Сам аккаунт endpoint не отдаёт — карточку надо
 * перезапросить отдельно.
 */
export interface RecreateLoginDispatch {
  id: string;
  old_login: string;
  new_login: string;
  deprovision: RecreateLoginTask[];
  provision: RecreateLoginTask[];
  skipped: RecreateLoginSkipped[];
}

/**
 * Тело PATCH /server-accounts/{id}.
 *
 * Пароль сюда не входит — для него отдельный `/rotate_password`. Привязка/
 * отвязка серверов — через `/servers` под-операции. Поля `is_active` тоже
 * нет: backend держит её для GET, но запрещает менять через PATCH.
 *
 * `login` — DB-rename: проходит только пока аккаунт нигде не present на
 * серверах. Если present — backend отбивает 409 `LOGIN_LOCKED`, и
 * переименование делается через `/recreate_login`.
 */
export interface ServerAccountUpdateRequest {
  login?: string | null;
  has_sudo?: boolean | null;
  unix_groups?: string[] | null;
  linked_user_id?: string | null;
  shell?: string | null;
  home_dir?: string | null;
}

// ── os-versions ─────────────────────────────────────────────────────────────

/**
 * Карточка OS-версии в каталоге (ответ GET/POST/PATCH /os-versions).
 *
 * `id` имеет префикс `osv_`. `repositories` — apt/yum URL'ы, валидируются на
 * backend'е (http(s), непустой host, до 64 штук).
 */
export interface OsVersion {
  id: string;
  name: string;
  description: string | null;
  repositories: string[];
  discovered_at: Iso8601;
  updated_at: Iso8601;
}

/** Тело POST /os-versions. `name` уникален в каталоге. */
export interface OsVersionCreateRequest {
  name: string;
  description?: string | null;
  repositories?: string[];
}

/** Тело PATCH /os-versions/{id}. Все поля опциональны. */
export interface OsVersionUpdateRequest {
  name?: string | null;
  description?: string | null;
  repositories?: string[] | null;
}

/**
 * Тело POST /servers/{server_id}/os-sync — ручная смена `servers.os_version_id`.
 * `null` сбрасывает версию (например, после переустановки до инвентаризации).
 */
export interface ServerOsSyncRequest {
  os_version_id: string | null;
}

// ── permissions ─────────────────────────────────────────────────────────────

/**
 * Типы сущностей матрицы прав. Сводка `ENTITY_ACTIONS` из
 * `server_service/src/core/constants.py`. Строковый union — backend в любой
 * момент может расширить (например, `ssh_key`), и мы хотим, чтобы лишний
 * вариант с сервера не валил типы; `string` хвост держит дверь приоткрытой.
 */
export type EntityType =
  | "server"
  | "server_account"
  | "os_version"
  | "ipmi_controller"
  | "permission"
  | "task"
  | (string & {});

/**
 * Имя роли. Системные: `guest`/`reader`/`operator`/`admin`. Кастомные
 * создаются `account_admin`'ом или department-admin'ом и не предопределены —
 * поэтому хвост `string`. Backend хранит как обычную строку до 64 символов.
 */
export type RoleName =
  | "guest"
  | "reader"
  | "operator"
  | "admin"
  | (string & {});

/**
 * Имя действия. Полный whitelist — `Action` enum в
 * `server_service/src/core/constants.py`; перечислены наиболее часто
 * используемые, хвост `string` оставлен под рост каталога без правки UI.
 */
export type ActionName =
  | "view"
  | "create"
  | "update"
  | "delete"
  | "busy_acquire"
  | "busy_release"
  | "os_sync"
  | "power_on"
  | "power_off"
  | "power_reboot"
  | "power_status"
  | "inventory_trigger"
  | "inventory_submit"
  | "prepare_callback"
  | "view_drift"
  | "view_password"
  | "rotate_password"
  | "grant_sudo"
  | "provision_on_host"
  | "view_credentials"
  | "rotate_credentials"
  | "permission_grant"
  | "permission_revoke"
  | "cancel"
  | (string & {});

/**
 * Одна строка матрицы `entity_permissions` (Pydantic `PermissionResponse`).
 *
 * `department_id` — scope-дискриминатор: `null` = system-wide grant
 * (встроенные роли), строка = per-department. `granted_by` — `null` для
 * seed-данных.
 */
export interface PermissionEntry {
  id: string;
  entity_type: EntityType;
  role: RoleName;
  action: ActionName;
  department_id: string | null;
  granted_by: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  /** Появляется только при `describe=true` (`PermissionDescribedResponse`). */
  entity_description?: string;
  /** Появляется только при `describe=true`. */
  action_description?: string;
  /** Появляется только при `describe=true`. CRITICAL-аудит при изменении. */
  sensitive?: boolean;
}

/** Действие в каталоге (`CatalogAction`). */
export interface PermissionCatalogAction {
  action: ActionName;
  description: string;
  /** Чувствительное действие — CRITICAL severity в audit. */
  sensitive: boolean;
  /** Служебный callback воркера; людям обычно не выдаётся. */
  worker_only: boolean;
}

/** Сущность каталога (`CatalogEntity`) — описание и набор её действий. */
export interface PermissionCatalogItem {
  entity_type: EntityType;
  description: string;
  actions: PermissionCatalogAction[];
}

/** Envelope для GET /permissions и GET /permissions/{entity_type}. */
export interface PermissionListResponse {
  items: PermissionEntry[];
  total: number;
  /** True — строки обогащены описаниями (`describe=true`). */
  described: boolean;
}

/**
 * Body PUT /permissions/{entity_type}/{role}/{action}.
 *
 * `target_department_id` опционален; caller обязан либо опустить, либо
 * передать собственный `department_id`, иначе 403 DEPARTMENT_ISOLATION.
 */
export interface PermissionGrantRequest {
  target_department_id?: string | null;
}

// ── resource-permissions (instance-уровневый ACL) ────────────────────────────

/**
 * Типы ресурсов, поддерживающих инстанс-уровневый ACL
 * (`resource_role_permissions`). Точечный грант роли имеет смысл только для
 * сущностей с реальными строками в БД — `server` и `server_account`. Зеркало
 * `RESOURCE_ACL_TYPES` из `server_service/src/core/constants.py`.
 */
export type ResourceAclType = "server" | "server_account";

/**
 * Действия, которые НЕЛЬЗЯ привязать к конкретному инстансу — они остаются
 * только в глобальном слое (`entity_permissions`). Зеркало
 * `_NON_INSTANCE_ACTIONS` из `server_service/src/core/constants.py`: `create`
 * (на момент проверки инстанса ещё нет), callback-действия воркера и служебные
 * гранты отдела. Инстанс-редактор фильтрует их из каталога, а backend
 * отбивает PUT на них 422 `ACTION_NOT_INSTANCE_GRANTABLE`.
 */
export const NON_INSTANCE_ACTIONS: ReadonlySet<ActionName> = new Set<ActionName>([
  "create",
  "inventory_submit",
  "provision_on_host",
  "prepare_callback",
  "view_management_credentials",
  "manage_ignored_logins",
]);

/** True iff `action` можно выдать инстанс-грантом (а не только тип-wide). */
export function isInstanceGrantable(action: ActionName): boolean {
  return !NON_INSTANCE_ACTIONS.has(action);
}

/**
 * Одна строка инстанс-ACL (Pydantic `ResourcePermissionResponse`).
 *
 * `id` с префиксом `rrp_`. Субъект гранта — роль (`role`); `department_id` —
 * scope строки (None — system-wide, иначе отдел ресурса). `granted_by` — `null`
 * для seed-грантов.
 */
export interface ResourcePermissionEntry {
  id: string;
  resource_type: ResourceAclType | (string & {});
  resource_id: string;
  role: RoleName;
  action: ActionName;
  department_id: string | null;
  granted_by: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Envelope `GET .../by-resource/...` и `.../by-role/...`. */
export interface ResourcePermissionListResponse {
  items: ResourcePermissionEntry[];
  total: number;
}

/**
 * Режим распространения инстанс-грантов на цели:
 *  - `merge` — добавить недостающие `(role, action)` образца, лишние на цели
 *    оставить;
 *  - `mirror` — привести цель к точной копии образца (добавить недостающее +
 *    удалить лишнее; требует и grant, и revoke прав).
 */
export type ResourcePropagateMode = "merge" | "mirror";

/** Тело `POST .../{resource_type}/{source_resource_id}/propagate`. */
export interface ResourcePropagateRequest {
  /** Цели того же типа, что и образец. Без самого образца. 1..500. */
  target_resource_ids: string[];
  /** merge (дефолт) | mirror. */
  mode: ResourcePropagateMode;
}

/** Сводка propagate по одной цели. */
export interface ResourcePropagateTargetSummary {
  resource_id: string;
  /** Сколько `(role, action)` добавлено. */
  added: number;
  /** Сколько удалено (только `mirror`; для `merge` — 0). */
  removed: number;
}

/** Ответ `POST .../propagate` (Pydantic `ResourcePropagateResponse`). */
export interface ResourcePropagateResponse {
  source_resource_id: string;
  resource_type: ResourceAclType | (string & {});
  mode: ResourcePropagateMode;
  /** Сколько инстанс-грантов у образца. */
  source_grant_count: number;
  targets: ResourcePropagateTargetSummary[];
}

// ── misc ────────────────────────────────────────────────────────────────────

/**
 * Ответ `POST /servers/{id}/installed-packages` — диспатч SSH-пробы пакетов.
 *
 * Endpoint возвращает `task_id` сразу (HTTP 202), фактический список пакетов
 * собирается worker'ом из `dpkg-query`/`rpm -qa` и пишется в `task.result`.
 * UI после dispatch'а опрашивает task-row, чтобы получить итоговый
 * `{packages: [{name, version}, ...]}`.
 */
export interface InstalledPackagesResult {
  task_id: string;
  status: string;
}

/**
 * Тело `POST /servers/{id}/installed-packages` — фильтр-шаблон.
 *
 * `pattern` — shell-glob (не regex): `htop`, `linux-image*`, `*-dev`. Default
 * на стороне backend — `*` (все пакеты). Допустимые символы:
 * `[A-Za-z0-9._\-+*?\[\]]+`, иначе 400 `INVALID_PATTERN`.
 */
export interface InstalledPackagesRequest {
  pattern?: string;
}

/** Один пакет в результате задачи installed-packages (`task.result.packages`). */
export interface PackageInfo {
  name: string;
  version: string;
}

/**
 * Одна строка истории `GET /servers/{id}/packages/history` — прошлый
 * live-запрос пакетов сервера. Источник — `installed_packages.list`-задача
 * воркера: `pattern`/`patterns` берутся из её payload'а (что запрашивали),
 * `packages`/`package_count` — из `task.result` (что нашёл worker). У ещё не
 * завершённых запросов (`queued`/`running`) `packages` и `package_count`
 * приходят `null` — они появятся при финализации задачи.
 */
export interface PackageHistoryEntry {
  task_id: string;
  /** queued / running / succeeded / failed / cancelled. */
  status: string;
  /** Одиночный shell-glob из payload'а (raw). */
  pattern: string | null;
  /** Список glob'ов (OR-матч), если запрашивали несколько. */
  patterns: string[] | null;
  /** user_id инициатора (task.created_by). */
  requested_by: string | null;
  /** Момент постановки запроса (ISO-8601, UTC). */
  requested_at: string;
  /** Момент завершения; null пока запрос не терминальный. */
  finished_at: string | null;
  /** Сколько пакетов нашёл worker; null пока результата нет. */
  package_count: number | null;
  /** Найденные пакеты; null пока запрос не завершён. */
  packages: PackageInfo[] | null;
  /** Текст ошибки для failed-запросов. */
  last_error: string | null;
}

/**
 * Тело `POST /servers/installed-packages/bulk` — массовый запрос пакетов.
 *
 * `patterns` — список shell-glob'ов (`ssh*`, `*libs*`); пусто → `*`. Поле
 * `pattern` (одиночный glob) оставлено для обратной совместимости: backend
 * принимает оба, но UI шлёт `patterns`.
 */
export interface BulkInstalledPackagesRequest {
  server_ids: string[];
  pattern?: string;
  patterns?: string[];
}

/**
 * Статус одного сервера в массовом запросе пакетов.
 *
 *  - `ok` — задача задиспатчена, пакеты добираются поллингом `task_id`;
 *  - `prepare_required` — сервер не подготовлен, probe не поставлен;
 *  - `decommissioned` — сервер выведен из эксплуатации;
 *  - `not_found` — сервер не найден / вне scope;
 *  - `auth_failed` — управляющие креды не подошли.
 */
export type BulkPackagesServerStatus =
  | "ok"
  | "prepare_required"
  | "decommissioned"
  | "not_found"
  | "auth_failed"
  | (string & {});

/** Один сервер в ответе массового запроса пакетов. */
export interface BulkPackagesServerResult {
  server_id: string;
  hostname: string | null;
  os_version_id: string | null;
  status: BulkPackagesServerStatus;
  task_id: string | null;
  /** Может прийти пустым — тогда пакеты добираются поллингом `task_id`. */
  packages: PackageInfo[];
}

/** Ответ POST /servers/installed-packages/bulk (202). */
export interface BulkInstalledPackagesResponse {
  pattern: string;
  requested: number;
  dispatched: number;
  results: BulkPackagesServerResult[];
}

/**
 * Действие массовой операции над пакетами (`POST /servers/packages/bulk-action`).
 *
 *  - `install` / `remove` — требуют непустого `packages`;
 *  - `update` — без `packages` означает upgrade всех пакетов на боксе.
 */
export type PackagesBulkActionKind = "install" | "remove" | "update";

/**
 * Тело `POST /servers/packages/bulk-action` — поставить install/remove/update
 * на набор серверов через worker.
 *
 * `packages` обязателен для `install`/`remove`; для `update` опционален
 * (пусто → upgrade всех пакетов).
 */
export interface PackagesBulkActionRequest {
  server_ids: string[];
  action: PackagesBulkActionKind;
  packages?: string[];
}

/**
 * Статус одного сервера в ответе массовой операции над пакетами.
 *
 *  - `ok` — задача задиспатчена, итог добирается поллингом `task_id`;
 *  - `prepare_required` — сервер не подготовлен, задача не поставлена;
 *  - `reserved` — сервер забронирован другим пользователем;
 *  - `decommissioned` — сервер выведен из эксплуатации;
 *  - `not_found` — сервер не найден / вне scope.
 */
export type PackagesBulkActionServerStatus =
  | "ok"
  | "prepare_required"
  | "reserved"
  | "decommissioned"
  | "not_found"
  | (string & {});

/** Один сервер в ответе массовой операции над пакетами. */
export interface PackagesBulkActionServerResult {
  server_id: string;
  hostname: string | null;
  status: PackagesBulkActionServerStatus;
  task_id?: string | null;
}

/** Ответ POST /servers/packages/bulk-action (202). */
export interface PackagesBulkActionResponse {
  action: PackagesBulkActionKind;
  packages: string[];
  requested: number;
  dispatched: number;
  results: PackagesBulkActionServerResult[];
}

/**
 * Ответ `POST /servers/{id}/users/inventory` — диспатч snapshot'а OS-юзеров.
 *
 * Endpoint отдаёт `task_id` сразу (HTTP 202). Worker заходит по SSH, читает
 * `getent passwd` / группы / sudoers, POST'ит результат в
 * `/internal/servers/{id}/users/inventory`, server_service reconcile'ит его
 * с `server_accounts`.
 */
export interface UsersInventoryResult {
  task_id: string;
  status: string;
}

/**
 * OS-юзер из полного скана бокса (`task.result.users`) — все найденные
 * учётки, независимо от того, заведены они в БД или нет. UI накладывает на
 * этот список статус по пересечению с `unknown_users`/`unlinked_existing`.
 */
export interface InventoryUser {
  login: string;
  uid: number;
  has_sudo: boolean;
  unix_groups: string[];
  shell: string | null;
  home_dir: string | null;
}

/**
 * OS-юзер, найденный на боксе ревизией, но не привязанный ни к одному
 * server_account'у и не попавший в ignore-list (системные по UID backend
 * отфильтровывает сам). Приезжает в `task.result.unknown_users`.
 */
export interface UnknownUser {
  login: string;
  uid: number;
  has_sudo: boolean;
  unix_groups: string[];
  shell: string | null;
}

/**
 * Аккаунт-кандидат на связку для логина из `unlinked_existing`. Login на
 * аккаунте не уникален в рамках отдела, поэтому на один найденный логин может
 * прийтись несколько кандидатов — оператор выбирает нужный.
 */
export interface UnlinkedExistingCandidate {
  account_id: string;
  /** Department кандидата (= department сервера). */
  department_id: string;
  /** Происхождение аккаунта: managed / discovered. */
  source: ServerAccountSource | string;
}

/**
 * OS-логин с бокса, под который в отделе сервера УЖЕ есть аккаунт, но он не
 * привязан к этому серверу. Reconcile его не создаёт и не линкует — связывание
 * делает оператор из UI. Приезжает в `task.result.unlinked_existing`.
 */
export interface UnlinkedExistingUser {
  login: string;
  uid: number;
  /** Существующие аккаунты отдела с этим login'ом (≥1). */
  candidates: UnlinkedExistingCandidate[];
}

/**
 * Тело `POST /server-accounts/import` — завести найденного на боксе юзера в БД
 * как discovered-аккаунт (`present_on_server=true`). `source` по умолчанию
 * `discovered`.
 */
export interface ImportUnknownUserRequest {
  server_id: string;
  login: string;
  has_sudo?: boolean;
  unix_groups?: string[];
  shell?: string | null;
  source?: ServerAccountSource;
}

/**
 * Запись ignore-list отдела (`GET/POST /server-accounts/ignored-logins`).
 * Логины из этого списка ревизия не показывает как незнакомые.
 */
export interface IgnoredLogin {
  id: string;
  department_id: string;
  login: string;
  reason: string | null;
  created_by: string | null;
  created_at: Iso8601;
}

/**
 * Статус worker-task'и (`tasks.status`).
 *
 * `queued`/`running` — нетерминальные (cancelable); `succeeded`/`failed`/
 * `cancelled` — терминальные. Хвост `string` оставлен на случай, если backend
 * добавит промежуточный статус (например `retrying`), чтобы не валить типы.
 */
export type TaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | (string & {});

/** Задача в терминальном статусе (succeeded/failed/cancelled) — поллить больше нечего. */
export function isTerminalTaskStatus(status: string): boolean {
  return (
    status === "succeeded" || status === "failed" || status === "cancelled"
  );
}

/**
 * Вид задачи (`tasks.kind`). Перечислены частые kind'ы для иконок/фильтра;
 * хвост `string` держит каталог открытым (backend может добавить новый kind).
 */
export type TaskKind =
  | "power.on"
  | "power.off"
  | "power.reboot"
  | "power.status"
  | "installed_packages.list"
  | "inventory.sync"
  | "users.inventory"
  | "server.prepare"
  | "account.provision"
  | "account.deprovision"
  | "account.update_on_host"
  | "account.rotate_password"
  | "ipmi.rotate_password"
  | (string & {});

/**
 * Карточка worker-task'и (`TaskRead` от server_service).
 *
 * `result`/`last_error` приходят полными только из `GET /tasks/{id}`; в
 * list-выдаче backend может их усекать/опускать. `result` — произвольный
 * JSON-объект (для installed_packages — `{packages: [...]}`).
 */
export interface TaskRead {
  id: string;
  kind: TaskKind;
  status: TaskStatus;
  server_id?: string | null;
  account_id?: string | null;
  server_hostname?: string | null;
  account_login?: string | null;
  department_id?: string | null;
  created_by?: string | null;
  created_at: Iso8601;
  started_at?: Iso8601 | null;
  finished_at?: Iso8601 | null;
  retry_count: number;
  last_error?: string | null;
  result?: Record<string, unknown> | null;
}

/**
 * Одно расхождение поля в `result.diffs` ревизии пользователей
 * (`users/inventory`): `expected` — что в БД, `found` — что реально на боксе.
 */
export interface RevisionFieldDiff<T> {
  expected: T;
  found: T;
}

/**
 * Расхождение по одному привязанному аккаунту в `task.result.diffs`.
 *
 * Присылаются только аккаунты, у которых хотя бы одно поле разошлось; набор
 * `fields` несёт лишь разошедшиеся поля. Незнакомые OS-юзеры (discovered) сюда
 * не попадают — это отдельный сценарий.
 */
export interface RevisionAccountDiff {
  account_id: string;
  login: string;
  fields: {
    has_sudo?: RevisionFieldDiff<boolean>;
    unix_groups?: RevisionFieldDiff<string[]>;
    shell?: RevisionFieldDiff<string | null>;
  };
}

/** Параметры фильтрации `GET /tasks`. */
export interface ListTasksQuery {
  status?: TaskStatus | "";
  kind?: TaskKind | "";
  server_id?: string;
  created_by?: string;
  limit?: number;
  offset?: number;
}

/** Тело `POST /tasks/{task_id}/cancel` — опциональная причина отмены. */
export interface TaskCancelRequest {
  reason?: string | null;
}

/** Ответ `POST /tasks/{task_id}/cancel` — финальное состояние row. */
export interface TaskCancelResult {
  task_id: string;
  status: string;
  previous_status: string;
  cancelled_at: Iso8601;
  cancelled_by: string | null;
  cancel_reason: string | null;
}
