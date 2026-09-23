/**
 * NOPASSWD sudo для тестовых учёток (`has_sudo`) — per-department opt-in.
 *
 * Пока выключено (дефолт), sudo-аккаунты на серверах/ВМ отдела продолжают
 * спрашивать пароль на каждый sudo-вызов (обычная группа `sudo`). Включение
 * кладёт per-user NOPASSWD sudoers-правило новым provision/prepare-вызовам.
 *
 * Два входа на одну настройку — источник истины `server_service`:
 *   GET/PUT /api/server/v1/settings/account-nopasswd-sudo — self-service,
 *     всегда свой отдел caller'а (department_id не передаётся, резолвится
 *     backend'ом из identity). Гейтится department_admin своего отдела или
 *     ролью `server_service.admin` (`canManageHostServices`); account_admin
 *     сюда не попадает (нет department_id).
 *   GET/PUT /api/server/v1/settings/account-nopasswd-sudo/departments —
 *     оверсайт account_admin, любой отдел списком/батчем, тот же паттерн, что
 *     `/settings/acs/departments`.
 */

import { apiGet, apiPut } from "@/api/client";

/** Ответ self-service GET/PUT /settings/account-nopasswd-sudo. */
export interface AccountNopasswdSudoSettings {
  department_id: string;
  is_enabled: boolean;
  updated_at: string | null;
}

/** Один отдел из GET /settings/account-nopasswd-sudo/departments. */
export interface AccountNopasswdSudoDepartmentItem {
  department_id: string;
  is_enabled: boolean;
  updated_at: string | null;
  created_by: string | null;
}

/** Один апдейт в батче PUT /settings/account-nopasswd-sudo/departments. */
export interface AccountNopasswdSudoDepartmentUpdateItem {
  department_id: string;
  is_enabled: boolean;
}

const BASE = "/server/v1";

/** Прочитать текущий флаг своего отдела (self-service). */
export function getAccountNopasswdSudoSettings(): Promise<AccountNopasswdSudoSettings> {
  return apiGet<AccountNopasswdSudoSettings>(`${BASE}/settings/account-nopasswd-sudo`);
}

/** Включить/выключить флаг для своего отдела (self-service). */
export function updateAccountNopasswdSudoSettings(
  is_enabled: boolean,
): Promise<AccountNopasswdSudoSettings> {
  return apiPut<AccountNopasswdSudoSettings>(
    `${BASE}/settings/account-nopasswd-sudo`,
    { is_enabled },
  );
}

/** Прочитать список отделов с текущим флагом (оверсайт account_admin). */
export async function getAccountNopasswdSudoDepartments(): Promise<
  AccountNopasswdSudoDepartmentItem[]
> {
  const res = await apiGet<{ items: AccountNopasswdSudoDepartmentItem[] }>(
    `${BASE}/settings/account-nopasswd-sudo/departments`,
  );
  return res.items;
}

/** Переключить флаг(и) отдела(ов) за один запрос (оверсайт account_admin). */
export async function updateAccountNopasswdSudoDepartments(
  items: AccountNopasswdSudoDepartmentUpdateItem[],
): Promise<AccountNopasswdSudoDepartmentItem[]> {
  const res = await apiPut<{ items: AccountNopasswdSudoDepartmentItem[] }>(
    `${BASE}/settings/account-nopasswd-sudo/departments`,
    { items },
  );
  return res.items;
}
