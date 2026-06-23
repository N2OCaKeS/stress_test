/**
 * Thin wrappers для `secret_service` `/secret/v1/credentials/{id}/acl`.
 *
 * Source of truth:
 *   secret_service/src/api/v1/endpoints/role_acls.py
 *   secret_service/src/schemas/role_acls.py
 */

import { apiDelete, apiGet, apiPost, apiPut } from "@/api/client";
import type {
  OkResponse,
  RoleACL,
  RoleACLCreateRequest,
  RoleACLList,
  RoleACLUpsertRequest,
  RoleACLUpsertResponse,
} from "@/api/secret/types";

const BASE = "/secret/v1/credentials";

/** `GET /credentials/{id}/acl` — список RoleACL кред. */
export function listRoleAcls(credId: string): Promise<RoleACLList> {
  return apiGet<RoleACLList>(`${BASE}/${credId}/acl`);
}

/**
 * `POST /credentials/{id}/acl` — выдать RoleACL.
 *
 * `dept_id` + `role_name` обязательны; cross_department recipient требует
 * заранее выданного DeptGrant, иначе backend отбивает.
 */
export function addRoleAcl(
  credId: string,
  body: RoleACLCreateRequest,
): Promise<RoleACL> {
  return apiPost<RoleACL>(`${BASE}/${credId}/acl`, body);
}

/**
 * `PUT /credentials/{id}/acl` — атомарный upsert RoleACL.
 *
 * Задаёт желаемую пару `(can_read, can_write)` для `(dept_id, role_name)`:
 * строки нет — создаёт, есть — переписывает флаги, оба флага false — снимает
 * строку. Идемпотентно, без 409 — заменяет связку revoke+re-add для тоггла
 * ячейки матрицы. `acl` в ответе = null, когда доступ снят.
 */
export function upsertRoleAcl(
  credId: string,
  body: RoleACLUpsertRequest,
): Promise<RoleACLUpsertResponse> {
  return apiPut<RoleACLUpsertResponse>(`${BASE}/${credId}/acl`, body);
}

/** `DELETE /credentials/{id}/acl/{aclId}` — снять RoleACL. */
export function revokeRoleAcl(
  credId: string,
  aclId: string,
): Promise<OkResponse> {
  return apiDelete<OkResponse>(`${BASE}/${credId}/acl/${aclId}`);
}
