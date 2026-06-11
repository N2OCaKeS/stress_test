/**
 * Типы запросов/ответов `secret_service` `/secret/v1/*`.
 *
 * Source of truth (read-only зеркало):
 *   secret_service/src/schemas/credentials.py
 *   secret_service/src/schemas/role_acls.py
 *   secret_service/src/schemas/dept_grants.py
 *   secret_service/src/schemas/common.py
 *
 * Все имена полей повторяют pydantic-схемы 1:1 — UI шлёт ровно то, что бэк
 * валидирует. Расхождение имени = 422.
 */

// ── общие ────────────────────────────────────────────────────────────────────

export type CredentialScope = "personal" | "department" | "cross_department";
export type CredentialStatus = "active" | "blocked";

/** `{ ok: true }` от mutation-эндпоинтов без тела (delete/revoke). */
export interface OkResponse {
  ok: boolean;
}

// ── credentials ───────────────────────────────────────────────────────────────

/** `CredentialRead` — метаданные кред без plaintext-секрета. */
export interface Credential {
  id: string;
  name: string;
  service: string;
  scope: CredentialScope;
  owner_user_id: string | null;
  owner_dept_id: string | null;
  login: string | null;
  status: CredentialStatus;
  created_by: string;
  created_at: string;
  updated_at: string;
  blocked_at: string | null;
  blocked_reason: string | null;
  visible_to_dept: boolean;
  valid_from: string | null;
  valid_to: string | null;
}

/**
 * Урезанная проекция кред для роли `guest` — только факт существования.
 * Backend отдаёт этот shape вместо `Credential`, когда identity guest-only.
 */
export interface CredentialGuest {
  id: string;
  name: string;
  service: string;
  scope: CredentialScope;
  visible_to_dept: boolean;
}

/** `CredentialCreate` — тело `POST /credentials`. */
export interface CredentialCreateRequest {
  name: string;
  service: string;
  scope: CredentialScope;
  /** plaintext секрет — шифруется at-rest, наружу не возвращается. */
  secret: string;
  login?: string | null;
  /** обязателен для scope `department` / `cross_department`, пустой для `personal`. */
  owner_dept_id?: string | null;
  visible_to_dept?: boolean;
  valid_from?: string | null;
  /** если задан — обязан быть в будущем. */
  valid_to?: string | null;
}

/** `CredentialUpdate` — тело `PATCH /credentials/{id}` (partial). */
export interface CredentialUpdateRequest {
  name?: string | null;
  login?: string | null;
  secret?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
}

/** `CredentialRevealResponse`. `secret_b64` = base64(plaintext). */
export interface CredentialRevealResponse {
  login: string | null;
  secret_b64: string;
}

/** `TransferRequest` — ровно одно из owner-полей + обязательный reason. */
export interface TransferRequest {
  new_owner_user_id?: string | null;
  new_owner_dept_id?: string | null;
  reason: string;
}

/** `AdminDeleteRequest` — reason обязателен только для admin-override. */
export interface AdminDeleteRequest {
  reason?: string | null;
}

/** Cursor-envelope списка кред. */
export interface CredentialList {
  items: Credential[];
  next_cursor: string | null;
}

/** Cursor-envelope guest-листинга. */
export interface CredentialGuestList {
  items: CredentialGuest[];
  next_cursor: string | null;
}

// ── role ACL ──────────────────────────────────────────────────────────────────

/** `RoleACLCreate` — тело `POST /credentials/{id}/acl`. */
export interface RoleACLCreateRequest {
  dept_id: string;
  role_name: string;
  can_read?: boolean;
  can_write?: boolean;
}

export interface RoleACL {
  id: string;
  cred_id: string;
  dept_id: string;
  role_name: string;
  can_read: boolean;
  can_write: boolean;
  granted_by_user_id: string;
  granted_at: string;
}

export interface RoleACLList {
  items: RoleACL[];
}

// ── dept grants ─────────────────────────────────────────────────────────────

/** `DeptGrantCreate` — тело `POST /credentials/{id}/dept-grants`. */
export interface DeptGrantCreateRequest {
  recipient_dept_id: string;
}

export interface DeptGrant {
  id: string;
  cred_id: string;
  recipient_dept_id: string;
  granted_by_user_id: string;
  granted_at: string;
}

export interface DeptGrantList {
  items: DeptGrant[];
}
