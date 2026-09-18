/**
 * Типы request/response для `testing_service` API (`/api/testing/v1/*`).
 *
 * Изначально файл нёс только минимум для кнопки «Живой лог теста» в консоли
 * сервера (§8.6 плана миграции) — с волны 11 расширен под полный клиент
 * backend'а. Источник истины — Pydantic-схемы в `testing_service/src/schemas/`
 * и роутеры в `testing_service/src/api/v1/endpoints/`, сверено построчно, не
 * выдумано. UI-страницы (`src/pages/testing/*`) строятся поверх этих типов в
 * следующей волне.
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
 */
export type QueueItemState =
  | "queued"
  | "preparing"
  | "ready"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "paused";

/** Элемент очереди в терминальном состоянии (succeeded/failed/skipped) — поллить больше нечего. */
export function isTerminalQueueItemState(state: string): boolean {
  return state === "succeeded" || state === "failed" || state === "skipped";
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
  | "secret_service";

/** Тип значения переменной (`GlobalVariableValueType`). */
export type GlobalVariableValueType = "string" | "integer" | "boolean";

/** Карточка глобальной переменной конструктора команд. */
export interface GlobalVariable {
  id: string;
  code: string;
  label: string;
  source: GlobalVariableSource | string;
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
}

// ── test-command-args ─────────────────────────────────────────────────────

/** Тип слота конструктора команд (`CommandArgKind`). */
export type CommandArgKind = "literal" | "variable";

/** Один слот команды теста. */
export interface TestCommandArg {
  id: string;
  test_id: string;
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

// ── test-stands (полная карточка) ─────────────────────────────────────────

/**
 * Полная карточка стенда — в отличие от `TestStandSummary` несёт все поля
 * ответа `test_stand.py`, включая живое обогащение сервером (только в
 * `GET /test-stands/{id}`).
 */
export interface TestStand {
  id: string;
  server_id: string;
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

/** Тело `POST /test-stands`. */
export interface TestStandCreateRequest {
  server_id: string;
  queue_enabled?: boolean;
  is_active?: boolean;
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
  test_username: string;
  activity_report_auto_generate: boolean;
  created_at: Iso8601 | null;
  updated_at: Iso8601 | null;
}

/** Тело `PUT /department-test-settings/{department_id}` — upsert, все поля опциональны. */
export interface DepartmentTestSettingsUpdateRequest {
  retry_enabled?: boolean;
  test_username?: string;
  activity_report_auto_generate?: boolean;
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
   * Запустить даже на занятых стендах кампании. Занятый стенд без `force`
   * попадает в `enqueue_errors` с `error_code: "STAND_BUSY"` вместо запуска;
   * без роли department_admin/`admin` testing_service своего отдела сервер
   * отвечает `FORCE_LAUNCH_DENIED`, а не запускает как обычно.
   */
  force?: boolean;
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
  /** Ключ семейства тестов; `null` — пересчитывалось всё сразу. */
  category: string | null;
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
}

/** Одно семейство тестов из `GET /statistics/categories`. */
export interface StatisticsCategory {
  key: string;
  label: string;
}

/** `GET /statistics/categories` — восемь семейств в порядке легаси-меню. */
export interface StatisticsCategoriesResponse {
  items: StatisticsCategory[];
}

// ── pool overview (§F плана 2026-09-11) ─────────────────────────────────────

/** Контекст обзора пула — переключатель «Все задания / прогон / одиночные». */
export type PoolOverviewContext = "all" | "run" | "standalone";

/** Статус стенда в обзоре пула, приоритет — §F плана 2026-09-11. */
export type PoolStandStatus = "recovering" | "unreachable" | "testing" | "ready" | "no_data";

/** Заголовок кампании в обзоре пула — только при `context=run`. */
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
  server_id: string;
  status: PoolStandStatus;
  busy_state: string | null;
  busy_service_name: string | null;
  ping_reachable: boolean | null;
  ping_checked_at: Iso8601 | null;
}

/** `GET /pool-overview` — очередь/исходы + статусы стендов одним запросом. */
export interface PoolOverviewResponse {
  context: PoolOverviewContext;
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
