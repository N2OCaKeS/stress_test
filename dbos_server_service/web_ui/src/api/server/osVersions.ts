/**
 * Thin wrappers для `server_service` `/os-versions/*` endpoints + ручной
 * `os-sync` на стороне сервера.
 *
 * Каталог OS-версий глобальный, без dept-привязки. Read-эндпоинты публичные
 * (без авторизации, лимитированы per-IP), CRUD — под action-матрицей
 * (`os_version, *, create|update|delete`). `osSync` живёт под `/servers/`,
 * но логически относится к каталогу — поэтому собран в одном модуле.
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/os_versions.py
 *   server_service/src/api/v1/endpoints/servers.py (POST /{id}/os-sync)
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  OffsetPaginatedResponse,
  OsVersion,
  OsVersionCreateRequest,
  OsVersionUpdateRequest,
  Server,
  ServerOsSyncRequest,
} from "@/api/server/types";

// ── catalog: list ───────────────────────────────────────────────────────────

/** Параметры пагинации списка OS-версий (offset envelope, legacy). */
export interface ListOsVersionsQuery {
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/server/v1/os-versions` — страница каталога OS-версий.
 *
 * Backend поддерживает и cursor-режим (`cursor=true`/`after=<token>`,
 * меняется envelope), но wrapper остаётся в offset-семантике как остальные
 * list-методы раздела. Read публичный — auth не обязателен.
 */
export function listOsVersions(
  query: ListOsVersionsQuery = {},
): Promise<OffsetPaginatedResponse<OsVersion>> {
  return apiGet<OffsetPaginatedResponse<OsVersion>>("/server/v1/os-versions", {
    query: { ...query },
  });
}

// ── catalog: create / update / delete ──────────────────────────────────────

/**
 * `POST /api/server/v1/os-versions` — регистрация новой версии.
 *
 * UNIQUE по `name`; повтор → 409 `OS_VERSION_NAME_CONFLICT`. Доступ —
 * action `(os_version, *, create)`.
 */
export function createOsVersion(body: OsVersionCreateRequest): Promise<OsVersion> {
  return apiPost<OsVersion>("/server/v1/os-versions", body);
}

/**
 * `PATCH /api/server/v1/os-versions/{id}` — частичное обновление.
 *
 * Конфликт UNIQUE по новому `name` → 409. Доступ — `(os_version, *, update)`.
 */
export function updateOsVersion(
  id: string,
  body: OsVersionUpdateRequest,
): Promise<OsVersion> {
  return apiPatch<OsVersion>(`/server/v1/os-versions/${id}`, body);
}

/**
 * `DELETE /api/server/v1/os-versions/{id}` — hard-delete версии.
 *
 * FK `servers.os_version_id ondelete=RESTRICT`: если хоть один сервер
 * ссылается, backend вернёт 409 `OS_VERSION_IN_USE`. Доступ —
 * `(os_version, *, delete)`.
 */
export function deleteOsVersion(id: string): Promise<void> {
  return apiDelete<void>(`/server/v1/os-versions/${id}`);
}

// ── server-side os-sync ────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/servers/{server_id}/os-sync` — выставить
 * `servers.os_version_id` вручную.
 *
 * Идёт без диспетча worker-задачи: backend сразу UPDATE'ит строку и
 * проставляет `os_last_synced_at = now()`. Используется, когда железо
 * переустановили мимо inventory-flow и нужно поправить версию руками.
 * Передача `os_version_id: null` сбрасывает версию. Невалидный FK → 422
 * `INVALID_OS_VERSION`. Доступ — `(server, *, os_sync)`, dept-isolation.
 */
export function osSync(
  serverId: string,
  body: ServerOsSyncRequest,
): Promise<Server> {
  return apiPost<Server>(`/server/v1/servers/${serverId}/os-sync`, body);
}
