/**
 * Тонкие обёртки над `testing_service` `/stp/*` — СТП: каталог тест-кейсов,
 * генерация Zephyr test-run'ов, прогоны, ячейки (§2.5, §6 плана миграции).
 *
 * Тест-кейсы — чтение открыто любому аутентифицированному актору, запись —
 * матрица `(stp_test_case, *, ...)`. `/stp/generate` — админский вызов,
 * матрица `(stp_test_run, *, create)`, принимает явный `scope` (changelog/
 * full) — не выводится из вида RC. `/stp/composition` — текущий scope+
 * revision пары (отдел, РЦ), чтение открыто. Ячейки — ручной override под
 * `(stp_cell, *, update)`; событийное обновление статуса идёт мимо HTTP.
 * `/stp/pull-from-life/*` — обратное направление (§D8): прочитать уже
 * существующие в Zephyr test-run'ы и импортировать/сверить их с EMM, не
 * публикуя ничего обратно в life.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/stp.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  StpAddTestOperation,
  StpAddTestRequest,
  StpCell,
  StpCellManualUpdateRequest,
  StpComposition,
  StpGenerateRequest,
  StpGenerateResponse,
  StpMatrixPublishRequest,
  StpMatrixPublishResponse,
  StpPullImportRequest,
  StpPullImportResponse,
  StpPullPreviewRequest,
  StpPullPreviewResponse,
  StpTestCase,
  StpTestCaseCreateRequest,
  StpTestCaseUpdateRequest,
  StpTestRun,
  TestingOkResponse,
  TestingPaginatedResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

// ── тест-кейсы ─────────────────────────────────────────────────────────────

/** Параметры списка тест-кейсов СТП. */
export interface ListStpTestCasesQuery {
  limit?: number;
  offset?: number;
  department_id?: string;
}

/** `GET /stp/test-cases` — каталог тест-кейсов (зеркало Zephyr Scale test-case). */
export function listStpTestCases(
  query: ListStpTestCasesQuery = {},
): Promise<TestingPaginatedResponse<StpTestCase>> {
  return apiGet<TestingPaginatedResponse<StpTestCase>>(
    `${BASE}/stp/test-cases`,
    { query: { ...query } },
  );
}

/** `POST /stp/test-cases` — завести тест-кейс. UNIQUE(code) — повтор → 409. */
export function createStpTestCase(
  body: StpTestCaseCreateRequest,
): Promise<StpTestCase> {
  return apiPost<StpTestCase>(`${BASE}/stp/test-cases`, body);
}

/** `GET /stp/test-cases/{id}` — карточка тест-кейса. */
export function getStpTestCase(caseId: string): Promise<StpTestCase> {
  return apiGet<StpTestCase>(`${BASE}/stp/test-cases/${caseId}`);
}

/** `PATCH /stp/test-cases/{id}` — частичное обновление. */
export function updateStpTestCase(
  caseId: string,
  body: StpTestCaseUpdateRequest,
): Promise<StpTestCase> {
  return apiPatch<StpTestCase>(`${BASE}/stp/test-cases/${caseId}`, body);
}

/** `DELETE /stp/test-cases/{id}` — удалить тест-кейс. */
export function deleteStpTestCase(caseId: string): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(`${BASE}/stp/test-cases/${caseId}`);
}

// ── генерация + прогоны ──────────────────────────────────────────────────

/**
 * `POST /stp/generate` — сгенерировать СТП-прогоны в Zephyr для
 * отдела/РЦ/режима/ядра (changelog-фильтр, группировка по pinned-стенду).
 * Провал одного стенда не рушит остальные — см. `errors` в ответе. Доступ:
 * `(stp_test_run, *, create)`.
 */
export function generateStp(body: StpGenerateRequest): Promise<StpGenerateResponse> {
  return apiPost<StpGenerateResponse>(`${BASE}/stp/generate`, body);
}

/**
 * `GET /stp/composition` — текущий активный `scope`+`revision` состава СТП
 * пары (отдел, РЦ). Отсутствие строки — не 404, а дефолт `scope: null,
 * revision: 0` (состав ещё ни разу не генерировался). Чтение открыто.
 */
