/**
 * Настройки доступа к ACS — внешнему сервису снимков дисков физических
 * серверов (обёртка над Clonezilla).
 *
 * Платформенный singleton (`enabled`/`acs_url`/пароль) + per-department
 * opt-in таблица поверх обычной action-матрицы. Пароль clonezilla-сервера
 * write-only — GET отдаёт только факт `password_is_set`, значение никогда
 * не возвращается.
 *
 * Источник истины — `server_service` endpoint'ы:
 *   GET  /api/server/v1/settings/acs
 *   PUT  /api/server/v1/settings/acs
 *   GET  /api/server/v1/settings/acs/departments
 *   PUT  /api/server/v1/settings/acs/departments
 * Гейтится account_admin; остальным backend ответит 403.
 *
 * `/settings/acs/departments` не несёт имён отделов — server_service своей
 * таблицы departments не хранит. Имена подтягиваются на фронте отдельно
 * через `listDepartments()` (`@/api/auth/departments`) и сшиваются по
 * `department_id`.
 */

import { apiGet, apiPut } from "@/api/client";

/** Текущие настройки ACS (ответ GET /settings/acs). */
export interface AcsSettings {
  /** Общий кил-свитч снимков ACS на всю платформу. */
  enabled: boolean;
  /** Базовый URL ACS. */
  acs_url: string | null;
  /** Задан ли пароль clonezilla-сервера. Само значение не отдаётся. */
  password_is_set: boolean;
}

/** Тело PUT /settings/acs — частичное обновление. */
export interface AcsSettingsUpdate {
  enabled?: boolean;
  acs_url?: string;
  /** Новый пароль, plaintext. Пусто/не передано — не менять текущий. */
  acs_password?: string;
  /** Явно стереть сохранённый пароль (игнорируется вместе с acs_password). */
  clear_password?: boolean;
}

/** Один отдел из GET /settings/acs/departments — без имени, только флаг. */
export interface AcsDepartmentAccessItem {
  department_id: string;
  is_enabled: boolean;
  updated_at: string | null;
  created_by: string | null;
}

/** Один апдейт в батче PUT /settings/acs/departments. */
export interface AcsDepartmentAccessUpdateItem {
  department_id: string;
  is_enabled: boolean;
}

const BASE = "/server/v1";

/** Прочитать текущие настройки ACS. 403 — нет account_admin. */
export function getAcsSettings(): Promise<AcsSettings> {
  return apiGet<AcsSettings>(`${BASE}/settings/acs`);
}

/** Обновить настройки ACS (частичная замена). */
export function updateAcsSettings(
  body: AcsSettingsUpdate,
): Promise<AcsSettings> {
  return apiPut<AcsSettings>(`${BASE}/settings/acs`, body);
}

/** Прочитать список отделов с текущим флагом доступа к снимкам ACS. */
export async function getAcsDepartmentAccess(): Promise<
  AcsDepartmentAccessItem[]
> {
  const res = await apiGet<{ items: AcsDepartmentAccessItem[] }>(
    `${BASE}/settings/acs/departments`,
  );
  return res.items;
}

/** Переключить флаг(и) доступа отдела(ов) к снимкам ACS за один запрос. */
export async function updateAcsDepartmentAccess(
  items: AcsDepartmentAccessUpdateItem[],
): Promise<AcsDepartmentAccessItem[]> {
  const res = await apiPut<{ items: AcsDepartmentAccessItem[] }>(
    `${BASE}/settings/acs/departments`,
    { items },
  );
  return res.items;
}
