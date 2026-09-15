/**
 * Тонкие обёртки над `testing_service` `/stp/*` — СТП: каталог тест-кейсов,
 * генерация Zephyr test-run'ов, прогоны, ячейки (§2.5, §6 плана миграции).
 *
 * Тест-кейсы — чтение открыто любому аутентифицированному актору, запись —
 * матрица `(stp_test_case, *, ...)`. `/stp/generate` — админский вызов,
 * матрица `(stp_test_run, *, create)`. Ячейки — ручной override под
 * `(stp_cell, *, update)`; событийное обновление статуса идёт мимо HTTP.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/stp.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  StpCell,
  StpCellManualUpdateRequest,
  StpGenerateRequest,
  StpGenerateResponse,
  StpMatrixPublishRequest,
  StpMatrixPublishResponse,
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
