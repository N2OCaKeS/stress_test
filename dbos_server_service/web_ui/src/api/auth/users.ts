/**
 * Thin wrappers for auth_service `/users/*` endpoints.
 *
 * One function per backend route; types come from `./types.ts`. List endpoints
 * return both the `items` and the total count parsed from `X-Total-Count`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";
import type {
  BanRequest,
  Group,
  MePasswordChangeRequest,
  MeResponse,
  MeUpdateRequest,
  SessionListResponse,
  User,
  UserCreateRequest,
  UserDeleteRequest,
  UserPatchRequest,
  UserPermissionsResponse,
  UserResetPasswordRequest,
  UserResolveResponse,
  UserStatus,
} from "@/api/auth/types";

export interface PaginatedList<T> {
  items: T[];
  total: number;
  /**
   * `false`, если `total` — не реальный счётчик из `X-Total-Count`, а фоллбэк
   * на `items.length` (заголовок не пришёл либо запрос ушёл по error-пути
   * `apiGet`, который заголовки не отдаёт). Потребители, которым важна
   * правда о полном размере (баннер усечения «N из M»), могут отличить
   * «знаем точно» от «столько загрузили». Отсутствие поля трактуется как
   * known — обратная совместимость со старыми вызовами.
   */
  totalKnown?: boolean;
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

export async function listWithTotal<T>(
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
    // Срабатывает на любом не-ok первого fetch'а (401/403/5xx/…). На 4xx/5xx
    // без восстановления apiGet бросит ApiError — обработку забирает вызывающий.
    // Если же apiGet вернул данные (например, после успешного refresh на 401),
    // заголовка X-Total-Count тут уже нет: total — это лишь размер страницы,
    // помечаем его как неточный.
    const items = await apiGet<T[]>(path, { query });
    return { items, total: items.length, totalKnown: false };
  }
  const totalHeader = res.headers.get("X-Total-Count");
  const parsed = totalHeader ? Number.parseInt(totalHeader, 10) : NaN;
  const items = ((await res.json()) as T[]) ?? [];
  const known = Number.isFinite(parsed);
  return {
    items,
    total: known ? parsed : items.length,
    totalKnown: known,
  };
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

// auth_service сериализует status как нижний регистр StrEnum
// (active/blocked/banned). Раньше страницы латали это по месту: где-то
// `?.toLowerCase()`, где-то dual-check с обоими регистрами. Единая точка
// нормализации убирает дубль и даёт стабильный union для бейджей.
const KNOWN_STATUSES: readonly UserStatus[] = ["active", "blocked", "banned"];

/**
 * Приводит сырой `status` (любой регистр, unknown-строка, null) к каноничному
 * lowercase-union. Неизвестное значение фоллбэкается на `active` — это
 * безопасный дефолт для бейджей и не превращает живого юзера в `danger`.
 */
export function normalizeUserStatus(
  raw: string | null | undefined,
): UserStatus {
  const s = raw?.toLowerCase?.() ?? "";
  return (KNOWN_STATUSES as readonly string[]).includes(s)
    ? (s as UserStatus)
    : "active";
}

/** True, если юзер забанен — по нормализованному статусу или явному флагу. */
export function isUserBanned(u: {
  status?: string | null;
  is_banned?: boolean | null;
}): boolean {
  return u.is_banned ?? normalizeUserStatus(u.status) === "banned";
}

/**
 * Класс бейджа (`ok`/`warn`/`danger`) для статуса юзера. Один источник
 * правды для всех мест, которые раньше повторяли тернарник по месту.
 */
export function userStatusBadgeKind(
  raw: string | null | undefined,
): "ok" | "warn" | "danger" {
  const s = normalizeUserStatus(raw);
  if (s === "active") return "ok";
  if (s === "blocked") return "warn";
  return "danger";
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
  return {
    items: res.items.map(normalizeUser),
    total: res.total,
    totalKnown: res.totalKnown,
  };
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
  return {
    items: res.items.map(normalizeUser),
    total: res.total,
    totalKnown: res.totalKnown,
  };
}

export async function getUser(userId: string): Promise<User> {
  const raw = await apiGet<BackendUser>(`/auth/v1/users/${userId}`);
  return normalizeUser(raw);
}

/**
 * Батч-резолв `usr_*` → username для любого аутентифицированного юзера (не
 * только админов): в отличие от `getUser`/`/users/{id}`, этот endpoint не
 * гейтится правами. Возвращаются только найденные id; отсутствующие молча
 * пропускаются. Лимит — 200 id за запрос. Пустой список не дёргает сеть.
 */
export async function getUserLabels(
  ids: string[],
): Promise<Record<string, string>> {
  if (ids.length === 0) return {};
  const res = await apiGet<{ labels: Record<string, string> }>(
    "/auth/v1/users/labels",
    { query: { ids: ids.join(",") } },
  );
  return res.labels;
}

/**
 * Точечный резолв username → id в видимом scope (свой отдел; account_admin —
 * cross-dept). Для адресации шаринга, когда списки юзеров недоступны.
 * Чужой/несуществующий username → 404 USER_NOT_FOUND (без enumeration).
 */
export function resolveUser(username: string): Promise<UserResolveResponse> {
  return apiGet<UserResolveResponse>("/auth/v1/users/resolve", {
    query: { username },
  });
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

// auth_service exposes ban/unban for user lifecycle plus an explicit unlock
// for lockout reset. enable/disable map to unban/ban respectively.
export function enableUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unban`, {});
}

export function disableUser(
  userId: string,
  reason: string = "disabled via admin UI",
): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/ban`, { reason });
}

// Снимает lockout (сбрасывает failed-attempts и locked_until). Раньше тут была
// 501-заглушка — endpoint появился на стороне auth_service.
export function unlockUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unlock`, {});
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

// `POST /users/{id}/force-password-change` — standalone-флаг-флип
// `must_change_password=True` без замены пароля. Admin/dep_admin зовут
// его, когда хотят, чтобы target сменил пароль на ближайшем входе, не
// выдавая новый временный пароль самим. После успеха middleware
// блокирует target'а на всех ручках кроме `/users/me/password`.
export function forcePasswordChange(userId: string): Promise<void> {
  return apiPost<void>(
    `/auth/v1/users/${userId}/force-password-change`,
    {},
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

// `PATCH /api/auth/v1/me` — self-service апдейт собственного профиля
// (display_name / email). Backend whitelist'ит поля через pydantic
// `extra='forbid'`: попытка передать `platform_role`/`department_id`/
// `username` отдаст 422 на этапе валидации.
export function patchMe(body: MeUpdateRequest): Promise<MeResponse> {
  return apiPatch<MeResponse>("/auth/v1/me", body);
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
