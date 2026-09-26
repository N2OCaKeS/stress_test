/**
 * Типы request/response для `testing_service` API (`/api/testing/v1/*`).
 *
 * Изначально файл нёс только минимум для кнопки «Живой лог теста» в консоли
 * сервера (§8.6 плана миграции), затем расширен под полный клиент backend'а.
 * Источник истины — Pydantic-схемы в `testing_service/src/schemas/` и роутеры
 * в `testing_service/src/api/v1/endpoints/`, сверено построчно, не выдумано.
 * UI-страницы (`src/pages/testing/*`) строятся поверх этих типов.
 */

/** ISO-8601 UTC timestamp. */
export type Iso8601 = string;

/** Offset-envelope list-эндпоинтов testing_service (`{items,total,limit,offset}`). */
export interface TestingPaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Тривиальный `{ok: true}` — ответ delete-эндпоинтов сервиса. */
export interface TestingOkResponse {
  ok: boolean;
}

/**
 * Минимум карточки стенда, нужный, чтобы по `server_id` найти `stand_id`
 * (`GET /test-stands?server_id=`).
 */
export interface TestStandSummary {
  id: string;
  server_id: string;
  department_id: string;
  queue_enabled: boolean;
  is_active: boolean;
}

/**
 * Состояние элемента очереди (`src/core/constants.py::QueueItemState`).
 *
 * `skipped` — терминальное, тест прерван без исхода и слот стенда освобождён.
 * `paused` — не терминальное: тест прерван, стенд остаётся занятым и стоит,
 * пока не позовут `POST /test-stands/{id}/resume-queue`.
 * `timed_out` — терминальное, разновидность `failed`: SSH-команда упёрлась в
 * `command_timeout`, а не в ненулевой код возврата/обрыв соединения.
 * `awaiting_verdict` — не терминальное: SSH-сессия закончилась,
 * стенд ещё занят, сервис ждёт статус теста из Zephyr.
 */
export type QueueItemState =
  | "queued"
  | "preparing"
  | "ready"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "paused"
  | "timed_out"
  | "awaiting_verdict";

/** Итог элемента очереди; `unknown` — результат не определён. */
export type QueueVerdict = "passed" | "failed" | "unknown";

/** Источник исхода теста (`test_definitions.verdict_source`). */
export type VerdictSource = "zephyr" | "exit_code";

/**
 * Как токены команды склеиваются в `dates.conf`: `shell` —
 * `shlex.quote` каждого токена (по умолчанию), `legacy` — кавычки только у
 * токенов с пробелом (как `backup_image.py`), `raw` — без экранирования.
 */
export type DatesQuoting = "shell" | "legacy" | "raw";

/** Исход, в который переводится статус Zephyr (`zephyr_status_mappings.outcome`). */
export type VerdictOutcome = "passed" | "failed" | "not_finished";

export interface ZephyrStatusMappingItem {
  zephyr_status: string;
  outcome: VerdictOutcome;
}

/** Ответ `GET/PUT/DELETE /zephyr-status-mappings/{department_id}`. */
export interface ZephyrStatusMapping {
  department_id: string;
  /** У отдела своих строк нет — действует набор по умолчанию. */
  is_default: boolean;
  items: ZephyrStatusMappingItem[];
}

/** Элемент очереди в терминальном состоянии (succeeded/failed/timed_out/skipped) — поллить больше нечего. */
export function isTerminalQueueItemState(state: string): boolean {
  return state === "succeeded" || state === "failed" || state === "timed_out" || state === "skipped";
}

/**
 * Ответ `GET /test-stands/{id}/current-queue-item` — активный (не терминальный)
 * элемент очереди стенда, если он сейчас есть.
 */
export interface QueueItemSummary {
  queue_item_id: string;
  state: QueueItemState | string;
  test_id: string;
  started_at: Iso8601 | null;
  /** Заказанное, но ещё не подтверждённое воркером прерывание. */
  interrupt_action?: "skip" | "pause" | null;
  /** Оценка освобождения стенда (`started_at` + таймаут теста), не гарантия. */
  estimated_finish_at?: Iso8601 | null;
}

// ── global-variables ──────────────────────────────────────────────────────

/** Откуда берётся значение переменной в момент резолва (`GlobalVariableSource`). */
export type GlobalVariableSource =
  | "launch_context"
  | "static"
  | "per_test_override"
  | "secret_service"
  | "template"
  | "test_field"
  | "stand"
  | "department_integration"
  | "os_version"
  | "test_account"
  | "zephyr_folder"
  | "stand_ref";

/**
 * `source_ref` переменной — на что она ссылается в своём источнике:
 * `{"template": "…{CODE}…", "when"?}`, `{"field", "fallback"?}`,
 * `{"field", "fallback"?, "credential_part"}`, `{"field", "segments", "uu_segments"}`,
 * `{"value"}`. Форма зависит от `source`, поэтому здесь — свободный объект.
 */
export type GlobalVariableSourceRef = Record<string, unknown>;

/** Тип значения переменной (`GlobalVariableValueType`). */
export type GlobalVariableValueType = "string" | "integer" | "boolean";