export function getStpComposition(params: {
  os_version_id: string;
  department_id?: string;
}): Promise<StpComposition> {
  return apiGet<StpComposition>(`${BASE}/stp/composition`, { query: { ...params } });
}

/**
 * `POST /stp/matrix/publish` — опубликовать сводную СТП-таблицу одного РЦ
 * в Confluence (department-scoped иерархия, настраивается в интеграциях
 * отдела). Не настроено/нет прогонов — понятный skip-статус в ответе, не
 * ошибка. Доступ: `(stp_test_run, *, publish)`.
 */
export function publishStpMatrix(body: StpMatrixPublishRequest): Promise<StpMatrixPublishResponse> {
  return apiPost<StpMatrixPublishResponse>(`${BASE}/stp/matrix/publish`, body);
}

/** Параметры списка СТП-прогонов. */
export interface ListStpTestRunsQuery {
  limit?: number;
  offset?: number;
  stand_id?: string;
  os_version_id?: string;
}

/** `GET /stp/test-runs` — список СТП-прогонов (Zephyr test-run'ов). */
export function listStpTestRuns(
  query: ListStpTestRunsQuery = {},
): Promise<TestingPaginatedResponse<StpTestRun>> {
  return apiGet<TestingPaginatedResponse<StpTestRun>>(
    `${BASE}/stp/test-runs`,
    { query: { ...query } },
  );
}

/** `GET /stp/test-runs/{id}` — карточка СТП-прогона. */
export function getStpTestRun(runId: string): Promise<StpTestRun> {
  return apiGet<StpTestRun>(`${BASE}/stp/test-runs/${runId}`);
}

/** `GET /stp/test-runs/{id}/cells` — ячейки СТП-прогона. */
export function listStpTestRunCells(runId: string): Promise<StpCell[]> {
  return apiGet<StpCell[]>(`${BASE}/stp/test-runs/${runId}/cells`);
}

/**
 * `POST /stp/test-runs/{run_id}/add-test` — добавить один тест EMM в
 * конкретный СТП-прогон (§D6/D7): заводит недостающий Zephyr testcase
 * (переиспользует существующую связь, если она уже есть), добавляет его в
 * Zephyr test-run, локальную ячейку и переопубликовывает СТП-матрицу.
 * Долговечно — повтор с тем же `test_id` на тот же прогон продолжает с
 * первого не пройденного шага вместо дублирования работы.
 */
export function addTestToStp(
  runId: string,
  body: StpAddTestRequest,
): Promise<StpAddTestOperation> {
  return apiPost<StpAddTestOperation>(`${BASE}/stp/test-runs/${runId}/add-test`, body);
}

// ── pull СТП из life (§D8) ────────────────────────────────────────────────

/**
 * `POST /stp/pull-from-life/preview` — предпросмотр импорта уже существующих
 * Zephyr test-run'ов отдела (свои/легаси/заведённые руками). Ни одной записи
 * в БД. Доступ: `(stp_test_run, *, create)` — тоже дёргает Jira живым
 * запросом с кредами отдела.
 */
export function previewPullFromLife(body: StpPullPreviewRequest): Promise<StpPullPreviewResponse> {
  return apiPost<StpPullPreviewResponse>(`${BASE}/stp/pull-from-life/preview`, body);
}

/**
 * `POST /stp/pull-from-life/import` — импортировать/сверить выбранные (или
 * все найденные, если `zephyr_keys` не задан) test-run'ы. Идемпотентно —
 * повтор на тот же ключ не дублирует локальные строки. Локальная ячейка с
 * расходящимся статусом не перезаписывается — попадает в `conflicts`.
 */
export function importPullFromLife(body: StpPullImportRequest): Promise<StpPullImportResponse> {
  return apiPost<StpPullImportResponse>(`${BASE}/stp/pull-from-life/import`, body);
}

// ── ячейки: ручной override ─────────────────────────────────────────────

/**
 * `PATCH /stp/cells/{id}` — ручной override статуса ячейки. Не трогает
 * Zephyr — легаси-паттерн: ручная правка локальна. Доступ: `(stp_cell, *,
 * update)`.
 */
export function overrideStpCell(
  cellId: string,
  body: StpCellManualUpdateRequest,
): Promise<StpCell> {
  return apiPatch<StpCell>(`${BASE}/stp/cells/${cellId}`, body);
}
