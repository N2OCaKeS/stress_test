/**
 * Настройки проб статуса `server_service`.
 *
 * Пробы статуса (reachability = ping+ssh, power = ipmi/domstate) снимает
 * server_worker. Их частота и вкл/выкл хранятся в БД (а не в env воркера),
 * чтобы работать одинаково в docker и k8s. Воркер читает настройки через
 * internal-эндпоинт; редактирует их account_admin.
 *
 * Источник истины — `server_service` endpoint'ы:
 *   GET  /api/server/v1/settings/probes
 *   PUT  /api/server/v1/settings/probes
 * Гейтится account_admin; остальным backend ответит 403.
 */

import { apiGet, apiPut } from "@/api/client";

/** Нижние границы интервалов (совпадают с backend'ом). */
export const MIN_REACHABILITY_INTERVAL_SECONDS = 15;
export const MIN_POWER_INTERVAL_SECONDS = 60;

/** Текущие настройки проб (ответ GET). */
export interface ProbeSettings {
  /** Интервал пробы доступности (ping+ssh), секунды. */
  reachability_probe_interval_seconds: number;
  /** Интервал пробы питания (ipmi/domstate), секунды. */
  power_probe_interval_seconds: number;
  /** Включена ли проба доступности. */
  reachability_probe_enabled: boolean;
  /** Включена ли проба питания. */
  power_probe_enabled: boolean;
}

/** Тело PUT — частичное обновление настроек проб. */
export type ProbeSettingsUpdate = Partial<ProbeSettings>;

const BASE = "/server/v1";

/** Прочитать текущие настройки проб. 403 — нет account_admin. */
export function getProbeSettings(): Promise<ProbeSettings> {
  return apiGet<ProbeSettings>(`${BASE}/settings/probes`);
}

/** Обновить настройки проб (частичная замена). */
export function putProbeSettings(
  body: ProbeSettingsUpdate,
): Promise<ProbeSettings> {
  return apiPut<ProbeSettings>(`${BASE}/settings/probes`, body);
}
