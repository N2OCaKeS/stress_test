/**
 * Service-role catalogue endpoints (scope: `(department, service)`).
 *
 * Path shape:
 *   /departments/{department_id}/services/{service_name}/roles[/{role_name}[/assign|revoke]]
 *
 * API_ENDPOINTS.md lists 6 endpoints under "Service roles":
 *   1. GET    .../roles                       listServiceRoles
 *   2. POST   .../roles                       createServiceRole
 *   3. PATCH  .../roles/{role_name}           patchServiceRole
 *   4. DELETE .../roles/{role_name}           deleteServiceRole
 *   5. POST   .../roles/{role_name}/assign    bulkAssignServiceRole
 *   6. POST   .../roles/{role_name}/revoke    bulkRevokeServiceRole
 *
 * System roles (`is_system=True`) cannot be patched / deleted — the
 * backend will refuse with `SERVICE_ROLE_SYSTEM_LOCKED`. The UI should
 * reflect this with a disabled state, not by hiding the row.
 */

import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
} from "@/api/client";
import type {
  ServiceName,
  ServiceRole,
  ServiceRoleBulkAssignRequest,
  ServiceRoleCreateRequest,
  ServiceRolePatchRequest,
} from "@/api/auth/types";

function rolesBase(departmentId: string, serviceName: ServiceName): string {
  return `/auth/v1/departments/${departmentId}/services/${serviceName}/roles`;
}

export function listServiceRoles(
  departmentId: string,
  serviceName: ServiceName,
): Promise<ServiceRole[]> {
  return apiGet<ServiceRole[]>(rolesBase(departmentId, serviceName));
}

export function createServiceRole(
  departmentId: string,
  serviceName: ServiceName,
  req: ServiceRoleCreateRequest,
): Promise<ServiceRole> {
  return apiPost<ServiceRole>(rolesBase(departmentId, serviceName), req);
}

export function patchServiceRole(
  departmentId: string,
  serviceName: ServiceName,
  roleName: string,
  req: ServiceRolePatchRequest,
): Promise<ServiceRole> {
  return apiPatch<ServiceRole>(
    `${rolesBase(departmentId, serviceName)}/${roleName}`,
    req,
  );
}

export function deleteServiceRole(
  departmentId: string,
  serviceName: ServiceName,
  roleName: string,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `${rolesBase(departmentId, serviceName)}/${roleName}`,
  );
}

export function bulkAssignServiceRole(
  departmentId: string,
  serviceName: ServiceName,
  roleName: string,
  req: ServiceRoleBulkAssignRequest,
): Promise<{ assigned: number }> {
  return apiPost<{ assigned: number }>(
    `${rolesBase(departmentId, serviceName)}/${roleName}/assign`,
    req,
  );
}

export function bulkRevokeServiceRole(
  departmentId: string,
  serviceName: ServiceName,
  roleName: string,
  req: ServiceRoleBulkAssignRequest,
): Promise<{ revoked: number }> {
  return apiPost<{ revoked: number }>(
    `${rolesBase(departmentId, serviceName)}/${roleName}/revoke`,
    req,
  );
}

// ---------------------------------------------------------------------------
// Convenience: flat search across departments + services. Backend has no
// global endpoint — the UI iterates known `(department, service)` pairs
// and concatenates. Callers pass the pair list (`scopes`) and we resolve
// in parallel.
// ---------------------------------------------------------------------------

export interface ServiceRoleScope {
  departmentId: string;
  serviceName: ServiceName;
}

export async function listServiceRolesAcross(
  scopes: ServiceRoleScope[],
): Promise<Array<{ scope: ServiceRoleScope; roles: ServiceRole[] }>> {
  const results = await Promise.allSettled(
    scopes.map(async (scope) => {
      const roles = await listServiceRoles(
        scope.departmentId,
        scope.serviceName,
      );
      return { scope, roles };
    }),
  );
  return results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
}
