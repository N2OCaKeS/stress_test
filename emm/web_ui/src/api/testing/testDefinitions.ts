/**
 * Тонкие обёртки над `testing_service` `/test-definitions/*` — каталог тестов
 * (§2.2 плана миграции). Чтение доступно любому аутентифицированному актору,
 * запись — под матрицей `(test_definition, *, create|update|delete)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/test_definitions.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  LaunchPreview,
  LaunchPreviewRequest,
  TestDefinition,
  TestDefinitionCreateRequest,
  TestDefinitionUpdateRequest,
  TestingOkResponse,
  TestingPaginatedResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** Параметры списка тестов каталога — пагинация + фильтры. */
export interface ListTestDefinitionsQuery {
  limit?: number;
  offset?: number;
  department_id?: string;
  category?: string;
  readiness?: string;
}

/** `GET /test-definitions` — страница каталога тестов. Любой аутентифицированный актор. */
export function listTestDefinitions(
  query: ListTestDefinitionsQuery = {},
): Promise<TestingPaginatedResponse<TestDefinition>> {
  return apiGet<TestingPaginatedResponse<TestDefinition>>(
    `${BASE}/test-definitions`,
    { query: { ...query } },
  );
}

/** `GET /test-definitions/by-code/{code}` — карточка по UNIQUE-коду. */
export function getTestDefinitionByCode(code: string): Promise<TestDefinition> {
  return apiGet<TestDefinition>(
    `${BASE}/test-definitions/by-code/${encodeURIComponent(code)}`,
  );
}

/** `GET /test-definitions/{id}` — карточка по id. */
export function getTestDefinition(testId: string): Promise<TestDefinition> {
  return apiGet<TestDefinition>(`${BASE}/test-definitions/${testId}`);
}

/** `POST /test-definitions` — завести тест. Доступ: `(test_definition, *, create)`. */
export function createTestDefinition(
  body: TestDefinitionCreateRequest,
): Promise<TestDefinition> {
  return apiPost<TestDefinition>(`${BASE}/test-definitions`, body);
}

/** `PATCH /test-definitions/{id}` — частичное обновление. Доступ: `(test_definition, *, update)`. */
export function updateTestDefinition(
  testId: string,
  body: TestDefinitionUpdateRequest,
): Promise<TestDefinition> {
  return apiPatch<TestDefinition>(`${BASE}/test-definitions/${testId}`, body);
}

/**
 * `DELETE /test-definitions/{id}` — hard-delete, каскадом сносит слоты
 * команды теста. Доступ: `(test_definition, *, delete)`.
 */
export function deleteTestDefinition(testId: string): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(`${BASE}/test-definitions/${testId}`);
}

/**
 * `POST /test-definitions/{id}/launch-preview` — задание воркеру, которое
 * собрал бы claim для выбранных стенда, РЦ и ядра: переменные,
 * dates.conf, файлы для стенда, команды запуска и остановки. Секреты — `***`.
 * Ничего не ставит в очередь. Доступ — как на чтение теста.
 */
export function previewTestLaunch(testId: string, body: LaunchPreviewRequest): Promise<LaunchPreview> {
  return apiPost<LaunchPreview>(`${BASE}/test-definitions/${testId}/launch-preview`, body);
}
