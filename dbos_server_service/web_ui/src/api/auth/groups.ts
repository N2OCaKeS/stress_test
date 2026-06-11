/**
 * `/api/auth/v1/groups` + group-related cross-resource endpoints.
 *
 * Covers 16 endpoints from API_ENDPOINTS.md "Groups" section plus the
 * three `GET /users/{user_id}/groups` / `POST /users/{user_id}/groups`
 * / `DELETE /users/{user_id}/groups/{group_id}` shortcuts that live in
 * the users.py router but logically belong to the groups surface.
 *
 * All returned promises throw `ApiError` on non-2xx; pagination uses the
 * common `PaginationParams` (`limit` / `offset`) and the response is the
 * raw `list[...]` — `X-Total-Count` is exposed by the backend in headers
 * but not threaded back here because the fetch wrapper does not surface
 * response headers. For the few list-views that care about total, the
 * UI re-counts after the fact.
 */

import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
} from "@/api/client";
import type {
  Group,
  GroupCreateRequest,
  GroupPatchRequest,
  GroupMember,
  BotMemberResponse,
  GroupServiceAccessResponse,
  ServiceName,
} from "@/api/auth/types";

export interface PaginationParams {
  limit?: number;
  offset?: number;
}

export interface GroupRoleAssignment {
  group_id: string;
  service_name: ServiceName;
  roles: string[];
}

export interface GroupRoleAssignRequest {
  service_name: ServiceName;
  roles: string[];
}

export interface UserGroupsResponse {
  group_id: string;
  group_name: string;
  /** Backend отдаёт `added_at`; нормализуем в `joined_at` при чтении. */
  joined_at: string;
}

/** Сырая форма ответа `GET /users/{id}/groups` от backend'а. */
interface BackendUserGroup {
  group_id: string;
  group_name: string;
  added_at: string;
}

// ---------------------------------------------------------------------------
// List / get / create / patch / delete
// ---------------------------------------------------------------------------

export function listGroups(params: PaginationParams = {}): Promise<Group[]> {
  return apiGet<Group[]>("/auth/v1/groups", {
    query: {
      limit: params.limit ?? null,
      offset: params.offset ?? null,
    },
  });
}

export function listGroupsByDepartment(
  departmentId: string,
  params: PaginationParams = {},
): Promise<Group[]> {
  // Backend `/groups` сам сужает выдачу до отдела для department_admin
  // (см. group_service.list_groups). Параметр departmentId здесь декларативный
  // — нужен для cache-keys в useQuery, отдельного эндпоинта не существует.
  void departmentId;
  return apiGet<Group[]>("/auth/v1/groups", {
    query: {
      limit: params.limit ?? null,
      offset: params.offset ?? null,
    },
  });
}

export function getGroup(groupId: string): Promise<Group> {
  return apiGet<Group>(`/auth/v1/groups/${groupId}`);
}

export function createGroup(req: GroupCreateRequest): Promise<Group> {
  return apiPost<Group>("/auth/v1/groups", req);
}

export function patchGroup(
  groupId: string,
  req: GroupPatchRequest,
): Promise<Group> {
  return apiPatch<Group>(`/auth/v1/groups/${groupId}`, req);
}

export function deleteGroup(groupId: string): Promise<void> {
  return apiDelete<void>(`/auth/v1/groups/${groupId}`);
}

// ---------------------------------------------------------------------------
// Members (users)
// ---------------------------------------------------------------------------

export function listGroupMembers(groupId: string): Promise<GroupMember[]> {
  return apiGet<GroupMember[]>(`/auth/v1/groups/${groupId}/members`);
}

export function addGroupMember(
  groupId: string,
  userId: string,
): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>(`/auth/v1/groups/${groupId}/members`, {
    user_id: userId,
  });
}

export function removeGroupMember(
  groupId: string,
  userId: string,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/groups/${groupId}/members/${userId}`,
  );
}

// ---------------------------------------------------------------------------
// Bot members
// ---------------------------------------------------------------------------

export function listGroupBots(groupId: string): Promise<BotMemberResponse[]> {
  return apiGet<BotMemberResponse[]>(`/auth/v1/groups/${groupId}/bots`);
}

export function addGroupBot(
  groupId: string,
  botId: string,
): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>(`/auth/v1/groups/${groupId}/bots`, {
    bot_id: botId,
  });
}

export function removeGroupBot(
  groupId: string,
  botId: string,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(`/auth/v1/groups/${groupId}/bots/${botId}`);
}

// ---------------------------------------------------------------------------
// Service access + roles
// ---------------------------------------------------------------------------

export function listGroupServices(
  groupId: string,
): Promise<GroupServiceAccessResponse[]> {
  return apiGet<GroupServiceAccessResponse[]>(
    `/auth/v1/groups/${groupId}/services`,
  );
}

export function addGroupService(
  groupId: string,
  serviceName: ServiceName,
): Promise<GroupServiceAccessResponse> {
  return apiPost<GroupServiceAccessResponse>(
    `/auth/v1/groups/${groupId}/services`,
    { service_name: serviceName },
  );
}

export function removeGroupService(
  groupId: string,
  serviceName: ServiceName,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/groups/${groupId}/services/${serviceName}`,
  );
}

export function listGroupRoles(
  groupId: string,
): Promise<GroupRoleAssignment[]> {
  return apiGet<GroupRoleAssignment[]>(`/auth/v1/groups/${groupId}/roles`);
}

export function assignGroupRoles(
  groupId: string,
  req: GroupRoleAssignRequest,
): Promise<GroupRoleAssignment> {
  return apiPost<GroupRoleAssignment>(
    `/auth/v1/groups/${groupId}/roles`,
    req,
  );
}

export function revokeGroupRoles(
  groupId: string,
  serviceName: ServiceName,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/groups/${groupId}/roles/${serviceName}`,
  );
}

// ---------------------------------------------------------------------------
// User-side shortcuts (live in users.py but read like group endpoints)
// ---------------------------------------------------------------------------

export async function listUserGroups(
  userId: string,
): Promise<UserGroupsResponse[]> {
  const raw = await apiGet<BackendUserGroup[]>(
    `/auth/v1/users/${userId}/groups`,
  );
  return raw.map((g) => ({
    group_id: g.group_id,
    group_name: g.group_name,
    joined_at: g.added_at,
  }));
}

export function addUserToGroup(
  userId: string,
  groupId: string,
): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>(`/auth/v1/users/${userId}/groups`, {
    group_id: groupId,
  });
}

export function removeUserFromGroup(
  userId: string,
  groupId: string,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/users/${userId}/groups/${groupId}`,
  );
}
