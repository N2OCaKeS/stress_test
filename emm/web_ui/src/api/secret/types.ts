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

export type CredentialScope = "personal" | "department" | "cross_department" | "service";
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

/**
 * `CredentialCreate` — что UI собирает в форме. `secret` тут plaintext;
 * api-слой кодирует его в `secret_b64` перед отправкой (см. `credentials.ts`).
 */
export interface CredentialCreateRequest {
  name: string;
  service: string;
  scope: CredentialScope;
  /** plaintext секрет — шифруется at-rest, наружу не возвращается. */
  secret: string;
  login?: string | null;
  /** обязателен для общих и сервисных учётных данных, пустой для `personal`. */
  owner_dept_id?: string | null;
  visible_to_dept?: boolean;
  valid_from?: string | null;
  /** если задан — обязан быть в будущем. */
  valid_to?: string | null;
}

/**
 * `CredentialUpdate` — что UI собирает в форме (partial). `secret` plaintext;
 * если поле отсутствует — секрет не перешифровывается.
 */
export interface CredentialUpdateRequest {
  name?: string | null;
  login?: string | null;
  secret?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
}

/** Wire-тело `POST /credentials` — `secret` уже закодирован в `secret_b64`. */
export interface CredentialCreateWire
  extends Omit<CredentialCreateRequest, "secret"> {
  /** base64(plaintext). */
  secret_b64: string;
}

/** Wire-тело `PATCH /credentials/{id}` — `secret` → `secret_b64` (опционально). */
export interface CredentialUpdateWire
  extends Omit<CredentialUpdateRequest, "secret"> {
  secret_b64?: string;
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

/** `RoleACLUpsert` — тело `PUT /credentials/{id}/acl`. */
export interface RoleACLUpsertRequest {
  dept_id: string;
  role_name: string;
  can_read?: boolean;
  can_write?: boolean;
}

/**
 * `RoleACLUpsertResponse` — ответ `PUT /credentials/{id}/acl`. `acl` = null,
 * когда оба флага сняты и строка удалена (доступ снят).
 */
export interface RoleACLUpsertResponse {
  ok: boolean;
  acl: RoleACL | null;
}

// ── user ACL ──────────────────────────────────────────────────────────────────

/** `UserACLCreate` — тело `POST /credentials/{id}/user-acl`. */
export interface UserACLCreateRequest {
  user_id: string;
  can_read?: boolean;
  can_write?: boolean;
}

export interface UserACL {
  id: string;
  cred_id: string;
  user_id: string;
  can_read: boolean;
  can_write: boolean;
  granted_by_user_id: string;
  created_at: string;
}

export interface UserACLList {
  items: UserACL[];
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

// ── permissions (тип-wide матрица прав) ───────────────────────────────────────

/**
 * Тип сущности матрицы прав secret_service. У сервиса ровно одна управляемая
 * сущность — `secret` (сам credential). Строковый хвост держит дверь
 * приоткрытой, если backend когда-нибудь расширит `ENTITY_ACTIONS`.
 */
export type SecretEntityType = "secret" | (string & {});

/**
 * Имя роли. Системные `guest`/`admin` сеются автоматически и защищены
 * фиксированной матрицей (grant/revoke по ним → 409 SYSTEM_ROLE_IMMUTABLE).
 * Остальные — кастомные роли отдела, отсюда хвост `string`.
 */
export type SecretRoleName = "guest" | "admin" | (string & {});

/**
 * Действие над секретом. Лесенка доступа к значению:
 * `read` (метаданные) ⊂ `reveal` (значение) ⊂ `write`. Прочие —
 * привилегированные операции управления кред'ой. Полный whitelist —
 * `SecretAction` в `secret_service/src/core/constants.py`.
 */
export type SecretActionName =
  | "read"
  | "reveal"
  | "write"
  | "delete"
  | "grant_acl"
  | "grant_dept"
  | "manage_status"
  | (string & {});

/**
 * Одна строка матрицы `entity_permissions` (`PermissionResponse`).
 *
 * `department_id` — scope-дискриминатор: `null` = system-wide grant, строка —
 * per-department. `granted_by` — `null` для seed-данных.
 */
export interface SecretPermissionEntry {
  id: string;
  entity_type: SecretEntityType;
  role: SecretRoleName;
  action: SecretActionName;
  department_id: string | null;
  granted_by: string | null;
  created_at: string;
  updated_at: string;
  /** Появляется только при `describe=true` (`PermissionDescribedResponse`). */
  entity_description?: string;
  /** Появляется только при `describe=true`. */
  action_description?: string;
  /** Появляется только при `describe=true`. CRITICAL-аудит при изменении. */
  sensitive?: boolean;
}

/**
 * Действие в каталоге (`CatalogAction`). У secret_service нет worker-only
 * действий, поэтому флага `worker_only` в схеме нет.
 */
export interface SecretPermissionCatalogAction {
  action: SecretActionName;
  description: string;
  /** Чувствительное действие — CRITICAL severity в audit. */
  sensitive: boolean;
}

/** Сущность каталога (`CatalogEntity`) — описание и набор её действий. */
export interface SecretPermissionCatalogItem {
  entity_type: SecretEntityType;
  description: string;
  actions: SecretPermissionCatalogAction[];
}

/** Envelope для GET /permissions и GET /permissions/{entity_type}. */
export interface SecretPermissionListResponse {
  items: SecretPermissionEntry[];
  total: number;
  /** True — строки обогащены описаниями (`describe=true`). */
  described: boolean;
}

/**
 * Body PUT /permissions/{entity_type}/{role}/{action}.
 *
 * `target_department_id` опционален; caller обязан либо опустить, либо
 * передать собственный `department_id`, иначе 403 DEPARTMENT_ISOLATION.
 * Платформенный account_admin может задать целевой отдел без dept-isolation.
 */
export interface SecretPermissionGrantRequest {
  target_department_id?: string | null;
}
