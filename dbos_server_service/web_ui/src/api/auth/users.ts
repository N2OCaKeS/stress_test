/**
 * Thin wrappers for auth_service `/users/*` endpoints.
 *
 * One function per backend route; types come from `./types.ts`. List endpoints
 * return both the `items` and the total count parsed from `X-Total-Count`.
 */

import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";
import type {
  BanRequest,
  Group,
  MePasswordChangeRequest,
  MeResponse,
  SessionListResponse,
  User,
  UserCreateRequest,
  UserDeleteRequest,
  UserPatchRequest,
  UserPermissionsResponse,
  UserResetPasswordRequest,
} from "@/api/auth/types";

export interface PaginatedList<T> {
  items: T[];
  total: number;
}

export interface ListUsersParams {
  limit?: number;
  offset?: number;
  include_banned?: boolean;
  status?: "active" | "banned" | "blocked";
}

// ---------------------------------------------------------------------------
// list helper that taps both body and X-Total-Count header in one round-trip
// ---------------------------------------------------------------------------

const BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

async function listWithTotal<T>(
  path: string,
  query: Record<string, string | number | boolean | null | undefined>,
): Promise<PaginatedList<T>> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === null || v === undefined) continue;
    params.append(k, String(v));
  }
  const qs = params.toString();
  const url = `${BASE_URL}${path}${qs ? `?${qs}` : ""}`;
  const access = getAccessToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (access) headers["Authorization"] = `Bearer ${access}`;
  const res = await fetch(url, {
    method: "GET",
    headers,
    credentials: "same-origin",
  });
  if (!res.ok) {
    // Fall back to the shared client for proper error envelope + refresh.
    // This path is hit when first request fails (401/403/etc.).
    const items = await apiGet<T[]>(path, { query });
    return { items, total: items.length };
  }
  const totalHeader = res.headers.get("X-Total-Count");
  const items = ((await res.json()) as T[]) ?? [];
  const total = totalHeader ? Number.parseInt(totalHeader, 10) : items.length;
  return { items, total: Number.isFinite(total) ? total : items.length };
}

// ---------------------------------------------------------------------------
// Users — list / detail / mutation
// ---------------------------------------------------------------------------

// auth_service возвращает `user_id` в payload, UI работает с `id`.
// Нормализуем единожды на API-слое, чтобы pages могли обращаться к `u.id`.
interface BackendUser extends Omit<User, "id"> {
  user_id?: string;
  id?: string;
}

function normalizeUser(u: BackendUser): User {
  return { ...u, id: u.id ?? u.user_id ?? "" } as User;
}

export async function listUsers(
  params: ListUsersParams = {},
): Promise<PaginatedList<User>> {
  const { limit = 50, offset = 0, include_banned, status } = params;
  const res = await listWithTotal<BackendUser>("/auth/v1/users", {
    limit,
    offset,
    include_banned,
    status,
  });
  return { items: res.items.map(normalizeUser), total: res.total };
}

export async function listUsersByDepartment(
  departmentId: string,
  params: ListUsersParams = {},
): Promise<PaginatedList<User>> {
  const { limit = 50, offset = 0, include_banned, status } = params;
  const res = await listWithTotal<BackendUser>(
    `/auth/v1/users/department/${departmentId}`,
    { limit, offset, include_banned, status },
  );
  return { items: res.items.map(normalizeUser), total: res.total };
}

export async function getUser(userId: string): Promise<User> {
  const raw = await apiGet<BackendUser>(`/auth/v1/users/${userId}`);
  return normalizeUser(raw);
}

export async function createUser(body: UserCreateRequest): Promise<User> {
  const raw = await apiPost<BackendUser>("/auth/v1/users", body);
  return normalizeUser(raw);
}

export async function updateUser(userId: string, body: UserPatchRequest): Promise<User> {
  const raw = await apiPatch<BackendUser>(`/auth/v1/users/${userId}`, body);
  return normalizeUser(raw);
}

export function deleteUser(userId: string, body: UserDeleteRequest): Promise<void> {
  return apiDelete<void>(`/auth/v1/users/${userId}`, body);
}

// ---------------------------------------------------------------------------
// Lifecycle: enable / disable / unlock / ban / unban
// ---------------------------------------------------------------------------

// auth_service exposes only ban/unban for user lifecycle. enable/disable map
// to unban/ban respectively. Lockout reset endpoint does not exist server-side
// — `unlockUser` returns 501 so callers see explicit not-implemented.
export function enableUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unban`, {});
}

export function disableUser(
  userId: string,
  reason: string = "disabled via admin UI",
): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/ban`, { reason });
}

export function unlockUser(_userId: string): Promise<User> {
  return Promise.reject(
    new ApiError(501, {
      error: "not_implemented",
      error_code: "NOT_IMPLEMENTED",
      message:
        "auth_service не реализует endpoint для снятия lockout — снимается автоматически по таймауту LOCKOUT_MINUTES",
    }),
  );
}

export function banUser(userId: string, body: BanRequest): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/ban`, body);
}

export function unbanUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unban`, {});
}

// ---------------------------------------------------------------------------
// Passwords / sessions / role-related
// ---------------------------------------------------------------------------

