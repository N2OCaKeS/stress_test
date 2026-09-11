/**
 * Тонкие обёртки над `testing_service` `/permissions/*` endpoints.
 *
 * Матрица entity-permissions: список grants, каталог сущностей/действий,
 * grant/revoke (PUT/DELETE по ключу `entity_type/role/action`). testing_service
 * не имеет инстанс-уровневого ACL — вся матрица тип-wide, в отличие от
 * `@/api/server/permissions`.
 *
 * Department scope: caller (department_admin своего отдела или сервисная
 * роль `admin`) пишет только в свой dept. PUT принимает body с
 * `target_department_id` (должен совпадать с `department_id` caller'а, либо
 * опущен), DELETE — query-параметром. Платформенный `account_admin` —
 * исключение: может нацелить запись в любой отдел.
 *
 * Source of truth:
 *   testing_service/src/api/v1/endpoints/permissions.py
 *   testing_service/src/schemas/permission.py
 */

import { apiDelete, apiGet, apiPut } from "@/api/client";
import type {
  TestingActionName,
  TestingEntityType,
  TestingPermissionCatalogItem,
  TestingPermissionEntry,
  TestingPermissionGrantRequest,
  TestingPermissionListResponse,
  TestingRoleName,
} from "@/api/testing/types";

const BASE = "/testing/v1";

// ── list / read ─────────────────────────────────────────────────────────────

/** Query-параметры `GET /permissions`. */
export interface ListTestingPermissionsQuery {
  /** Сузить выдачу до грантов одной роли. */
  role?: TestingRoleName;
  /** Обогатить строки описаниями из каталога (`described=true` в ответе). */
  describe?: boolean;
}

/**
 * `GET /permissions` — матрица grants envelope'ом `{items, total, described}`.
 *
 * Фильтр по сущности backend принимает только path-формой
 * `/permissions/{entity_type}` — см. `listPermissionsForEntity`.
 */
export function listPermissions(
  query: ListTestingPermissionsQuery = {},
): Promise<TestingPermissionListResponse> {
  return apiGet<TestingPermissionListResponse>(`${BASE}/permissions`, {
    query: { ...query },
  });
}

/** `GET /permissions/{entity_type}` — grants одной сущности. `described` всегда `false`. */
export function listPermissionsForEntity(
  entityType: TestingEntityType,
): Promise<TestingPermissionListResponse> {
  return apiGet<TestingPermissionListResponse>(
    `${BASE}/permissions/${entityType}`,
  );
}

/**
 * `GET /permissions/catalog` — справочник сущностей и действий с описаниями,
 * флагами `sensitive` и `worker_only`. Read-only.
 */
export function getPermissionCatalog(): Promise<TestingPermissionCatalogItem[]> {
  return apiGet<TestingPermissionCatalogItem[]>(`${BASE}/permissions/catalog`);
}

// ── mutate ──────────────────────────────────────────────────────────────────

/**
 * `PUT /permissions/{entity_type}/{role}/{action}` — выдать `action` на
 * `entity_type` указанной `role`. Идемпотентно (повторный grant с тем же
 * scope → noop, возвращает существующую запись).
 *
 * Body опционален: опустить (grant в свой отдел) или передать
 * `{target_department_id}` равный собственному `department_id` caller'а.
 * Несовпадение → 403 `DEPARTMENT_ISOLATION`. Невалидная пара
 * (entity_type, action) → 422 `INVALID_ACTION_FOR_ENTITY`. Системная роль
 * (`admin`/`guest`) → 409 `SYSTEM_ROLE_IMMUTABLE`. Race на UNIQUE → 409
 * `PERMISSION_ALREADY_EXISTS`. Аудит: `permission.grant` (CRITICAL).
 */
export function setPermission(
  entityType: TestingEntityType,
  role: TestingRoleName,
  action: TestingActionName,
  body?: TestingPermissionGrantRequest,
): Promise<TestingPermissionEntry> {
  return apiPut<TestingPermissionEntry>(
    `${BASE}/permissions/${entityType}/${role}/${action}`,
    body,
  );
}

/** Query-параметры `DELETE /permissions/{...}`. */
export interface DeleteTestingPermissionQuery {
  /**
   * Scope строки на удаление. `null`/`undefined` → system-wide;
   * `department_id` → per-department (только свой department, иначе 403
   * `DEPARTMENT_ISOLATION`; `account_admin` — любой).
   */
  target_department_id?: string | null;
}

/**
 * `DELETE /permissions/{entity_type}/{role}/{action}` — снять grant. Scope
 * симметричен `setPermission`'у, но передаётся через query-параметр
 * `target_department_id`. Отсутствие строки → 404 `PERMISSION_NOT_FOUND`.
 * Системная роль → 409 `SYSTEM_ROLE_IMMUTABLE`. Аудит: `permission.revoke`
 * (CRITICAL).
 */
export function deletePermission(
  entityType: TestingEntityType,
  role: TestingRoleName,
  action: TestingActionName,
  query: DeleteTestingPermissionQuery = {},
): Promise<void> {
  return apiDelete<void>(
    `${BASE}/permissions/${entityType}/${role}/${action}`,
    undefined,
    { query: { ...query } },
  );
}
