/**
 * Thin wrappers для `server_service` `/permissions/*` endpoints.
 *
 * Матрица entity-permissions: список grants, каталог сущностей/действий,
 * grant/revoke (PUT/DELETE по ключу `entity_type/role/action`). Один метод
 * на backend-route, типы — из `./types.ts`.
 *
 * Department scope: caller (department_admin / service admin своего отдела)
 * пишет только в свой dept. PUT принимает body с `target_department_id`
 * (должен совпадать с `department_id` caller'а, либо опущен), DELETE —
 * query-параметром. Platform-уровневые роли (`account_admin`/`loging_admin`)
 * не доходят до endpoint'а — их режет middleware 403
 * `PLATFORM_ADMIN_BUSINESS_DATA_DENIED`.
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/permissions.py
 *   server_service/src/schemas/permission.py
 */

import { apiDelete, apiGet, apiPut } from "@/api/client";
import type {
  ActionName,
  EntityType,
  PermissionCatalogItem,
  PermissionEntry,
  PermissionGrantRequest,
  PermissionListResponse,
  RoleName,
} from "@/api/server/types";

// ── list / read ─────────────────────────────────────────────────────────────

/** Query-параметры `GET /api/server/v1/permissions`. */
export interface ListPermissionsQuery {
  /** Сузить выдачу до грантов одного entity_type. */
  entity_type?: EntityType;
  /** Сузить выдачу до грантов одной роли. */
  role?: RoleName;
  /** Обогатить строки описаниями из каталога (`described=true` в ответе). */
  describe?: boolean;
}

/**
 * `GET /api/server/v1/permissions` — матрица grants envelope'ом
 * `{items, total, described}`.
 *
 * `entity_type` бэкендом как query-параметр напрямую не парсится — для
 * фильтрации по сущности используй `getPermissionsForEntity` (path-форма).
 * `role` и `describe` уходят как query-params как есть.
 */
export function listPermissions(
  query: ListPermissionsQuery = {},
): Promise<PermissionListResponse> {
  return apiGet<PermissionListResponse>("/server/v1/permissions", {
    query: { ...query },
  });
}

/**
 * `GET /api/server/v1/permissions/catalog` — справочник сущностей и
 * действий с описаниями, флагами `sensitive` и `worker_only`. Read-only.
 */
export function getPermissionCatalog(): Promise<PermissionCatalogItem[]> {
  return apiGet<PermissionCatalogItem[]>("/server/v1/permissions/catalog");
}

/**
 * `GET /api/server/v1/permissions/{entity_type}` — grants для одной
 * сущности. Envelope тот же `{items, total, described=false}` (describe-
 * обогащения у этого endpoint'а нет). Неизвестный `entity_type` → 422
 * `UNKNOWN_ENTITY_TYPE`.
 */
export function getPermissionsForEntity(
  entityType: EntityType,
): Promise<PermissionListResponse> {
  return apiGet<PermissionListResponse>(
    `/server/v1/permissions/${entityType}`,
  );
}

// ── mutate ──────────────────────────────────────────────────────────────────

/**
 * `PUT /api/server/v1/permissions/{entity_type}/{role}/{action}` — выдать
 * `action` на `entity_type` указанной `role`. Идемпотентно (повторный grant
 * с тем же scope → noop, возвращает существующую запись).
 *
 * Body опционален: можно либо опустить целиком (system-wide grant — в
 * текущей конфигурации недостижим, platform-роли блокирует middleware), либо
 * передать `{target_department_id}` равный собственному `department_id`
 * caller'а. Несовпадение → 403 `DEPARTMENT_ISOLATION`. Невалидная пара
 * (entity_type, action) → 422 `INVALID_ACTION_FOR_ENTITY`. Race на UNIQUE →
 * 409 `PERMISSION_ALREADY_EXISTS`. Аудит: `permission.grant` (CRITICAL).
 */
export function setPermission(
  entityType: EntityType,
  role: RoleName,
  action: ActionName,
  body?: PermissionGrantRequest,
): Promise<PermissionEntry> {
  return apiPut<PermissionEntry>(
    `/server/v1/permissions/${entityType}/${role}/${action}`,
    body,
  );
}

/** Query-параметры `DELETE /api/server/v1/permissions/{...}`. */
export interface DeletePermissionQuery {
  /**
   * Scope строки на удаление. `null`/`undefined` → system-wide;
   * `department_id` → per-department (только свой department, иначе 403
   * `DEPARTMENT_ISOLATION`).
   */
  target_department_id?: string | null;
}

/**
 * `DELETE /api/server/v1/permissions/{entity_type}/{role}/{action}` —
 * сброс grant'а к дефолту каталога. Scope симметричен `setPermission`'у,
 * но передаётся через query-параметр `target_department_id`. Отсутствие
 * строки → 404 `PERMISSION_NOT_FOUND`. Аудит: `permission.revoke`
 * (CRITICAL).
 */
export function deletePermission(
  entityType: EntityType,
  role: RoleName,
  action: ActionName,
  query: DeletePermissionQuery = {},
): Promise<void> {
  return apiDelete<void>(
    `/server/v1/permissions/${entityType}/${role}/${action}`,
    undefined,
    { query: { ...query } },
  );
}
