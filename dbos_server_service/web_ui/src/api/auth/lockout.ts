/**
 * Lockout-обёртки auth_service.
 *
 * Endpoints:
 *   GET  /api/auth/v1/users/locked?include_failing=
 *   POST /api/auth/v1/users/{id}/unlock
 *   POST /api/auth/v1/users/{id}/ban
 *   POST /api/auth/v1/users/{id}/unban
 *   GET  /api/auth/v1/admin/lockout-policy
 *   PUT  /api/auth/v1/admin/lockout-policy
 *
 * Гейт — account_admin (см. adminCatalog `services.security.lockout`).
 */

import { apiGet, apiPost, apiPut } from "@/api/client";
import type {
  BanRequest,
  LockedUser,
  LockoutPolicy,
  User,
} from "@/api/auth/types";

/**
 * Список залоченных учёток. С `includeFailing=true` добавляются записи, которые
 * ещё не залочены, но уже копят неудачные попытки — чтобы инцидент было видно
 * до того, как сработает порог.
 */
export function listLockedUsers(
  includeFailing = false,
): Promise<LockedUser[]> {
  return apiGet<LockedUser[]>("/auth/v1/users/locked", {
    query: { include_failing: includeFailing },
  });
}

/** Снять lockout — сбрасывает счётчик и `locked_until`. */
export function unlockUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unlock`, {});
}

/** Заблокировать (ban) учётку — отличается от temporary lockout: ban ручной. */
export function banUser(userId: string, body: BanRequest): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/ban`, body);
}

/** Снять ban. */
export function unbanUser(userId: string): Promise<User> {
  return apiPost<User>(`/auth/v1/users/${userId}/unban`, {});
}

export function getLockoutPolicy(): Promise<LockoutPolicy> {
  return apiGet<LockoutPolicy>("/auth/v1/admin/lockout-policy");
}

export function updateLockoutPolicy(
  body: LockoutPolicy,
): Promise<LockoutPolicy> {
  return apiPut<LockoutPolicy>("/auth/v1/admin/lockout-policy", body);
}
