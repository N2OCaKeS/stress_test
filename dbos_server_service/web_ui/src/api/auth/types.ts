/**
 * TypeScript shapes for auth_service `/api/auth/v1/*`.
 *
 * Source of truth: `auth_service/API_ENDPOINTS.md`. Fields marked
 * `// TODO: clarify` are inferred from prose where the doc does not spell out
 * an exact JSON schema; revisit once OpenAPI is published.
 */

// ---------------------------------------------------------------------------
// Common
// ---------------------------------------------------------------------------

export type Iso8601 = string;

/**
 * Uniform error envelope returned for every non-2xx response.
 * See "Формат ошибки" in API_ENDPOINTS.md.
 */
export interface ApiErrorPayload {
  error: string;
  error_code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
  timestamp?: Iso8601;
}

// ---------------------------------------------------------------------------
// Identity / roles
// ---------------------------------------------------------------------------

/**
 * Platform-wide role. `null` means «no platform role» (regular user, service
 * roles only).
 *
 * Note: backend currently uses the legacy spellings `loging_admin` /
 * `loging_reader`; the UI side already uses `logging_*`. We accept both to
 * survive the rename window — adapters should normalize on read.
 */
export type PlatformRole =
  | "account_admin"
  | "department_admin"
  | "dep_admin"
  | "loging_admin"
  | "logging_admin"
  | "loging_reader"
  | "logging_reader"
  | null;

export type ActorType = "user" | "bot" | "oauth_client" | "pat";

export type UserStatus = "ACTIVE" | "BANNED" | "BLOCKED";

/** Subset of services known to the platform. */
export type ServiceName =
  | "auth_service"
  | "secret_service"
  | "server_service"
  | "worker_service"
  | "loging_service"
  | "config_service"
  | "docker_registry"
  | string;

/**
 * `IdentityContext` returned by `/login`, `/refresh`, `/me`. The doc does not
 * spell every field out — minimum schema below covers the UI's needs. New
 * fields land non-breaking; unknown ones survive as part of the parent object.
 */
export interface IdentityContext {
  actor_type: ActorType;
  user_id?: string | null;
  username?: string | null;
  display_name?: string | null;
  email?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  platform_role?: PlatformRole;
  is_active?: boolean;
  is_banned?: boolean;
  status?: UserStatus;
  must_change_password?: boolean;
  mfa_enabled?: boolean; // TODO: clarify — not described in API_ENDPOINTS.md
  /** Services the principal is allowed to call. */
  allowed_services?: ServiceName[];
  /** Per-service role lists (effective view, INTERSECT'ed by backend). */
  service_roles?: Record<ServiceName, string[]>;
  /** Convenience flag set when any platform-admin-ish role is present. */
  has_admin?: boolean;
  /** Optional jti / session id; not used by UI directly. */
  jti?: string;
  sid?: string;
  exp?: number;
}

// ---------------------------------------------------------------------------
// Auth flow
// ---------------------------------------------------------------------------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
  identity: IdentityContext;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface RefreshResponse {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
}

export interface LogoutRequest {
  refresh_token: string;
}

export interface LogoutResponse {
  ok: true;
}

/** `GET /me` response — refreshed IdentityContext from DB. */
export type MeResponse = IdentityContext;

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export interface User {
  id: string;
  username: string;
  email: string | null;
  department_id: string | null;
  department_name?: string | null;
  platform_role: PlatformRole;
  status: UserStatus;
  is_active: boolean;
  is_banned: boolean;
  must_change_password?: boolean;
  created_at: Iso8601;
  updated_at?: Iso8601;
}

export interface UserCreateRequest {
  username: string;
  password: string;
  email?: string;
  department_id?: string | null;
  platform_role?: PlatformRole;
  initial_roles?: Array<{
    service_name: ServiceName;
    roles: string[];
  }>;
}

export interface UserPatchRequest {
  email?: string;
  department_id?: string | null;
  status?: UserStatus;
  platform_role?: PlatformRole;
}

export interface UserResetPasswordRequest {
  new_password: string;
}

export interface MePasswordChangeRequest {
  old_password: string;
  new_password: string;
}

/**
 * Тело `PATCH /api/auth/v1/me` — self-service апдейт.
 *
 * Whitelist: backend принимает только `display_name` и `email`.
 * `null` — явная очистка поля. Минимум одно поле обязательно: пустое
 * тело отдаст 422 EMPTY_UPDATE.
 */
export interface MeUpdateRequest {
  display_name?: string | null;
  email?: string | null;
}

export interface BanRequest {
  ban_type: "permanent" | "temporary";
  reason: string;
  expires_at?: Iso8601 | null;
}

export interface UserDeleteRequest {
  reason: string;
}

export interface SessionItem {
  session_id: string;
  created_at: Iso8601;
  last_used_at: Iso8601 | null;
  expires_at: Iso8601;
  ip_address: string | null;
  user_agent: string | null;
  is_current: boolean;
}

export interface SessionListResponse {
  items: SessionItem[];
  total: number;
}

export interface UserPermissionsResponse {
  user_id: string;
  username: string;
  department_id: string | null;
  department_name: string | null;
  platform_role: PlatformRole;
  is_active: boolean;
  is_banned: boolean;
  status: UserStatus;
  direct_service_roles: Array<{
    service_name: ServiceName;
    role_name: string;
    assigned_at: Iso8601;
    assigned_by: string;
  }>;
  groups: Array<{
    group_id: string;
    group_name: string;
    department_id: string;
    joined_at: Iso8601;
    service_accesses: Array<{ service_name: ServiceName }>;
    service_roles: Array<{ service_name: ServiceName; role_name: string }>;
  }>;
  allowed_services: ServiceName[];
  service_roles: Record<ServiceName, string[]>;
}

