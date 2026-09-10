/**
 * Тонкие обёртки над `testing_service` `/test-runs/*` — прогоны/кампании
 * (§2.4, §6.1 плана миграции). Создание — под матрицей `(test_run, *,
 * create)`, чтение (список/карточка/summary-comment) доступно любому
 * аутентифицированному актору.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/test_runs.py`.
 */

import { apiGet, apiPost } from "@/api/client";
import type {
  RunSummaryComment,
  TestingPaginatedResponse,
  TestRun,
  TestRunCreateRequest,
  TestRunCreateResponse,
  TestRunDetail,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/**
 * `POST /test-runs` — поставить один РЦ+ядро+режим сразу на весь явно
 * выбранный пул стендов (по закреплённому тесту на каждый). Частичные
 * провалы не рушат остальную кампанию — см.
 * `stands_without_tests`/`enqueue_errors` в ответе. Доступ: `(test_run, *,
 * create)`.
 */
export function createTestRun(
  body: TestRunCreateRequest,
): Promise<TestRunCreateResponse> {
  return apiPost<TestRunCreateResponse>(`${BASE}/test-runs`, body);
}

/** Параметры списка кампаний — пагинация + фильтры. */
export interface ListTestRunsQuery {
  limit?: number;
  offset?: number;
  department_id?: string;
  status?: string;
  final?: boolean;
}

/** `GET /test-runs` — страница списка кампаний. Любой аутентифицированный актор. */
export function listTestRuns(
  query: ListTestRunsQuery = {},
): Promise<TestingPaginatedResponse<TestRun>> {
  return apiGet<TestingPaginatedResponse<TestRun>>(`${BASE}/test-runs`, {
    query: { ...query },
  });
}

/** `GET /test-runs/{id}` — карточка кампании + все дочерние queue_items. */
export function getTestRun(runId: string): Promise<TestRunDetail> {
  return apiGet<TestRunDetail>(`${BASE}/test-runs/${runId}`);
}

/**
 * `GET /test-runs/{id}/summary-comment` — статус end-of-run комментария в
 * Confluence-блоге (§2.7, §9.2). Пустые поля, кроме `test_run_id` — кампания
 * ещё не завершилась терминально, это не ошибка.
 */
export function getRunSummaryComment(
  runId: string,
): Promise<RunSummaryComment> {
  return apiGet<RunSummaryComment>(`${BASE}/test-runs/${runId}/summary-comment`);
}
