/**
 * Thin wrappers для `server_service` `/os-versions/*` endpoints + ручной
 * `os-sync` на стороне сервера.
 *
 * Каталог OS-версий глобальный, без dept-привязки. Read доступен ролям с
 * доступом к server-зоне (запрос идёт с Bearer как и везде); платформенные
 * business-data-denied роли (account_admin / loging_admin) получат 403
 * `PLATFORM_ADMIN_BUSINESS_DATA_DENIED`, а loging_reader без server в
 * allowed_services — 200. CRUD — под action-матрицей
 * (`os_version, *, create|update|delete`). `osSync` живёт под `/servers/`,
 * но логически относится к каталогу — поэтому собран в одном модуле.
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/os_versions.py
 *   server_service/src/api/v1/endpoints/servers.py (POST /{id}/os-sync)
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import { naturalCompare } from "@/lib/naturalSort";
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
 * list-методы раздела. Запрос идёт с Bearer; доступ — у ролей server-зоны,
 * платформенным business-data-denied ролям backend вернёт 403.
 *
 * Backend сортирует по `discovered_at DESC` (когда добавили) — для человека
 * это нечитаемо (РЦ вперемешку). Здесь пересортировываем `items` по `name`
 * натуральным сравнением (`179` < `1710rc45` < `1711rc17`, не лексикографически)
 * — единая точка, все вызывающие места (дропдауны/каталог) получают
 * отсортированный список без собственной сортировки.
 */
export async function listOsVersions(
  query: ListOsVersionsQuery = {},
): Promise<OffsetPaginatedResponse<OsVersion>> {
  const page = await apiGet<OffsetPaginatedResponse<OsVersion>>(
    "/server/v1/os-versions",
    { query: { ...query } },
  );
  return {
    ...page,
    items: [...page.items].sort((a, b) => naturalCompare(a.name, b.name)),
  };
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

/**
 * `POST /api/server/v1/os-versions/{id}/resolve-repositories` — перестроить
 * `repositories` версии из индекса релизов по build-версии.
 *
 * Порт легаси `ReleaseToRepo`. Требует то же право, что и `update`. Версия
 * каталога не найдена → 404 `OS_VERSION_NOT_FOUND`; build-версия
 * отсутствует в индексе релизов → 404 `OS_RELEASE_NOT_FOUND`; индекс
 * недоступен/битый → 503 `OS_RELEASES_INDEX_UNAVAILABLE` /
 * `OS_RELEASES_INDEX_INVALID`. Отдаёт полную обновлённую карточку версии
 * (не отдельный список репозиториев) — сам объект версии resolve не меняет,
 * пишет только `repositories`.
 */
export function resolveOsVersionRepositories(
  id: string,
  buildVersion: string,
): Promise<OsVersion> {
  return apiPost<OsVersion>(`/server/v1/os-versions/${id}/resolve-repositories`, {
    build_version: buildVersion,
  });
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