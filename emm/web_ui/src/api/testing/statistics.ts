/**
 * Тонкие обёртки над `testing_service` `/statistics/*` (§2.7, §9.3 плана
 * миграции — фоновый пересчёт статистики через внешний сервис, ветка
 * `statistics` того же монорепо).
 *
 * `getStatisticsSettings`/`getStatisticsStatus` открыты любому
 * аутентифицированному актору. `updateStatisticsSettings`/
 * `triggerStatisticsRecalc` — под матрицей `(statistics_settings, *, update)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/statistics.py`.
 */

import { apiGet, apiPost, apiPut } from "@/api/client";
import type {
  StatisticsRecalcStatus,
  StatisticsRecalcTriggerRequest,
  StatisticsSettings,
  StatisticsSettingsUpdateRequest,
} from "@/api/testing/types";

const BASE = "/testing/v1/statistics";

/** `GET /statistics/settings` — текущие настройки (дефолты, если строка не создана). */
export function getStatisticsSettings(): Promise<StatisticsSettings> {
  return apiGet<StatisticsSettings>(`${BASE}/settings`);
}

/** `PUT /statistics/settings` — частичное обновление. Доступ: `(statistics_settings, *, update)`. */
export function updateStatisticsSettings(
  body: StatisticsSettingsUpdateRequest,
): Promise<StatisticsSettings> {
  return apiPut<StatisticsSettings>(`${BASE}/settings`, body);
}

/** `GET /statistics/status` — индикатор фонового пересчёта для левой панели. */
export function getStatisticsStatus(): Promise<StatisticsRecalcStatus> {
  return apiGet<StatisticsRecalcStatus>(`${BASE}/status`);
}

/**
 * `POST /statistics/recalculate` — ручной триггер для одиночных тестов.
 * Не блокирует до завершения пересчёта — отвечает сразу после постановки в
 * фон, актуальный статус смотреть через `getStatisticsStatus`.
 */
export function triggerStatisticsRecalc(
  body: StatisticsRecalcTriggerRequest = {},
): Promise<StatisticsRecalcStatus> {
  return apiPost<StatisticsRecalcStatus>(`${BASE}/recalculate`, body);
}
