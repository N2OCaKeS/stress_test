/**
 * Тонкие обёртки над `testing_service` `/zephyr-status-mappings/*`.
 *
 * Какой статус тест-кейса в Zephyr считается пройденным, проваленным или
 * незавершённым (ждать дальше). У отдела без своих строк действует набор по
 * умолчанию (`is_default: true`). Чтение — свой отдел, запись — admin
 * testing_service или department_admin (`department_test_settings:update`).
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/zephyr_status_mappings.py`.
 */

import { apiDelete, apiGet, apiPut } from "@/api/client";
import type { ZephyrStatusMapping, ZephyrStatusMappingItem } from "@/api/testing/types";

const BASE = "/testing/v1/zephyr-status-mappings";

/** `GET` — действующий маппинг отдела (свой или по умолчанию). */
export function getZephyrStatusMapping(departmentId: string): Promise<ZephyrStatusMapping> {
  return apiGet<ZephyrStatusMapping>(`${BASE}/${departmentId}`);
}

/** `PUT` — заменить набор отдела целиком. */
export function putZephyrStatusMapping(
  departmentId: string,
  items: ZephyrStatusMappingItem[],
): Promise<ZephyrStatusMapping> {
  return apiPut<ZephyrStatusMapping>(`${BASE}/${departmentId}`, { items });
}

/** `DELETE` — удалить строки отдела, вернуться к набору по умолчанию. */
export function resetZephyrStatusMapping(departmentId: string): Promise<ZephyrStatusMapping> {
  return apiDelete<ZephyrStatusMapping>(`${BASE}/${departmentId}`);
}
