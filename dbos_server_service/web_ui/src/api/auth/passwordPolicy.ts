/**
 * Настраиваемая парольная политика логина (`auth_service`).
 *
 * Минимальная длина + обязательность буквы/цифры для пользовательских паролей.
 * Хранится singleton-строкой в БД, правит `account_admin`. На первом запуске
 * сервиса сидируется из env (`AUTH_PASSWORD_POLICY_*`), дальше — из БД.
 * `INITIAL_ADMIN_PASSWORD` этой политике не подчиняется (жёсткий guard = 12).
 *
 *   GET  /api/auth/v1/admin/password-policy  — текущая политика (account_admin)
 *   PUT  /api/auth/v1/admin/password-policy  — обновить (account_admin)
 *   GET  /api/auth/v1/password-policy         — публично (для форм ввода пароля)
 */

import { apiGet, apiPut } from "@/api/client";

/** Границы настраиваемой минимальной длины (совпадают с backend'ом). */
export const MIN_CONFIGURABLE_LENGTH = 1;
export const MAX_CONFIGURABLE_LENGTH = 128;

/** Текущая политика (ответ GET). */
export interface PasswordPolicy {
  min_length: number;
  require_letter: boolean;
  require_digit: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
}

/** Тело PUT — частичное обновление. */
export type PasswordPolicyUpdate = Partial<
  Pick<PasswordPolicy, "min_length" | "require_letter" | "require_digit">
>;

const BASE = "/auth/v1";

/** Текущая политика логина (account_admin). 403 — нет роли. */
export function getPasswordPolicy(): Promise<PasswordPolicy> {
  return apiGet<PasswordPolicy>(`${BASE}/admin/password-policy`);
}

/** Обновить политику логина (частичная замена; account_admin). */
export function putPasswordPolicy(
  body: PasswordPolicyUpdate,
): Promise<PasswordPolicy> {
  return apiPut<PasswordPolicy>(`${BASE}/admin/password-policy`, body);
}

/** Публичная текущая политика — для форм ввода пароля (без авторизации). */
export function getPublicPasswordPolicy(): Promise<PasswordPolicy> {
  return apiGet<PasswordPolicy>(`${BASE}/password-policy`);
}
