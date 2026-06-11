/**
 * Thin wrappers для `secret_service` `/secret/v1/credentials/{id}/dept-grants`.
 *
 * DeptGrant — только для cross_department кред: даёт recipient-dep'у право
 * получать RoleACL на креду. Снятие grant'а каскадит RoleACL recipient-dep'а.
 *
 * Source of truth:
 *   secret_service/src/api/v1/endpoints/dept_grants.py
 *   secret_service/src/schemas/dept_grants.py
 */

import { apiDelete, apiGet, apiPost } from "@/api/client";
import type {
  DeptGrant,
  DeptGrantCreateRequest,
  DeptGrantList,
  OkResponse,
} from "@/api/secret/types";

const BASE = "/secret/v1/credentials";

/** `GET /credentials/{id}/dept-grants` — список DeptGrant'ов кред. */
export function listDeptGrants(credId: string): Promise<DeptGrantList> {
  return apiGet<DeptGrantList>(`${BASE}/${credId}/dept-grants`);
}

/**
 * `POST /credentials/{id}/dept-grants` — выдать DeptGrant recipient-dep'у.
 *
 * `recipient_dept_id` обязателен. Гейтится owner dep_admin / admin
 * secret_service владеющего dep'а.
 */
export function addDeptGrant(
  credId: string,
  body: DeptGrantCreateRequest,
): Promise<DeptGrant> {
  return apiPost<DeptGrant>(`${BASE}/${credId}/dept-grants`, body);
}

/** `DELETE /credentials/{id}/dept-grants/{grantId}` — снять DeptGrant (cascade RoleACL). */
export function revokeDeptGrant(
  credId: string,
  grantId: string,
): Promise<OkResponse> {
  return apiDelete<OkResponse>(`${BASE}/${credId}/dept-grants/${grantId}`);
}