// ---------------------------------------------------------------------------
// Departments
// ---------------------------------------------------------------------------

export interface Department {
  id: string;
  name: string;
  description?: string | null;
  created_at: Iso8601;
  updated_at?: Iso8601;
}

export interface DepartmentCreateRequest {
  name: string;
}

export interface DepartmentUpdateRequest {
  name?: string;
  description?: string;
}

// ---------------------------------------------------------------------------
// Services / service roles
// ---------------------------------------------------------------------------

export interface Service {
  service_name: ServiceName;
  description?: string | null;
  is_active?: boolean;
  created_at: Iso8601;
}

export interface ServiceCreateRequest {
  service_name: ServiceName;
  description?: string;
}

export interface ServiceRole {
  role_name: string;
  description?: string | null;
  department_id: string;
  service_name: ServiceName;
  is_system: boolean;
  created_at: Iso8601;
}

export interface ServiceRoleCreateRequest {
  role_name: string;
  description?: string;
}

export interface ServiceRolePatchRequest {
  description?: string;
}

export interface ServiceRoleBulkAssignRequest {
  user_ids: string[];
}

// ---------------------------------------------------------------------------
// Personal Access Tokens
// ---------------------------------------------------------------------------

export interface PATCreateRequest {
  name: string;
  expires_at?: Iso8601 | null;
  allowed_services?: ServiceName[];
}

export interface PATCreateResponse {
  token_id: string;
  /** Shown once. */
  token: string;
  name: string;
  expires_at: Iso8601 | null;
}

export interface PersonalAccessToken {
  token_id: string;
  name: string;
  expires_at: Iso8601 | null;
  allowed_services: ServiceName[];
  created_at: Iso8601;
  last_used_at?: Iso8601 | null;
  revoked_at?: Iso8601 | null;
}

// ---------------------------------------------------------------------------
// Bots
// ---------------------------------------------------------------------------

export type BotStatus = "active" | "disabled";

export interface Bot {
  id: string;
  name: string;
  description: string | null;
  department_id: string;
  status: BotStatus;
  allowed_services: ServiceName[];
  created_at: Iso8601;
  updated_at?: Iso8601;
  created_by?: string | null;
}

export interface BotCreateRequest {
  name: string;
  department_id: string;
  allowed_services: ServiceName[];
  description?: string;
}

export interface BotPatchRequest {
  name?: string;
  description?: string;
  status?: BotStatus;
  allowed_services?: ServiceName[];
}

export interface BotTokenCreateRequest {
  name: string;
  expires_at?: Iso8601 | null;
}

export interface BotTokenCreateResponse {
  token_id: string;
  /** Shown once. */
  token: string;
  name: string;
  expires_at: Iso8601 | null;
}

export interface BotTokenListItem {
  token_id: string;
  name: string;
  expires_at: Iso8601 | null;
  created_at: Iso8601;
  last_used_at?: Iso8601 | null;
  revoked_at?: Iso8601 | null;
}

export interface BotRoleResponse {
  bot_id: string;
  service_name: ServiceName;
  roles: string[];
}

export interface BotRoleAssignRequest {
  service_name: ServiceName;
  roles: string[];
}

// ---------------------------------------------------------------------------
// Groups
// ---------------------------------------------------------------------------

export interface Group {
  id: string;
  department_id: string;
  name: string;
  description: string | null;
  created_at: Iso8601;
  updated_at?: Iso8601;
}

export interface GroupCreateRequest {
  department_id: string;
  name: string;
  description?: string;
}

export interface GroupPatchRequest {
  name?: string;
  description?: string;
}

export interface GroupMember {
  user_id: string;
  username: string;
  added_at: Iso8601;
}

export interface BotMemberResponse {
  bot_id: string;
  name: string;
  added_at: Iso8601;
}

export interface GroupServiceAccessResponse {
  group_id: string;
  service_name: ServiceName;
  granted_at: Iso8601;
}

// ---------------------------------------------------------------------------
// OAuth2 / Docker
// ---------------------------------------------------------------------------

export interface OAuth2Client {
  id: string;
  client_id: string;
  department_id: string;
  name: string;
  description?: string | null;
  redirect_uris: string[];
  allowed_scopes: string[];
  grant_types: string[];
  is_active: boolean;
  is_public?: boolean;
  created_at: Iso8601;
}

export interface OAuth2ClientCreateRequest {
  name: string;
  description?: string;
  department_id: string;
  redirect_uris: string[];
  allowed_scopes: string[];
  grant_types: string[];
}

export interface OAuth2ClientCreatedResponse extends OAuth2Client {
  /** Shown once. */
  client_secret: string;
}

export interface DockerRegistryConfig {
  department_id: string;
  is_enabled: boolean;
  pull_policy: "all" | "restricted";
  pull_user_ids: string[];
  push_user_ids: string[];
  created_at: Iso8601;
  updated_at?: Iso8601;
}

export interface DockerRegistryConfigPutRequest {
  pull_policy: "all" | "restricted";
  pull_user_ids?: string[];
  push_user_ids?: string[];
}

export interface DockerRegistryConfigPatchRequest
  extends Partial<DockerRegistryConfigPutRequest> {
  is_enabled?: boolean;
}

export interface DockerTokenResponse {
  token: string;
  access_token: string;
  expires_in: number;
  issued_at: Iso8601;
}
