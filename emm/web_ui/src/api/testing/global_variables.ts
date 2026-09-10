/**
 * Тонкие обёртки над `testing_service` `/global-variables/*` — платформенный
 * каталог переменных конструктора команд (§2.1, §3.3 плана миграции). Чтение
 * (список/карточка/резолв choices) доступно любому аутентифицированному
 * актору, запись — под матрицей `(global_variable, *, create|update|delete)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/global_variables.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  ChoicesResponse,
  GlobalVariable,
  GlobalVariableCreateRequest,
  GlobalVariableUpdateRequest,
  TestingOkResponse,
  TestingPaginatedResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** Параметры пагинации списка переменных (offset envelope). */
export interface ListGlobalVariablesQuery {
  limit?: number;
  offset?: number;
}

/** `GET /global-variables` — страница каталога. Любой аутентифицированный актор. */
export function listGlobalVariables(
  query: ListGlobalVariablesQuery = {},
): Promise<TestingPaginatedResponse<GlobalVariable>> {
  return apiGet<TestingPaginatedResponse<GlobalVariable>>(
    `${BASE}/global-variables`,
    { query: { ...query } },
  );
}

/** `GET /global-variables/by-code/{code}` — карточка по UNIQUE-коду. */
export function getGlobalVariableByCode(code: string): Promise<GlobalVariable> {
  return apiGet<GlobalVariable>(
    `${BASE}/global-variables/by-code/${encodeURIComponent(code)}`,
  );
}

/** `GET /global-variables/{id}` — карточка по id. */
export function getGlobalVariable(variableId: string): Promise<GlobalVariable> {
  return apiGet<GlobalVariable>(`${BASE}/global-variables/${variableId}`);
}

/**
 * `GET /global-variables/{id}/choices` — резолв `choices_source` в список
 * `{value,label}` в момент запроса. `params` — произвольные query-параметры,
 * которых требует конкретный `dynamic:`-резолвер (например
 * `dynamic:kernels` требует `os_version_id`).
 */
export function getGlobalVariableChoices(
  variableId: string,
  params: Record<string, string | number | boolean | null | undefined> = {},
): Promise<ChoicesResponse> {
  return apiGet<ChoicesResponse>(
    `${BASE}/global-variables/${variableId}/choices`,
    { query: { ...params } },
  );
}

/** `POST /global-variables` — завести переменную. Доступ: `(global_variable, *, create)`. */
export function createGlobalVariable(
  body: GlobalVariableCreateRequest,
): Promise<GlobalVariable> {
  return apiPost<GlobalVariable>(`${BASE}/global-variables`, body);
}

/** `PATCH /global-variables/{id}` — частичное обновление. Доступ: `(global_variable, *, update)`. */
export function updateGlobalVariable(
  variableId: string,
  body: GlobalVariableUpdateRequest,
): Promise<GlobalVariable> {
  return apiPatch<GlobalVariable>(
    `${BASE}/global-variables/${variableId}`,
    body,
  );
}

/**
 * `DELETE /global-variables/{id}` — hard-delete. 409, если переменную
 * использует слот команды теста. Доступ: `(global_variable, *, delete)`.
 */
export function deleteGlobalVariable(
  variableId: string,
): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(`${BASE}/global-variables/${variableId}`);
}
