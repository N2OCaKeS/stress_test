/**
 * Тонкие обёртки над `testing_service` `/statistics/*` (§2.7, §9.3 плана
 * миграции — фоновый пересчёт статистики через внешний сервис, ветка
 * `statistics` того же монорепо).
 *
 * `getStatisticsSettings`/`getStatisticsStatus`/`getStatisticsCategories`
 * открыты любому аутентифицированному актору. `updateStatisticsSettings`/
 * `triggerStatisticsRecalc` и запись справочника семейств (`create`/`update`/
 * `deleteStatisticsCategory`) — под матрицей `(statistics_settings, *, update)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/statistics.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/api/client";
import type {
  StatisticsCategoriesResponse,
  StatisticsCategory,
  StatisticsCategoryCreateRequest,
  StatisticsCategoryUpdateRequest,
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
 * `GET /statistics/categories` — справочник семейств тестов, которые можно
 * пересчитать по отдельности (данные в БД, D18/). По умолчанию только
 * включённые — для модалки/кнопок; `includeDisabled` — весь справочник для
 * страницы настроек.
 */
export async function getStatisticsCategories(
  opts: { includeDisabled?: boolean } = {},
): Promise<StatisticsCategory[]> {
  const body = await apiGet<StatisticsCategoriesResponse>(
    `${BASE}/categories`,
    opts.includeDisabled ? { query: { include_disabled: true } } : undefined,
  );
  return body.items ?? [];
}

/** `POST /statistics/categories` — завести семейство. UNIQUE(key) → 409. */
export function createStatisticsCategory(
  body: StatisticsCategoryCreateRequest,
): Promise<StatisticsCategory> {
  return apiPost<StatisticsCategory>(`${BASE}/categories`, body);
}

/** `PATCH /statistics/categories/{id}` — частичное обновление (без `key`). */
export function updateStatisticsCategory(
  id: string,
  body: StatisticsCategoryUpdateRequest,
): Promise<StatisticsCategory> {
  return apiPatch<StatisticsCategory>(`${BASE}/categories/${encodeURIComponent(id)}`, body);
}

/** `DELETE /statistics/categories/{id}` — удалить семейство из справочника. */
export function deleteStatisticsCategory(id: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`${BASE}/categories/${encodeURIComponent(id)}`);
}

/**
 * `POST /statistics/recalculate` — ручной триггер для одиночных тестов.
 * Не блокирует до завершения пересчёта — отвечает сразу после постановки в
 * фон, актуальный статус смотреть через `getStatisticsStatus`. Без `category`/
 * `categories` пересчитывается всё, с ними — выбранные семейства по очереди.
 */
export function triggerStatisticsRecalc(
  body: StatisticsRecalcTriggerRequest = {},
): Promise<StatisticsRecalcStatus> {
  return apiPost<StatisticsRecalcStatus>(`${BASE}/recalculate`, body);
}
