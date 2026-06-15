/**
 * Thin wrappers для `secret_service` `/secret/v1/credentials/{id}/user-acl`.
 *
 * UserACL — пер-пользовательский доступ к креде (в дополнение к RoleACL по
 * ролям). Выдаётся владельцем personal-кред'ы либо dep_admin для dept/cross.
 *
 * Source of truth:
 *   secret_service/src/api/v1/endpoints/user_acls.py
 *   secret_service/src/schemas/user_acls.py
 */

import { apiDelete, apiGet, apiPost } from "@/api/client";
import type {
  OkResponse,
  UserACL,
  UserACLCreateRequest,
  UserACLList,
} from "@/api/secret/types";

const BASE = "/secret/v1/credentials";

/** `GET /credentials/{id}/user-acl` — список UserACL кред. */
export function listUserAcls(credId: string): Promise<UserACLList> {
  return apiGet<UserACLList>(`${BASE}/${credId}/user-acl`);
}

/**
 * `POST /credentials/{id}/user-acl` — выдать доступ пользователю.
 *
 * `user_id` обязателен (`usr_…`). Backend отбивает попытку выдать ACL
 * владельцу (USER_ACL_OWNER_REDUNDANT) или самому себе
 * (USER_ACL_SELF_REDUNDANT), а также дубль (USER_ACL_DUPLICATE).
 */
export function addUserAcl(
  credId: string,
  body: UserACLCreateRequest,
): Promise<UserACL> {
  return apiPost<UserACL>(`${BASE}/${credId}/user-acl`, body);
}

/** `DELETE /credentials/{id}/user-acl/{aclId}` — снять доступ пользователя. */
export function revokeUserAcl(
  credId: string,
  aclId: string,
): Promise<OkResponse> {
  return apiDelete<OkResponse>(`${BASE}/${credId}/user-acl/${aclId}`);
}
