/**
 * Thin wrappers для `server_service` `/resource-permissions/*` endpoints.
 *
 * Инстанс-уровневый ACL: точечный грант роли на КОНКРЕТНЫЙ ресурс
 * (`server` / `server_account`) поверх тип-wide матрицы `entity_permissions`.
 * Субъект гранта — роль, действие — инстанс-грантуемое (глобальные `create` и
 * callback'и воркера сюда не входят, backend отбивает их 422).
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/resource_permissions.py
 *   server_service/src/schemas/resource_permission.py
 */

import { apiDelete, apiGet, apiPost, apiPut } from "@/api/client";
import type {
  ActionName,
  ResourceAclType,
  ResourcePermissionEntry,
  ResourcePermissionListResponse,
  ResourcePropagateRequest,
  ResourcePropagateResponse,
  RoleName,
} from "@/api/server/types";

const BASE = "/server/v1/resource-permissions";

// ── list / read ─────────────────────────────────────────────────────────────

/**
 * `GET /resource-permissions/by-resource/{resource_type}/{resource_id}` —
 * все инстанс-гранты одного ресурса (envelope `{items, total}`). Неизвестный
 * resource_type → 422 `UNKNOWN_RESOURCE_TYPE`.
 */
export function listResourcePermissions(
  resourceType: ResourceAclType,
  resourceId: string,
): Promise<ResourcePermissionListResponse> {
  return apiGet<ResourcePermissionListResponse>(
    `${BASE}/by-resource/${resourceType}/${resourceId}`,
  );
}

/**
 * `GET /resource-permissions/by-role/{role}` — срез инстанс-грантов одной
 * роли по всем ресурсам. Опц. `resourceType` сужает выдачу до одного типа.
 */
export function listResourcePermissionsByRole(
  role: RoleName,
  resourceType?: ResourceAclType,
): Promise<ResourcePermissionListResponse> {
  return apiGet<ResourcePermissionListResponse>(`${BASE}/by-role/${role}`, {
    query: { resource_type: resourceType },
  });
}

// ── mutate ──────────────────────────────────────────────────────────────────

/**
 * `PUT /resource-permissions/{resource_type}/{resource_id}/{role}/{action}` —
 * выдать инстанс-грант. Идемпотентно (повтор → существующая строка).
 * Не инстанс-грантуемое действие → 422 `ACTION_NOT_INSTANCE_GRANTABLE`,
 * чужой/несуществующий ресурс → 404.
 */
export function grantResourcePermission(
  resourceType: ResourceAclType,
  resourceId: string,
  role: RoleName,
  action: ActionName,
): Promise<ResourcePermissionEntry> {
  return apiPut<ResourcePermissionEntry>(
    `${BASE}/${resourceType}/${resourceId}/${role}/${action}`,
  );
}

/**
 * `DELETE /resource-permissions/{resource_type}/{resource_id}/{role}/{action}`
 * — снять инстанс-грант. Отсутствие строки → 404.
 */
export function revokeResourcePermission(
  resourceType: ResourceAclType,
  resourceId: string,
  role: RoleName,
  action: ActionName,
): Promise<void> {
  return apiDelete<void>(
    `${BASE}/${resourceType}/${resourceId}/${role}/${action}`,
  );
}

/**
 * `POST /resource-permissions/{resource_type}/{source_resource_id}/propagate`
 * — скопировать инстанс-гранты образца на однотипные цели. `mode=merge`
 * добавляет недостающее; `mode=mirror` приводит цели к точной копии образца
 * (требует и grant, и revoke). Ответ — сводка added/removed на каждую цель.
 */
export function propagateResourcePermissions(
  resourceType: ResourceAclType,
  sourceResourceId: string,
  body: ResourcePropagateRequest,
): Promise<ResourcePropagateResponse> {
  return apiPost<ResourcePropagateResponse>(
    `${BASE}/${resourceType}/${sourceResourceId}/propagate`,
    body,
  );
}
