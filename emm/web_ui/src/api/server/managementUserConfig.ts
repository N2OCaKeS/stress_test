/**
 * Конфиг управляющей (системной) учётки `server_service`.
 *
 * Управляющая учётка — это OS-пользователь, под которым сервис заходит на
 * подготовленные серверы. Конфиг хранит её `login` и per-режим настройки:
 * доп-группы и команды, выполняемые при создании учётки на боксе (например,
 * для Смоленска — выставление уровней целостности).
 *
 * Источник истины — `server_service` endpoint'ы:
 *   GET  /api/server/v1/management-user-config
 *   PUT  /api/server/v1/management-user-config
 * Гейтится account_admin; остальным backend ответит 403.
 */

import { apiGet, apiPut } from "@/api/client";

/** Идентификаторы режимов ОС (совпадают с backend'ом). */
export type ManagementUserMode =
  | "astra_orel"
  | "astra_smolensk"
  | "astra_voronezh"
  | "other_os";

/** Настройки управляющей учётки для одного режима ОС. */
export interface ManagementUserModeConfig {
  /** Доп-группы, в которые добавляется учётка при создании. */
  groups: string[];
  /** Команды, выполняемые при создании учётки на сервере. */
  extra_create_commands: string[];
}

/** Полный конфиг управляющей учётки (ответ GET). */
export interface ManagementUserConfig {
  login: string;
  modes: Record<ManagementUserMode, ManagementUserModeConfig>;
  /** true, если имя учётки уже менялось и идёт/требуется cutover. */
  login_changed?: boolean;
  /** Прежнее имя учётки до последней смены. */
  previous_login?: string;
}

/** Тело PUT — частичная замена `login` и/или `modes`. */
export interface ManagementUserConfigUpdate {
  login?: string;
  modes?: Record<ManagementUserMode, ManagementUserModeConfig>;
}

const BASE = "/server/v1";

/**
 * Прочитать текущий конфиг управляющей учётки. 403 — нет account_admin.
 */
export function getManagementUserConfig(): Promise<ManagementUserConfig> {
  return apiGet<ManagementUserConfig>(`${BASE}/management-user-config`);
}

/**
 * Заменить конфиг управляющей учётки. Смена `login` запускает на backend'е
 * cutover управляющей учётки на всех подготовленных серверах.
 */
export function putManagementUserConfig(
  body: ManagementUserConfigUpdate,
): Promise<ManagementUserConfig> {
  return apiPut<ManagementUserConfig>(`${BASE}/management-user-config`, body);
}