/** Карточка глобальной переменной конструктора команд. */
export interface GlobalVariable {
  id: string;
  code: string;
  label: string;
  source: GlobalVariableSource | string;
  /** Ссылка источника; `null` — у источников без ссылки. */
  source_ref?: GlobalVariableSourceRef | null;
  value_type: GlobalVariableValueType | string;
  choices_source: string | null;
  is_sensitive: boolean;
  description: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело `POST /global-variables`. */
export interface GlobalVariableCreateRequest {
  code: string;
  label: string;
  source: GlobalVariableSource;
  source_ref?: GlobalVariableSourceRef | null;
  value_type?: GlobalVariableValueType;
  choices_source?: string | null;
  is_sensitive?: boolean;
  description?: string | null;
}

/** Тело `PATCH /global-variables/{id}` — все поля опциональны. */
export interface GlobalVariableUpdateRequest {
  code?: string;
  label?: string;
  source?: GlobalVariableSource;
  source_ref?: GlobalVariableSourceRef | null;
  value_type?: GlobalVariableValueType;
  choices_source?: string | null;
  is_sensitive?: boolean;
  description?: string | null;
}

/** Один вариант значения переменной в резолве `choices`. */
export interface ChoiceItem {
  value: string;
  label: string;
}

/** Ответ `GET /global-variables/{id}/choices`. */
export interface ChoicesResponse {
  items: ChoiceItem[];
  choices_source: string;
}

/** Колонка интеграций отдела, на которую может сослаться переменная. */
export interface DepartmentIntegrationFieldOption {
  field: string;
  /** `*_credential_id` — ссылка на credential secret_service, нужен `credential_part`. */
  is_credential: boolean;
}

/** Ответ `GET /global-variables/source-options` — допустимые значения `source_ref`. */
export interface GlobalVariableSourceOptions {
  sources: string[];
  test_fields: string[];
  stand_fields: string[];
  /** `stand_ref.field`: поле конкретного стенда `stand_ref.stand_id`. */
  stand_ref_fields: string[];
  department_integration_fields: DepartmentIntegrationFieldOption[];
  credential_parts: string[];
  os_version_fields: string[];
  test_account_fields: string[];
  zephyr_folder_fields: string[];
  template_conditions: string[];
}

// ── test-definitions ──────────────────────────────────────────────────────

/** Карточка теста каталога. */
export interface TestDefinition {
  id: string;
  code: string;
  full_name: string;
  category: string | null;
  owner: string | null;
  readiness: string | null;
  /** Режим безопасности Astra, под которым тест исполняется — фиксирован на тесте. */
  mode: string;
  department_id: string | null;
  pinned_stand_id: string | null;
  changelog_component: string | null;
  timeout_seconds: number | null;
  /** Приоритет для ключа `priority` правила сортировки прогона РЦ, по умолчанию 0. */
  priority?: number;
  /** Откуда брать исход теста: статус в Zephyr (по умолчанию) или код выхода. */
  verdict_source?: VerdictSource;
  /** Профиль запуска; `null` — профиль отдела по умолчанию. */
  launch_profile_id?: string | null;
  /** Профиль подготовки стенда; `null` — профиль отдела по умолчанию. */
  provisioning_profile_id?: string | null;
  /** Шаг настройки стенда первого шага теста; `null` — шага нет. */
  stand_setup?: StandSetup | null;
  /** Короткое имя — его подставляет `TEST_SHORT_NAME`; пусто — полное имя. */
  short_name?: string | null;
  /** Экранирование токенов в dates.conf. */
  dates_quoting?: DatesQuoting;
  /** `$5` starter.sh первого шага теста. */
  starter_suffix?: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело `POST /test-definitions`. */
export interface TestDefinitionCreateRequest {
  code: string;
  full_name: string;
  category?: string | null;
  owner?: string | null;
  readiness?: string | null;
  mode?: string;
  department_id?: string | null;
  pinned_stand_id?: string | null;
  changelog_component?: string | null;
  timeout_seconds?: number | null;
  priority?: number;
  verdict_source?: VerdictSource;
  launch_profile_id?: string | null;
  provisioning_profile_id?: string | null;
  stand_setup?: StandSetup | null;
  short_name?: string | null;
  dates_quoting?: DatesQuoting;
}

/** Тело `PATCH /test-definitions/{id}` — все поля опциональны. */
export interface TestDefinitionUpdateRequest {
  code?: string;
  full_name?: string;
  category?: string | null;
  owner?: string | null;
  readiness?: string | null;
  mode?: string;
  department_id?: string | null;
  pinned_stand_id?: string | null;
  changelog_component?: string | null;
  timeout_seconds?: number | null;
  priority?: number;
  verdict_source?: VerdictSource;
  launch_profile_id?: string | null;
  provisioning_profile_id?: string | null;
  stand_setup?: StandSetup | null;
  short_name?: string | null;
  dates_quoting?: DatesQuoting;
}

// ── test-command-args ─────────────────────────────────────────────────────

/** Тип слота конструктора команд (`CommandArgKind`). */
export type CommandArgKind = "literal" | "variable";

/** Один слот команды теста. */
export interface TestCommandArg {
  id: string;
  test_id: string;
  /** Шаг теста, которому принадлежит слот. */
  step_id?: string;
  position: number;
  kind: CommandArgKind | string;
  literal_value: string | null;
  variable_id: string | null;
  override_value: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Тело `POST /test-definitions/{test_id}/args`. */
export interface TestCommandArgCreateRequest {
  /** Шаг теста; не задан — первый шаг. */
  step_id?: string | null;
  position?: number | null;
  kind: CommandArgKind;
  literal_value?: string | null;
  variable_id?: string | null;
  override_value?: string | null;
}

/** Тело `PATCH /test-definitions/{test_id}/args/{arg_id}` — все поля опциональны. */
export interface TestCommandArgUpdateRequest {
  position?: number;
  kind?: CommandArgKind;
  literal_value?: string | null;
  variable_id?: string | null;
  override_value?: string | null;
}

// ── шаги многоступенчатого теста ──────────────────────────────────

/**
 * `full` — команда запуска профиля (starter.sh с клонированием ветки);
 * `rerun` — повторный запуск уже склонированного кода (скрипт повторного
 * запуска профиля запуска).
 */
export type StepRunMode = "full" | "rerun";

/** Шаг теста: свои слоты команды, `$5` starter.sh, способ запуска и настройка стенда. */
export interface TestStep {
  id: string;
  test_id: string;
  position: number;
  name: string;
  starter_suffix: string | null;
  run_mode: StepRunMode;
  /** Настройка стенда перед шагом; у первого — в prepare-for-test. */
  stand_setup: StandSetup | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Тело `POST /test-definitions/{test_id}/steps`. */
export interface TestStepCreateRequest {
  name?: string;
  /** Не задан — как у первого шага. */
  starter_suffix?: string | null;
  run_mode?: StepRunMode;
  stand_setup?: StandSetup | null;
  position?: number | null;
  /** Скопировать слоты команды другого шага этого теста. */
  copy_args_from_step_id?: string | null;
}

/** Тело `PATCH /test-definitions/{test_id}/steps/{step_id}`. */
export interface TestStepUpdateRequest {
  name?: string;
  starter_suffix?: string | null;
  run_mode?: StepRunMode;
  stand_setup?: StandSetup | null;
}

// ── test-stands (полная карточка) ─────────────────────────────────────────

/**
 * Полная карточка стенда — в отличие от `TestStandSummary` несёт все поля
 * ответа `test_stand.py`, включая живое обогащение сервером (только в
 * `GET /test-stands/{id}`).
 */
/** Тип стенда: физический сервер (ACS) или ВМ server_service (откат снимка). */
export type TestStandTargetType = "server" | "vm";

export interface TestStand {
  id: string;
  /** Отдаётся всегда; необязателен в типе для фикстур до (нет поля — `server`). */
  target_type?: TestStandTargetType;
  /** `Server.id` — у физического стенда; у ВМ-стенда `null`. */
  server_id: string | null;
  /** `Vm.id` — у ВМ-стенда; у физического `null`. */
  vm_id?: string | null;
  department_id: string;
  queue_enabled: boolean;
  is_active: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
  /** Живая карточка сервера/ВМ из server_service — только в GET одного стенда. */
  server: Record<string, unknown> | null;
  /** `true`, если live-вызов к server_service не удался. */
  server_unavailable: boolean;
}

/** Тело `POST /test-stands`: `server_id` для `target_type=server`, `vm_id` для `vm`. */
export interface TestStandCreateRequest {
  target_type?: TestStandTargetType;
  server_id?: string;
  vm_id?: string;
  queue_enabled?: boolean;
  is_active?: boolean;
}

/** Снимок ВМ-стенда и версия ОС, прочитанная из его имени по шаблонам. */
export interface TestStandVmSnapshot {
  snapshot_id: string;
  name: string;
  kind: string;
  os_version: string | null;
  snapshot_mode: string | null;
  is_current: boolean;
  /** Версия как в имени; `null` — имя не подошло ни к одному шаблону. */
  version_name: string | null;
  /** Версия после нормализации (`1710rc52` → `1.7.10.52`) — по ней ищет server_service. */
  normalized_version: string | null;
  mode: string | null;
  template: string | null;
}

/** `GET /test-stands/{id}/vm-snapshots` — сопоставление снимков ВМ и версий ОС. */
export interface TestStandVmSnapshots {
  stand_id: string;
  vm_id: string;
  templates: string[];
  snapshots: TestStandVmSnapshot[];
}

/** Тело `PATCH /test-stands/{id}` — изменяемы только эти два поля. */
export interface TestStandUpdateRequest {
  queue_enabled?: boolean;
  is_active?: boolean;
}

/** Ответ `GET /test-stands/{id}/test-credentials` — прокси на server_service. */
export interface TestStandTestCredentials {
  exists: boolean;
  username: string | null;
  ssh_public_key: string | null;
  rotated_at: Iso8601 | null;
  /** Только при `?reveal=true`. */
  password_b64: string | null;
  /** Только при `?reveal=true`. */
  ssh_private_key_b64: string | null;
}

// ── department-test-settings ──────────────────────────────────────────────

/** Настройки тестирования отдела (`GET` никогда не 404 — дефолты при `id: null`). */
export interface DepartmentTestSettings {
  id: string | null;
  department_id: string;
  retry_enabled: boolean;
  /** Подсказка логина тестовой учётки (после — зеркало логина из credential). */
  test_username: string;
  /** Ссылка на тестовую учётку в secret_service; задаётся через `/department-test-account`. */
  test_account_credential_id?: string | null;
  activity_report_auto_generate: boolean;
  /**
   * Порядок постановки состава прогона РЦ в очередь стенда.
   * Дефолт — легаси: режим → ядро → имя тест-кейса. Одиночные/debug-запуски
   * правилу не подчиняются (FIFO).
   */
  campaign_sort_rule: CampaignSortRuleItem[];
  /** Проверка внешних сервисов перед запуском теста; легаси-дефолты, если не задано. */
  preflight: PreflightSettings;
  /** Вердикт из Zephyr: сколько ждать итогового статуса после конца SSH-сессии, сек (2100). */
  zephyr_verdict_wait_seconds: number;
  /** Как часто опрашивать Zephyr, сек (60). */
  zephyr_verdict_poll_seconds: number;
  /** Итог, если тест так и не выставил финальный статус (T3: `failed`). */
  zephyr_verdict_unfinished_outcome: "passed" | "failed";
  /** Исход запуска без прогона в Zephyr (debug): `unknown` — «не определён», `exit_code` — по коду выхода. */
  verdict_without_zephyr_run: "unknown" | "exit_code";
  /** Живой лог: как часто воркер шлёт вывод теста, сек (2.5). */
  log_chunk_interval_seconds?: number;
  /** Наибольший кусок живого лога, байт (4096). */
  log_chunk_max_bytes?: number;
  created_at: Iso8601 | null;
  updated_at: Iso8601 | null;
}

/** Ключ правила сортировки кампании — белый список `testing_service`. */
export type CampaignSortKey = "mode" | "kernel" | "test_case_name" | "test_code" | "priority";

export interface CampaignSortRuleItem {
  key: CampaignSortKey;
  direction: "asc" | "desc";
}

/**
 * Критерий доступности HTTP-пробы preflight: `"200"` — строго 200 (паритет с
 * легаси), `"lt500"` — любой ответ со статусом < 500.
 */
export type PreflightOkStatus = "200" | "lt500";

export interface PreflightHttpProbe {
  url: string;
  ok_status: PreflightOkStatus;
}

/** `department_test_settings.preflight` — та же форма уходит воркеру в claim (CONTRACTS.md C3). */
export interface PreflightSettings {
  enabled: boolean;
  http: PreflightHttpProbe[];
  dns_hosts: string[];
  dns_port: number;
  poll_interval_seconds: number;
  timeout_seconds: number;
  probe_timeout_seconds: number;
}

/** Ответ `GET /preflight/status` — ждут ли тесты отдела внешние сервисы. */
export interface PreflightStatus {
  state: "waiting" | "ok";
  /** Недоступные сервисы (объединение по ожидающим тестам). */
  unavailable: string[];
  since: Iso8601 | null;
  waiting_items: number;
}

// ── department-test-account ───────────────────────────────────────

/**
 * Тестовая учётка отдела — пользователь исполнения теста на стендах.
 * Пароль и приватный ключ backend не отдаёт никогда.
 */
export interface DepartmentTestAccount {
  department_id: string;
  configured: boolean;
  credential_id: string | null;
  /** Ссылка есть, но credential в secret_service пропал — задать заново. */
  credential_missing: boolean;
  login: string | null;
  /** Подсказка логина, пока учётка не настроена (легаси `test_username`). */
  login_hint: string;
  has_password: boolean;
  ssh_public_key: string | null;
  home_template: string;
  home: string | null;
  updated_at: Iso8601 | null;
}

/** Тело `PUT /department-test-account/{department_id}` — незаданное остаётся как есть. */
export interface DepartmentTestAccountUpdateRequest {
  login?: string;
  password?: string;
  regenerate_ssh_key?: boolean;
  home_template?: string;
}

/** Тело `PUT /department-test-settings/{department_id}` — upsert, все поля опциональны. */
export interface DepartmentTestSettingsUpdateRequest {
  retry_enabled?: boolean;
  test_username?: string;
  activity_report_auto_generate?: boolean;
  /** 1–5 ключей без повторов. */
  campaign_sort_rule?: CampaignSortRuleItem[];
  /** Заменяет объект целиком; `null` — сброс к легаси-дефолтам. */
  preflight?: PreflightSettings | null;
  zephyr_verdict_wait_seconds?: number;
  zephyr_verdict_poll_seconds?: number;
  zephyr_verdict_unfinished_outcome?: "passed" | "failed";
  verdict_without_zephyr_run?: "unknown" | "exit_code";
  log_chunk_interval_seconds?: number;
  log_chunk_max_bytes?: number;
}

// ── department-integration-settings ───────────────────────────────────────

/** Настройки интеграции отдела с Jira/Zephyr/Confluence/Bitbucket. */
export interface DepartmentIntegrationSettings {
  id: string | null;
  department_id: string;
  credential_id: string | null;
  jira_base_url: string | null;
  confluence_base_url: string | null;
  confluence_credential_id: string | null;
  bitbucket_base_url: string | null;
  bitbucket_project_key: string | null;
  bitbucket_repo_slug: string | null;
  bitbucket_credential_id: string | null;
  git_credential_id: string | null;
  jira_board_id: string | null;
  tempo_team_id: string | null;
  confluence_report_page_space: string | null;
  confluence_report_parent_page_title: string | null;
  stp_matrix_confluence_space: string | null;
  stp_matrix_confluence_root_page_title: string | null;
  /**: шаблон пути папки Zephyr; `null` — легаси `/stress_test/{RC_RELEASE}/{RC_NAME}`. */
  zephyr_folder_path_template: string | null;
  /**: шаблон имени test-run'а; `null` — легаси `{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}`. */
  zephyr_run_name_template: string | null;
  created_at: Iso8601 | null;
  updated_at: Iso8601 | null;
}

/** Тело `PUT /department-integration-settings/{department_id}` — upsert, все поля опциональны. */
export interface DepartmentIntegrationSettingsUpdateRequest {
  credential_id?: string | null;
  jira_base_url?: string | null;
  confluence_base_url?: string | null;
  confluence_credential_id?: string | null;
  bitbucket_base_url?: string | null;
  bitbucket_project_key?: string | null;
  bitbucket_repo_slug?: string | null;
  bitbucket_credential_id?: string | null;
  git_credential_id?: string | null;
  jira_board_id?: string | null;
  tempo_team_id?: string | null;
  confluence_report_page_space?: string | null;
  confluence_report_parent_page_title?: string | null;
  stp_matrix_confluence_space?: string | null;
  stp_matrix_confluence_root_page_title?: string | null;
  zephyr_folder_path_template?: string | null;
  zephyr_run_name_template?: string | null;
}

// ── jira sprint board ─────────────────────────────────────────────────────

/** Одна карточка issue на доске спринта. */
export interface JiraSprintBoardIssue {
  key: string;
  summary: string;
  status: string;
  status_category: string | null;
  assignee: string | null;
  issue_type: string | null;
}

/** Колонка доски — все issue спринта с одинаковым статусом. */
export interface JiraSprintBoardColumn {
  status: string;
  issues: JiraSprintBoardIssue[];
}

export interface JiraSprintInfo {
  id: number;
  name: string;
  start_date: string | null;
  end_date: string | null;
}

/** Ответ `GET /department-integration-settings/{department_id}/sprint-board`. */
export interface JiraSprintBoard {
  configured: boolean;
  sprint: JiraSprintInfo | null;
  columns: JiraSprintBoardColumn[];
  warning: string | null;
}

// ── department-report-members ─────────────────────────────────────────────

/** Сотрудник отдела, учитываемый HR-отчётом по активности. */
export interface DepartmentReportMember {
  id: string;
  department_id: string;
  display_name: string;
  bitbucket_username: string | null;
  jira_author_name: string | null;
  jira_tempo_worker_key: string | null;
  is_active: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело `POST /departments/{department_id}/report-members`. */
export interface DepartmentReportMemberCreateRequest {
  display_name: string;
  bitbucket_username?: string | null;
  jira_author_name?: string | null;
  jira_tempo_worker_key?: string | null;
  is_active?: boolean;
}

/** Тело `PATCH /departments/{department_id}/report-members/{member_id}` — все поля опциональны. */
export interface DepartmentReportMemberUpdateRequest {
  display_name?: string;
  bitbucket_username?: string | null;
  jira_author_name?: string | null;
  jira_tempo_worker_key?: string | null;
  is_active?: boolean;
}

// ── department-activity-reports ───────────────────────────────────────────

/** Исход попытки генерации HR-отчёта (`DepartmentActivityReportStatus`). */
export type DepartmentActivityReportStatus = "generating" | "done" | "failed";

/** Одна попытка генерации HR-отчёта в истории/сразу после запуска. */
export interface DepartmentActivityReport {
  id: string;
  department_id: string;
  period: string;
  generated_at: Iso8601;
  generated_by: string | null;
  confluence_page_id: string | null;
  status: DepartmentActivityReportStatus | string;
  error: string | null;
}

/** Тело `POST /departments/{department_id}/activity-reports/generate`. */
export interface DepartmentActivityReportGenerateRequest {
  /** Период отчёта, `'YYYY-MM'`. */
  period: string;
}

// ── test-runs ──────────────────────────────────────────────────────────────

/** Агрегатный статус кампании (`TestRunStatus`). */
export type TestRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "partially_failed";

/** Что делать с уже активной очередью стенда при запуске: в конец либо заменить. */
export type ActiveQueueMode = "append" | "replace";

/**
 * Тело `POST /test-runs` (и `POST /test-runs/preview`, где `request_id` игнорируется).
 * Режима здесь нет — он фиксирован на каждом тесте (`TestDefinition.mode`),
 * кампания может законно смешивать orel- и smolensk-тесты.
 */
export interface TestRunCreateRequest {
  os_version_id: string;
  kernel?: string;
  /**
   * Явно выбранный пул стендов. Не задан/пуст — состав кампании выводится
   * из активного состава СТП отдела для `os_version_id` (полный прогон по
   * РЦ), стенды не выбираются оператором.
   */
  test_run_stands?: string[];
  /** Официальный/финальный прогон релиза — чисто информационная метка, на допуск по СТП не влияет. */
  final?: boolean;
  /**
   * Только вместе с `test_run_stands`. Снимает и допуск по СТП, и требование
   * готовности теста — весь пул стенда уходит в очередь как есть.
   */
  debug?: boolean;
  /**
   * Только без `test_run_stands`. Перед сборкой состава расширяет СТП этого
   * РЦ до полного набора (`scope=full`), затем запускает уже расширенный состав.
   */
  full?: boolean;
  /**
   * Забрать занятые человеком стенды кампании (`busy`/`testing_done`).
   * Занятый стенд без `force` попадает в `enqueue_errors` с
   * `error_code: "STAND_BUSY"` вместо запуска; `updating` и чужой `acs` не
   * отбираются никогда (`STAND_TAKEOVER_NOT_ALLOWED`). Без роли
   * department_admin/`admin` testing_service своего отдела сервер отвечает
   * `FORCE_LAUNCH_DENIED`, а не запускает как обычно.
   */
  force?: boolean;
  /**
   * Режим для стендов, где очередь testing_service уже активна. Не задан —
   * у админа такой стенд попадает в `enqueue_errors` с `STAND_QUEUE_ACTIVE`
   * (в `details` — что стоит в очереди), у остальных тесты встают в конец.
   */
  on_active_queue?: ActiveQueueMode;
  /** Ключ идемпотентности: повтор с тем же значением и тем же телом вернёт ту же кампанию, с другим телом — 409 REQUEST_ID_CONFLICT. */
  request_id?: string;
}

/** Что произойдёт с одним тестом кампании при постановке в очередь (`TestRunPreviewEntry.action`). */
export type TestRunPreviewAction =
  | "launch"
  | "skip_debug_required"
  | "skip_stand_inactive"
  | "skip_not_in_stp"
  | "skip_stp_not_generated";

/** Один тест кампании до постановки в очередь — ответ `POST /test-runs/preview`. */
export interface TestRunPreviewEntry {
  stand_id: string;
  test_id: string;
  test_code: string;
  test_name: string;
  kernel: string;
  mode: string;
  action: TestRunPreviewAction | string;
  reason: string | null;
  /**
   * Заполнен только при `action=skip_not_in_stp` — id уже существующего
   * СТП-прогона этого контекста, куда можно добавить тест
   * (`POST /stp/test-runs/{id}/add-test`). При `skip_stp_not_generated` СТП
   * для этого контекста ещё не генерировалась — добавлять некуда.
   */
  stp_test_run_id?: string | null;
}

/** Ответ `POST /test-runs/preview` — состав кампании без побочных эффектов. */
export interface TestRunPreviewResponse {
  stands_without_tests: string[];
  entries: TestRunPreviewEntry[];
}

/** Один частичный провал постановки в очередь одного теста одного стенда кампании. */
export interface TestRunPartialError {
  stand_id: string;
  test_id: string;
  error_code: string;
  message: string;
  /** Держатель стенда (`STAND_BUSY`) или состав очереди (`STAND_QUEUE_ACTIVE`); только в ответе на создание. */
  details?: Record<string, unknown> | null;
}

/** Карточка кампании. */
export interface TestRun {
  kernels?: string[];
  id: string;
  os_version_id: string;
  /** Легаси — новые кампании этого не пишут, режим смотрите в entries[].mode. */
  mode: string | null;
  kernel: string;
  department_id: string;
  test_run_stands: string[];
  status: TestRunStatus | string;
  final: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Один частичный провал синхронизации СТП (`full=True`) — не про постановку в очередь. */
export interface TestRunStpSyncError {
  stand_id: string | null;
  error_code: string;
  message: string;
}

/** Ответ `POST /test-runs` — карточка + отчёт о частичных провалах постановки. */
export interface TestRunCreateResponse extends TestRun {
  stands_without_tests: string[];
  enqueue_errors: TestRunPartialError[];
  stp_sync_errors: TestRunStpSyncError[];
}

/** Один дочерний queue_item в детальной карточке кампании. */
export interface TestRunQueueItem {
  kernel?: string | null;
  log_status?: "available" | "rotated" | "pending" | "missing";
  retry_of_id?: string | null;
  test_run_entry_id?: string | null;
  is_current?: boolean;
  queue_item_id: string;
  stand_id: string;
  test_id: string;
  state: QueueItemState | string;
  is_retry: boolean;
  started_at: Iso8601 | null;
  finished_at: Iso8601 | null;
  error: string | null;
  /** Вердикт; `unknown` — результат не определён. */
  verdict?: QueueVerdict | null;
  zephyr_status_raw?: string | null;
}

/** Ответ `GET /test-runs/{id}` — карточка + все дочерние queue_items. */
export interface TestRunDetail extends TestRun {
  composition_source?: string;
  entries?: { id: string; test_run_id: string; stand_id: string; test_id: string; test_code: string; test_name: string; mode: string; enqueue_error_code: string | null; enqueue_error: string | null }[];
  progress?: Record<string, number>;
  queue_items: TestRunQueueItem[];
}

/** Исход попытки публикации end-of-run комментария (`RunSummaryCommentStatus`). */
export type RunSummaryCommentStatus =
  | "posted"
  | "skipped_no_blog"
  | "skipped_no_stp_page"
  | "skipped_no_rc_number"
  | "failed";

/** Ответ `GET /test-runs/{id}/summary-comment`. Пустые поля — попытки ещё не было. */
export interface RunSummaryComment {
  id: string | null;
  test_run_id: string;
  status: RunSummaryCommentStatus | string | null;
  confluence_blog_id: string | null;
  confluence_comment_id: string | null;
  stp_page_id: string | null;
  posted_at: Iso8601 | null;
  updated_at: Iso8601 | null;
}

// ── test-logs ──────────────────────────────────────────────────────────────

/** Статус сегмента лога (легаси-паттерн `dev_libs`: OK/CHANGED/FATAL). */
export type TestLogSegmentStatus = "OK" | "CHANGED" | "FATAL";

/** Один сегмент (чекпоинт/команда) лога прогона, без самого текста. */
export interface TestLogSegment {
  id: string;
  log_id: string;
  position: number;
  kind: "checkpoint" | "command" | string;
  label: string;
  command_text_masked: string | null;
  status: TestLogSegmentStatus | string;
  started_at: Iso8601;
  finished_at: Iso8601 | null;
  byte_offset_start: number;
  byte_offset_end: number | null;
}

// ── stp ────────────────────────────────────────────────────────────────────

/** Тест-кейс СТП — зеркало Zephyr Scale test-case. */
export interface StpTestCase {
  id: string;
  code: string;
  title: string;
  zephyr_id: string | null;
  department_id: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by: string | null;
}

/** Тело `POST /stp/test-cases`. */
export interface StpTestCaseCreateRequest {
  code: string;
  title: string;
  zephyr_id?: string | null;
  department_id?: string | null;
}

/** Тело `PATCH /stp/test-cases/{id}` — все поля опциональны. */
export interface StpTestCaseUpdateRequest {
  title?: string;
  zephyr_id?: string | null;
  department_id?: string | null;
}

/** Режим состава СТП (`StpCompositionScope`) — явный выбор, не выводится из RC. */
export type StpCompositionScope = "changelog" | "full";

/** Тело `POST /stp/generate`. `scope` обязателен — «Полный набор»/«По changelog». */
export interface StpGenerateRequest {
  os_version_id: string;
  mode?: string;
  kernel?: string;
  scope: StpCompositionScope;
  department_id?: string;
}

/** Ответ `GET /stp/composition` — текущий активный состав пары (отдел, РЦ). */
export interface StpComposition {
  id: string | null;
  department_id: string;
  os_version_id: string;
  scope: StpCompositionScope | null;
  revision: number;
  updated_at: Iso8601 | null;
  updated_by: string | null;
}

/** Один частичный провал генерации СТП для одного стенда. */
export interface StpGeneratePartialError {
  stand_id: string;
  error_code: string;
  message: string;
}

/** СТП-прогон (Zephyr test-run), заведённый на конкретном стенде. */
export interface StpTestRun {
  id: string;
  os_version_id: string;
  mode: string;
  kernel: string;
  stand_id: string;
  zephyr_test_run_key: string | null;
  zephyr_folder_path: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Ответ `POST /stp/generate` — заведённые прогоны + частичные ошибки по стендам. */
export interface StpGenerateResponse {
  test_runs: StpTestRun[];
  errors: StpGeneratePartialError[];
  /**: папка Zephyr этой РЦ; `error` — id получить не удалось (прогоны всё равно заведены). */
  zephyr_folder?: ZephyrFolder | null;
}

/**
 * Папка Zephyr пары (отдел, РЦ) — источник `-fti` (`FOLDER_TREE_ID`) у тестов
 *. Нет записи — `id: null` и путь по шаблону отдела.
 */
export interface ZephyrFolder {
  id: string | null;
  department_id: string;
  os_version_id: string;
  folder_path: string | null;
  folder_tree_id: string | null;
  /** Задан вручную — генерация СТП его не перезаписывает. */
  is_manual: boolean;
  resolved_at: Iso8601 | null;
  updated_by: string | null;
  updated_at: Iso8601 | null;
  error: { error_code: string; message: string } | null;
}

/** Тело `PUT /stp/zephyr-folder` — задать id папки вручную. */
export interface ZephyrFolderManualUpdateRequest {
  os_version_id: string;
  department_id?: string;
  folder_tree_id: string;
  folder_path?: string | null;
}

/** Тело `POST /stp/matrix/publish` — ручная публикация сводной СТП-таблицы одного РЦ. */
export interface StpMatrixPublishRequest {
  os_version_id: string;
  department_id?: string;
}

/** Исход публикации СТП-матрицы (`StpMatrixPublicationStatus`). */
export type StpMatrixPublicationStatus =
  | "posted"
  | "skipped_not_configured"
  | "skipped_no_test_runs"
  | "failed";

/** Ответ `POST /stp/matrix/publish`. */
export interface StpMatrixPublishResponse {
  id: string;
  department_id: string;
  os_version_id: string;
  status: StpMatrixPublicationStatus | string;
  confluence_page_id: string | null;
  confluence_parent_page_id: string | null;
  error: string | null;
  published_at: Iso8601 | null;
  updated_at: Iso8601;
}

/** Статус ячейки СТП `(stp_test_case × stp_test_run)` (`StpCellStatus`). */
export type StpCellStatus = "not_run" | "in_progress" | "pass" | "fail";

/** Одна ячейка СТП-прогона. */
export interface StpCell {
  id: string;
  stp_test_case_id: string;
  stp_test_run_id: string;
  status: StpCellStatus | string;
  is_active: boolean;
  queue_item_id: string | null;
  updated_by: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Тело `PATCH /stp/cells/{id}` — ручной override статуса. Не трогает Zephyr. */
export interface StpCellManualUpdateRequest {
  status: StpCellStatus | string;
}

/** Статус операции добавления одного теста в СТП (`StpAddTestOperationStatus`). */
export type StpAddTestOperationStatus = "pending" | "succeeded" | "failed";

/** Тело `POST /stp/test-runs/{run_id}/add-test`. */
export interface StpAddTestRequest {
  test_id: string;
}

/**
 * Ответ добавления теста в СТП (§D6/D7) — шаговое состояние долговечной
 * операции. Повторный вызов на ту же пару `(test_id, run_id)` возвращает эту
 * же строку, продолженную с первого не пройденного шага.
 */
export interface StpAddTestOperation {
  id: string;
  department_id: string;
  test_definition_id: string;
  stp_test_run_id: string;
  stp_test_case_id: string | null;
  stp_cell_id: string | null;
  zephyr_testcase_created: boolean;
  zephyr_added_to_run: boolean;
  stp_cell_created: boolean;
  life_published: boolean;
  status: StpAddTestOperationStatus | string;
  last_error: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

// ── pull СТП из life (§D8) ────────────────────────────────────────────────

/** Тело `POST /stp/pull-from-life/preview`. Чтение — ни одной записи в БД. */
export interface StpPullPreviewRequest {
  os_version_id: string;
  department_id?: string;
}

/** Сводка состава одного найденного в Zephyr test-run'а. */
export interface StpPullRunComposition {
  case_count: number;
  matched_case_count: number;
  new_case_count: number;
}

/** Один найденный в Zephyr test-run — как он будет сопоставлен/импортирован. */
export interface StpPullPreviewItem {
  zephyr_key: string;
  zephyr_link: string | null;
  name: string;
  parsed_os_version_id: string | null;
  parsed_mode: string | null;
  parsed_kernel: string | null;
  parsed_stand_token: string | null;
  stand_id: string | null;
  needs_manual_mapping: boolean;
  mapping_issue: string | null;
  already_imported: boolean;
  stp_test_run_id: string | null;
  composition: StpPullRunComposition;
}

/** Ответ `POST /stp/pull-from-life/preview`. */
export interface StpPullPreviewResponse {
  department_id: string;
  os_version_id: string;
  folder: string;
  items: StpPullPreviewItem[];
  total_found: number;
  new_count: number;
  already_imported_count: number;
  needs_manual_mapping_count: number;
}

/**
 * Тело `POST /stp/pull-from-life/import`. `zephyr_keys` пуст/не задан —
 * импортировать все test-run'ы, найденные сейчас в этой папке.
 */
export interface StpPullImportRequest {
  os_version_id: string;
  department_id?: string;
  zephyr_keys?: string[];
}

/** Ячейка, чей локальный статус разошёлся со статусом Zephyr — не перезаписана. */
export interface StpPullConflict {
  zephyr_key: string;
  test_case_key: string;
  stp_cell_id: string;
  local_status: string;
  zephyr_status: string;
}

/** Итог обработки одного test-run'а в рамках импорта. */
export interface StpPullRunResult {
  zephyr_key: string;
  status: "succeeded" | "failed" | "skipped_needs_manual_mapping" | string;
  stp_test_run_id: string | null;
  created_run: boolean;
  matched_run: boolean;
  cases_created: number;
  cases_matched: number;
  cells_created: number;
  cells_matched: number;
  conflicts: StpPullConflict[];
  mapping_issue: string | null;
  error: string | null;
}

/** Ответ `POST /stp/pull-from-life/import` — итог по каждому test-run'у + агрегаты. */
export interface StpPullImportResponse {
  department_id: string;
  os_version_id: string;
  results: StpPullRunResult[];
  created_runs: number;
  matched_runs: number;
  cases_created: number;
  cases_matched: number;
  cells_created: number;
  cells_matched: number;
  conflicts_count: number;
  skipped_count: number;
  failed_count: number;
}

// ── permissions (entity_permissions matrix) ───────────────────────────────

/**
 * Тип сущности матрицы прав. Полный список — `EntityType` enum в
 * `testing_service/src/core/constants.py`. testing_service не имеет
 * инстанс-уровневого ACL (в отличие от server_service) — вся матрица
 * тип-wide.
 */
export type TestingEntityType =
  | "global_variable"
  | "test_definition"
  | "test_stand"
  | "department_test_settings"
  | "test_run"
  | "stp_test_case"
  | "stp_test_run"
  | "stp_cell"
  | "department_integration_settings"
  | "department_report_member"
  | "department_activity_report"
  | "permission"
  | "statistics_settings"
  | (string & {});

/** Имя роли. Системные — `guest`/`admin`; остальные — кастомные, per department. */
export type TestingRoleName = "guest" | "admin" | (string & {});

/** Имя действия. Полный whitelist — `Action` enum + `ENTITY_ACTIONS` в constants.py. */
export type TestingActionName =
  | "view"
  | "create"
  | "update"
  | "delete"
  | "view_test_credentials"
  | "permission_grant"
  | "permission_revoke"
  | (string & {});

/** Одна строка матрицы `entity_permissions` в ответе (без `describe`). */
export interface TestingPermissionEntry {
  id: string;
  entity_type: TestingEntityType;
  role: TestingRoleName;
  action: TestingActionName;
  department_id: string | null;
  granted_by: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

/** Строка матрицы, обогащённая описаниями каталога (`describe=true`). */
export interface TestingPermissionDescribedEntry extends TestingPermissionEntry {
  entity_description: string;
  action_description: string;
  sensitive: boolean;
}

/** Действие в каталоге прав — имя, описание, флаги чувствительности. */
export interface TestingPermissionCatalogAction {
  action: TestingActionName;
  description: string;
  /** Чувствительное действие — CRITICAL severity в audit. */
  sensitive: boolean;
  /** Служебный callback воркера; у testing_service сейчас всегда `false` —
   * callback'и закрыты `require_internal_caller`, не матрицей. */
  worker_only: boolean;
}

/** Сущность каталога прав с описанием и набором её действий. */
export interface TestingPermissionCatalogItem {
  entity_type: TestingEntityType;
  description: string;
  actions: TestingPermissionCatalogAction[];
}

/** Envelope для `GET /permissions` и `GET /permissions/{entity_type}`. */
export interface TestingPermissionListResponse {
  items: (TestingPermissionEntry | TestingPermissionDescribedEntry)[];
  total: number;
  /** True — строки обогащены описаниями (`describe=true`). */
  described: boolean;
}

/**
 * Тело `PUT /permissions/{entity_type}/{role}/{action}`. Опустить поле —
 * grant в свой отдел (или system-wide для `account_admin`); передать свой
 * `department_id` — то же самое явно. Чужой `department_id` → 403
 * `DEPARTMENT_ISOLATION` (кроме `account_admin` — ему можно любой).
 */
export interface TestingPermissionGrantRequest {
  target_department_id?: string | null;
}

// ── statistics (§2.7, §9.3 плана миграции) ─────────────────────────────────

/** Платформенные настройки внешнего сервиса статистики (`GET/PUT /statistics/settings`). */
export interface StatisticsSettings {
  enabled: boolean;
  base_url: string | null;
}

/** Тело `PUT /statistics/settings` — частичное обновление, пустая строка в `base_url` очищает. */
export interface StatisticsSettingsUpdateRequest {
  enabled?: boolean;
  base_url?: string;
}

/** Состояние фонового пересчёта статистики (`GET /statistics/status`). */
export type StatisticsRecalcState = "idle" | "running" | "succeeded" | "failed";

/** `GET /statistics/status` — индикатор для левой панели. */
export interface StatisticsRecalcStatus {
  status: StatisticsRecalcState;
  triggered_by: "test_run" | "manual" | (string & {}) | null;
  /** Ключ семейства тестов (текущего при нескольких); `null` — пересчитывалось всё сразу. */
  category: string | null;
  /** Весь выбранный набор семейств; `null`/нет — полный пересчёт. */
  categories?: string[] | null;
  test_run_id: string | null;
  started_at: Iso8601 | null;
  finished_at: Iso8601 | null;
  error: string | null;
  updated_at: Iso8601 | null;
}

/** Тело `POST /statistics/recalculate` — не передан `department_id` → берётся отдел вызывающего. */
export interface StatisticsRecalcTriggerRequest {
  department_id?: string | null;
  /** Ключ семейства тестов из `GET /statistics/categories`; не передан — пересчёт всего. */
  category?: string | null;
  /** Несколько семейств за один запуск (модалка); пусто вместе с `category` — пересчёт всего. */
  categories?: string[] | null;
}

/**
 * Одна строка справочника семейств статистики (`GET /statistics/categories`,
 * D18/): что уходит во внешний сервис статистики одним POST'ом
 * (`path` + тело `title_statistics`/`set_of_test_types`/`comparison_*`).
 * Модалке пересчёта достаточно `key`/`label`, остальное — для страницы настроек.
 */
export interface StatisticsCategory {
  key: string;
  label: string;
  id?: string;
  path?: string;
  title_statistics?: string;
  set_of_test_types?: string[];
  comparison_list?: string[][] | null;
  comparison_kernel_list?: string[] | null;
  enabled?: boolean;
  sort_order?: number;
  created_at?: Iso8601 | null;
  updated_at?: Iso8601 | null;
  created_by?: string | null;
}

/** `GET /statistics/categories` — справочник в порядке `sort_order` (сид — восемь семейств легаси). */
export interface StatisticsCategoriesResponse {
  items: StatisticsCategory[];
}

/** Тело `POST /statistics/categories`. `key` — `[a-z][a-z0-9_]*`, после создания не меняется. */
export interface StatisticsCategoryCreateRequest {
  key: string;
  label: string;
  path: string;
  title_statistics: string;
  set_of_test_types: string[];
  comparison_list?: string[][] | null;
  comparison_kernel_list?: string[] | null;
  enabled?: boolean;
  sort_order?: number;
}

/** Тело `PATCH /statistics/categories/{id}` — частичное; `null` в `comparison_*` очищает поле. */
export type StatisticsCategoryUpdateRequest = Partial<Omit<StatisticsCategoryCreateRequest, "key">>;

// ── pool overview (§F плана 2026-09-11, доработка 2026-09-23) ───────────────

/**
 * Режим агрегации succeeded/failed, выбранный backend'ом сам (без
 * переключателя на UI — см. `testing_service/src/services/pool_overview.py`):
 * `active_run` — по незавершённой кампании отдела (пока не закончится,
 * сколько бы дней ни шла), `rolling_24h` — за последние сутки, если активных
 * кампаний нет.
 */
export type PoolOverviewMode = "active_run" | "rolling_24h";

/** Статус стенда в обзоре пула, приоритет — §F плана 2026-09-11. */
export type PoolStandStatus = "recovering" | "unreachable" | "testing" | "testing_done" | "ready" | "no_data";

/** Заголовок кампании в обзоре пула — только при `mode=active_run` и ровно одной активной кампании. */
export interface PoolOverviewTestRun {
  id: string;
  os_version_id: string;
  kernel: string;
  /** Легаси — новые кампании этого не пишут, режим теперь у каждого теста отдельно. */
  mode: string | null;
  status: string;
  final: boolean;
  created_at: Iso8601;
}

/** Один стенд пула с посчитанным статусом. */
export interface PoolOverviewStand {
  stand_id: string;
  server_id: string | null;
  /**: тип стенда; у ВМ-стенда `server_id=null`, есть `vm_id`. */
  target_type?: TestStandTargetType;
  vm_id?: string | null;
  status: PoolStandStatus;
  busy_state: string | null;
  busy_service_name: string | null;
  ping_reachable: boolean | null;
  ping_checked_at: Iso8601 | null;
}

/** `GET /pool-overview` — очередь/исходы + статусы стендов одним запросом. */
export interface PoolOverviewResponse {
  mode: PoolOverviewMode;
  test_run_id: string | null;
  test_run: PoolOverviewTestRun | null;
  remaining: number;
  running: number;
  succeeded: number;
  failed: number;
  stands: PoolOverviewStand[];
  stand_status_counts: Record<PoolStandStatus, number>;
  generated_at: Iso8601;
}

/** Один стенд в `GET /test-stands/metrics` — живые CPU/RAM с node_exporter'а. */
export interface TestStandMetricsItem {
  stand_id: string;
  cpu_percent: number;
  ram_percent: number;
}

/** `GET /test-stands/metrics` — батч живых CPU/RAM по активным стендам отдела. */
export interface TestStandMetricsResponse {
  items: TestStandMetricsItem[];
}

// ── launch-profiles ──────────────────────────────────────────────

export interface LaunchProfileClone {
  repo_url: string;
  mode: "branch" | "full";
  depth: number | null;
  credential?: "git";
}

export interface LaunchProfilePaths {
  script: string;
  dates: string;
  token: string;
  testenv_marker: string;
  command_file: string;
}

export interface LaunchProfileTestenv {
  on_value: string;
  off_value: string;
  cleanup_other: boolean;
}

/** Содержимое версии профиля запуска (тело `POST /launch-profiles/{id}/versions`). */
export interface LaunchProfileVersionInput {
  comment?: string | null;
  /** Текст starter.sh, подстановки `{{CODE}}`. */
  starter_script: string;
  clone: LaunchProfileClone;
  paths: LaunchProfilePaths;
  /** Токены по пробелам, `{CODE}` в каждом. */
  launch_command_template: string;
  /** Shell, подстановки `{{CODE}}`. */
  stop_command_template: string;
  stop_grace_seconds: number;
  use_pty: boolean;
  testenv: LaunchProfileTestenv;
  /**
   * Скрипт повторного запуска для шагов `rerun` многоступенчатого теста:
   * кладётся вместо starter.sh по тому же пути, подстановки `{{CODE}}`.
   */
  rerun_script?: string | null;
  /**
   * Дополнительные файлы на стенде: путь — `{CODE}`, содержимое —
   * `{{CODE}}`. Например `tokens.json` FreeIPA или адреса стендов сценария
   * через переменные `stand_ref`.
   */
  extra_files?: LaunchProfileExtraFile[];
}

export interface LaunchProfileExtraFile {
  path: string;
  content: string;
  /** Права файла, `0644`. */
  mode: string;
  sensitive: boolean;
}

export interface LaunchProfileVersion extends LaunchProfileVersionInput {
  id: string;
  profile_id: string;
  version: number;
  created_by: string | null;
  created_at: Iso8601;
}

export interface LaunchProfile {
  id: string;
  /** `null` — общий профиль (для всех отделов). */
  department_id: string | null;
  name: string;
  is_default: boolean;
  current_version: LaunchProfileVersion | null;
  created_at: Iso8601;
  updated_at: Iso8601;
}

// ── stand setup и provisioning-profiles ─────────────────

/** Шаг настройки стенда теста: параметры ядра и bash-скрипт (`{{CODE}}`). */
export interface StandSetup {
  kernel_cmdline_extra: string[];
  script: string;
  run_as: "root" | "test_user";
  phase: "before_kernel" | "after_boot";
  /** `null` — да, если задан скрипт или параметры ядра. */
  reboot_after: boolean | null;
  timeout_seconds: number;
}

export interface ProvisioningProfileFields {
  allowed_failed_units: string[];
  degraded_reboot_attempts: number;
  disable_pam_lastlog_inactive: boolean;
  boot_wait_timeout_seconds: number | null;
}

export interface ProvisioningProfile extends ProvisioningProfileFields {
  id: string;
  /** `null` — общий профиль. */
  department_id: string | null;
  name: string;
  is_default: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
}

// ── публичный compat /rest/api/* ─────────────────────────────────

/** Подсеть, из которой `/rest/api/*` доступен без авторизации. */
export interface CompatNetwork {
  id: string;
  /** Канонический CIDR (`10.177.103.0/24`, адрес — `/32`). */
  cidr: string;
  description: string | null;
  enabled: boolean;
  created_by?: string | null;
  created_at?: Iso8601 | null;
  updated_at?: Iso8601 | null;
}

export interface CompatNetworkCreateRequest {
  cidr: string;
  description?: string | null;
  enabled?: boolean;
}

export type CompatNetworkUpdateRequest = Partial<CompatNetworkCreateRequest>;

/** `GET/PUT /legacy-compat/settings`. */
export interface LegacyCompatSettings {
  /** Отдел для URL интеграций, если IP источника не принадлежит стенду. `null` — не выбран. */
  default_department_id: string | null;
  updated_by?: string | null;
  updated_at?: Iso8601 | null;
}

/** Как выбран отдел: стенд с этим IP / отдел по умолчанию / стенды в разных отделах / не определён. */
export type CompatResolveReason = "stand" | "default" | "ambiguous" | "no_default";

/** `GET /legacy-compat/resolve?ip=`. */
export interface CompatResolveResult {
  ip: string;
  allowed: boolean;
  network_id: string | null;
  cidr: string | null;
  department_id: string | null;
  reason: CompatResolveReason;
  stand_ids: string[];
}

// ── launch-preview ────────────────────────────────────────────────

/** Тело `POST /test-definitions/{id}/launch-preview`. */
export interface LaunchPreviewRequest {
  stand_id: string;
  os_version_id: string;
  kernel: string;
  /** Пусто — режим самого теста. */
  mode?: string | null;
  debug?: boolean;
  /** Одиночный запуск «только подготовка» (маркер testenv включён, файл команды). */
  testenv?: boolean;
  /** Шаг многоступенчатого теста (с 0). */
  step_index?: number;
}

/** Строка таблицы переменных превью: код → значение → источник. */
export interface LaunchPreviewVariable {
  code: string;
  label: string | null;
  /** `source` переменной; `claim` — значение задания; `override` — `override_value` слота. */
  source: string;
  /** Sensitive — `***`. */
  value: string;
  sensitive: boolean;
  slot_position: number | null;
}

/** Файл, который воркер запишет на стенд. */
export interface LaunchPreviewFile {
  /** Ключ пути в профиле запуска: script, token, dates, testenv_marker, command_file. */
  role: string;
  path: string;
  mode: string;
  sensitive: boolean;
  /** С маской `***`; `null` — не собран (см. `errors`). */
  content: string | null;
}

/** Этап сборки задания, на котором упал бы claim. */
export interface LaunchPreviewError {
  stage: string;
  error_code: string;
  message: string;
  details: Record<string, unknown>;
}

/** Ответ превью запуска. */
export interface LaunchPreview {
  test_id: string;
  stand_id: string;
  launch_context: Record<string, string>;
  debug: boolean;
  testenv: boolean;
  launch_profile: { profile_id: string; name: string | null; version_id: string; version: number } | null;
  /** Шаг, для которого собрано задание. */
  step?: { index: number; count: number; name: string; run_mode: string } | null;
  variables: LaunchPreviewVariable[];
  dates_content_masked: string | null;
  files: LaunchPreviewFile[];
  launch_command_masked: string | null;
  stop_command: string | null;
  use_pty: boolean | null;
  cleanup_globs: string[];
  /** Шаг настройки стенда: отрезолвленный скрипт с маской секретов. */
  stand_setup?: (Omit<StandSetup, "reboot_after"> & { reboot_after: boolean; script_is_sensitive: boolean }) | null;
  /** Профиль подготовки, который уйдёт в prepare-for-test. */
  provisioning?: ProvisioningProfileFields | null;
  errors: LaunchPreviewError[];
}

// ── scenarios ──────────────────────────────────────────────

export type ScenarioActionKind = "run_test" | "prepare_stand" | "wait";
export type ScenarioPreparation = "full" | "revert_only" | "none";
export type ScenarioReadiness = "ready" | "review" | "broken" | "development";

/** Стенд сценария (стенд пула: сервер или ВМ) и как его готовить. */
export interface ScenarioStandIn {
  stand_id: string;
  label: string | null;
  preparation: ScenarioPreparation;
  provisioning_profile_id: string | null;
  stand_setup: StandSetup | null;
  kernel_override: string | null;
  mode_override: "orel" | "smolensk" | null;
}

export interface ScenarioStand extends ScenarioStandIn {
  id: string;
  target_type: "server" | "vm" | string;
  stand_name: string | null;
}

/** Действие сценария; ссылается на стенд сценария по `stand_id` стенда пула. */
export interface ScenarioActionIn {
  kind: ScenarioActionKind;
  stand_id: string | null;
  test_id: string | null;
  is_verdict: boolean;
  params: Record<string, unknown>;
}

export interface ScenarioAction extends ScenarioActionIn {
  id: string;
  position: number;
  test_code: string | null;
}

/** Тело POST/PUT `/scenarios` — сценарий целиком. */
export interface ScenarioWrite {
  code: string;
  name: string;
  department_id: string;
  readiness: ScenarioReadiness;
  stands: ScenarioStandIn[];
  actions: ScenarioActionIn[];
}

export interface Scenario {
  id: string;
  code: string;
  name: string;
  department_id: string;
  readiness: ScenarioReadiness;
  created_by: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  stands: ScenarioStand[];
  actions: ScenarioAction[];
}

export interface ScenarioSummary {
  id: string;
  code: string;
  name: string;
  department_id: string;
  readiness: ScenarioReadiness;
  stands_count: number;
  actions_count: number;
  updated_at: Iso8601;
}

export interface ScenarioActionPreview {
  position: number;
  kind: ScenarioActionKind;
  stand_id: string | null;
  test_id: string | null;
  is_verdict: boolean;
  params: Record<string, unknown>;
  /** run_test — превью запуска теста на стенде действия. */
  launch: LaunchPreview | null;
  /** prepare_stand — подготовка стенда. */
  stand: {
    preparation: ScenarioPreparation;
    kernel: string;
    mode: string;
    provisioning_profile_id: string | null;
    stand_setup: LaunchPreview["stand_setup"];
  } | null;
  errors: LaunchPreviewError[];
}

export interface ScenarioPreview {
  scenario_id: string;
  actions: ScenarioActionPreview[];
}

// ── scenario runs ─────────────────────────────────────────────────

export type ScenarioRunState =
  | "waiting_for_stands" | "preparing" | "running" | "stopping" | "succeeded" | "failed" | "stopped";

export interface ScenarioRun {
  id: string;
  scenario_id: string;
  department_id: string;
  state: ScenarioRunState;
  launch_context: Record<string, string>;
  debug_mode: boolean;
  current_position: number | null;
  wait_until: Iso8601 | null;
  /** Занятые стенды при `waiting_for_stands`. */
  blocked_by: { stand_id: string; reason: string; message?: string }[];
  verdict: "passed" | "failed" | "unknown" | null;
  error: string | null;
  created_by: string | null;
  created_at: Iso8601;
  started_at: Iso8601 | null;
  finished_at: Iso8601 | null;
  stands: { stand_id: string; label: string | null; preparation: string; kernel: string; mode: string; state: string; error: string | null }[];
  actions: {
    position: number;
    kind: ScenarioActionKind;
    stand_id: string | null;
    test_id: string | null;
    is_verdict: boolean;
    params: Record<string, unknown>;
    queue_item_id: string | null;
    state: string | null;
    verdict: string | null;
  }[];
}
