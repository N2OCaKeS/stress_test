/**
 * Thin wrappers для `secret_service` `/permissions/*` endpoints.
 *
 * Тип-wide матрица прав: каталог сущности/действий, список grants,
 * grant/revoke (PUT/DELETE по ключу `secret/role/action`). У secret_service
 * ровно одна сущность — `secret`, поэтому `entity_type` в путях зашит.
 *
 * Department scope: caller (department_admin / service admin своего отдела)
 * пишет только в свой dept. PUT принимает body с `target_department_id`
 * (должен совпадать с `department_id` caller'а либо опущен), DELETE — тем же
 * query-параметром. Платформенный account_admin — мета-админ матрицы: для
 * него `target_department_id` задаёт целевой отдел без dept-isolation.
 *
 * Source of truth:
 *   secret_service/src/api/v1/endpoints/permissions.py
 *   secret_service/src/schemas/permission.py
 */

import { apiDelete, apiGet, apiPut } from "@/api/client";
import type {
  OkResponse,
  SecretActionName,
  SecretPermissionCatalogItem,
  SecretPermissionEntry,
  SecretPermissionGrantRequest,
  SecretPermissionListResponse,
  SecretRoleName,
} from "@/api/secret/types";

const BASE = "/secret/v1/permissions";
const ENTITY = "secret";

// ── list / read ─────────────────────────────────────────────────────────────

/** Query-параметры `GET /api/secret/v1/permissions`. */
export interface ListSecretPermissionsQuery {
  /** Сузить выдачу до грантов одной роли. */
  role?: SecretRoleName;
  /** Обогатить строки описаниями из каталога (`described=true` в ответе). */
  describe?: boolean;
}

/**
 * `GET /api/secret/v1/permissions` — матрица grants envelope'ом
 * `{items, total, described}`. `role` и `describe` уходят query-параметрами.
 */
export function listSecretPermissions(
  query: ListSecretPermissionsQuery = {},
): Promise<SecretPermissionListResponse> {
  return apiGet<SecretPermissionListResponse>(BASE, { query: { ...query } });
}

/**
 * `GET /api/secret/v1/permissions/catalog` — справочник сущности `secret` и
 * её действий с описаниями и флагом `sensitive`. Read-only.
 */
export function getSecretPermissionCatalog(): Promise<
  SecretPermissionCatalogItem[]
> {
  return apiGet<SecretPermissionCatalogItem[]>(`${BASE}/catalog`);
}

// ── mutate ──────────────────────────────────────────────────────────────────

/**
 * `PUT /api/secret/v1/permissions/secret/{role}/{action}` — выдать `action`
 * роли `role`. Идемпотентно (повторный grant с тем же scope → noop).
 *
 * Body опционален: опустить целиком либо передать `{target_department_id}`
 * равный собственному `department_id`. Несовпадение → 403 DEPARTMENT_ISOLATION.
 * Системные роли guest/admin → 409 SYSTEM_ROLE_IMMUTABLE. Невалидное действие
 * → 422 INVALID_ACTION_FOR_ENTITY.
 */
export function setSecretPermission(
  role: SecretRoleName,
  action: SecretActionName,
  body?: SecretPermissionGrantRequest,
): Promise<SecretPermissionEntry> {
  return apiPut<SecretPermissionEntry>(
    `${BASE}/${ENTITY}/${role}/${action}`,
    body,
  );
}

/** Query-параметры `DELETE /api/secret/v1/permissions/secret/{role}/{action}`. */
export interface DeleteSecretPermissionQuery {
  /** Scope строки на удаление; свой `department_id` либо `null`/`undefined`. */
  target_department_id?: string | null;
}

/**
 * `DELETE /api/secret/v1/permissions/secret/{role}/{action}` — сброс grant'а
 * к дефолту каталога. Scope через query-параметр `target_department_id`.
 * Системные роли guest/admin → 409. Отсутствие строки → 404.
 */
export function deleteSecretPermission(
  role: SecretRoleName,
  action: SecretActionName,
  query: DeleteSecretPermissionQuery = {},
): Promise<OkResponse> {
  return apiDelete<OkResponse>(
    `${BASE}/${ENTITY}/${role}/${action}`,
    undefined,
    { query: { ...query } },
  );
}
