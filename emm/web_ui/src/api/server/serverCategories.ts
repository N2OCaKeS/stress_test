/**
 * Thin wrappers для `server_service` `/server-categories/*` endpoints.
 *
 * Каталог категорий серверов по мощности глобальный, без dept-привязки.
 * Read доступен любому аутентифицированному актору (токен обязателен,
 * доступ департамента к server_service не проверяется — это справочная
 * величина, а не бизнес-данные отдела). CRUD — под action-матрицей
 * (`server_category, *, create|update|delete`).
 *
 * Source of truth: server_service/src/api/v1/endpoints/server_categories.py
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  OffsetPaginatedResponse,
  ServerCategory,
  ServerCategoryCreateRequest,
  ServerCategoryUpdateRequest,
} from "@/api/server/types";

/** Параметры пагинации списка категорий (offset envelope). */
export interface ListServerCategoriesQuery {
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/server/v1/server-categories` — страница каталога категорий.
 *
 * Envelope `{items, total, limit, offset}`. Доступен любому
 * аутентифицированному актору.
 */
export function listServerCategories(
  query: ListServerCategoriesQuery = {},
): Promise<OffsetPaginatedResponse<ServerCategory>> {
  return apiGet<OffsetPaginatedResponse<ServerCategory>>(
    "/server/v1/server-categories",
    { query: { ...query } },
  );
}

/**
 * `GET /api/server/v1/server-categories/by-code/{code}` — карточка по
 * UNIQUE-коду. Доступен любому аутентифицированному актору.
 */
export function getServerCategoryByCode(code: string): Promise<ServerCategory> {
  return apiGet<ServerCategory>(
    `/server/v1/server-categories/by-code/${encodeURIComponent(code)}`,
  );
}

/**
 * `POST /api/server/v1/server-categories` — завести новую категорию.
 *
 * UNIQUE по `code`; повтор → 409. Доступ — `(server_category, *, create)`.
 */
export function createServerCategory(
  body: ServerCategoryCreateRequest,
): Promise<ServerCategory> {
  return apiPost<ServerCategory>("/server/v1/server-categories", body);
}

/**
 * `PATCH /api/server/v1/server-categories/{id}` — частичное обновление.
 *
 * Конфликт UNIQUE по новому `code` → 409. Доступ —
 * `(server_category, *, update)`.
 */
export function updateServerCategory(
  id: string,
  body: ServerCategoryUpdateRequest,
): Promise<ServerCategory> {
  return apiPatch<ServerCategory>(`/server/v1/server-categories/${id}`, body);
}

/**
 * `DELETE /api/server/v1/server-categories/{id}` — hard-delete категории.
 *
 * FK `servers.category_id ondelete=RESTRICT`: если хоть один сервер отнесён
 * к этой категории, backend вернёт 409 `SERVER_CATEGORY_IN_USE`. Доступ —
 * `(server_category, *, delete)`.
 */
export function deleteServerCategory(id: string): Promise<void> {
  return apiDelete<void>(`/server/v1/server-categories/${id}`);
}