export function resetUserPassword(
  userId: string,
  body: UserResetPasswordRequest,
): Promise<void> {
  return apiPost<void>(`/auth/v1/users/${userId}/reset-password`, body);
}

// Backend endpoint `POST /users/{id}/force-password-change` отсутствует в
// auth_service (см. API_ENDPOINTS.md, Users). Флаг `must_change_password=True`
// сейчас выставляется только через `POST /users/{id}/reset-password` (там
// admin задаёт новый пароль и одновременно поднимает флаг).
// TODO(auth_service): добавить standalone-endpoint, который поднимает
// `must_change_password=True` без смены пароля, и подключить его сюда.
// Пока — функция-заглушка: бросает ApiError, чтобы случайный вызов из UI
// падал предсказуемо вместо runtime 404.
export function forcePasswordChange(_userId: string): Promise<void> {
  return Promise.reject(
    new ApiError(501, {
      error: "not_implemented",
      error_code: "NOT_IMPLEMENTED",
      message:
        "force-password-change endpoint отсутствует в auth_service; используй reset-password",
    }),
  );
}

// Admin session management. Маршруты дублируют /me/sessions/*, но с
// target user_id и отдельной серией audit-событий (`user.sessions_admin_*`).
// RBAC: account_admin — любой; dep_admin — только в своём отделе (иначе 403).
export function listUserSessions(
  userId: string,
): Promise<SessionListResponse> {
  return apiGet<SessionListResponse>(`/auth/v1/users/${userId}/sessions`);
}

export function revokeUserAllSessions(
  userId: string,
): Promise<{ revoked_count: number }> {
  return apiPost<{ revoked_count: number }>(
    `/auth/v1/users/${userId}/sessions/revoke`,
    {},
  );
}

export function revokeUserSessionById(
  userId: string,
  sessionId: string,
): Promise<{ revoked_count: number }> {
  return apiDelete<{ revoked_count: number }>(
    `/auth/v1/users/${userId}/sessions/${sessionId}`,
  );
}

// Backwards-compat alias: старое имя `revokeUserSessions` (return shape
// идентичен) сохраняем для существующих call-site'ов в UsersAccountAdmin/
// UsersDepAdmin/UserDetail/ServicesUsers. Раньше эта функция возвращала
// 501 заглушку — теперь делает реальный admin-revoke.
export function revokeUserSessions(
  userId: string,
): Promise<{ revoked_count: number }> {
  return revokeUserAllSessions(userId);
}

export function changeMyPassword(
  body: MePasswordChangeRequest,
): Promise<void> {
  return apiPost<void>("/auth/v1/users/me/password", body);
}

export function listMySessions(): Promise<SessionListResponse> {
  return apiGet<SessionListResponse>("/auth/v1/users/me/sessions");
}

export function revokeAllMySessions(
  exceptCurrent = true,
): Promise<{ revoked_count: number }> {
  return apiPost<{ revoked_count: number }>(
    "/auth/v1/users/me/sessions/revoke",
    { except_current: exceptCurrent },
  );
}

export function revokeMySession(
  sessionId: string,
): Promise<{ revoked_count: number }> {
  return apiDelete<{ revoked_count: number }>(
    `/auth/v1/users/me/sessions/${sessionId}`,
  );
}

export function getMe(): Promise<MeResponse> {
  return apiGet<MeResponse>("/auth/v1/me");
}

// auth_service не реализует PATCH /me — профиль читается через GET /me,
// смена пароля идёт через POST /users/me/password.
export function updateMe(_body: { email?: string }): Promise<MeResponse> {
  return Promise.reject(
    new ApiError(501, {
      error: "not_implemented",
      error_code: "NOT_IMPLEMENTED",
      message:
        "PATCH /me не реализован в auth_service. Профиль read-only через GET /me.",
    }),
  );
}

// ---------------------------------------------------------------------------
// User permissions / groups
// ---------------------------------------------------------------------------

export function getUserPermissions(
  userId: string,
): Promise<UserPermissionsResponse> {
  return apiGet<UserPermissionsResponse>(`/auth/v1/users/${userId}/permissions`);
}

export function getUserGroups(userId: string): Promise<Group[]> {
  return apiGet<Group[]>(`/auth/v1/users/${userId}/groups`);
}

// ---------------------------------------------------------------------------
// Service roles assignment (replace-semantics)
// ---------------------------------------------------------------------------

/**
 * Назначить service-роли юзеру для указанного сервиса.
 *
 * Replace-семантика: переданный `roles` массив заменяет текущий набор ролей
 * юзера в этом сервисе целиком. Чтобы снять все роли — передать пустой массив.
 *
 * Backend контракт (POST /users/{user_id}/roles):
 *   body = { service_name: str, roles: list[str] }
 *
 * Доступ: account_admin (любой юзер) или department_admin своего отдела.
 * Возможные ошибки: USER_NOT_FOUND, USER_INACTIVE, USER_ROLE_UPDATE_FORBIDDEN,
 * SERVICE_NOT_ALLOWED_FOR_DEPARTMENT, INVALID_SERVICE_ROLE.
 */
export interface AssignUserRolesRequest {
  service_name: string;
  roles: string[];
}

export function assignUserRoles(
  userId: string,
  body: AssignUserRolesRequest,
): Promise<void> {
  return apiPost<void>(`/auth/v1/users/${userId}/roles`, body);
}
