/**
 * Тонкие обёртки над `testing_service` `/department-test-settings/*` (§2.4
 * плана миграции). GET открыт любому аутентифицированному актору и никогда
 * не отдаёт 404 — отсутствие строки в БД означает дефолты (`retry_enabled:
 * true`, `test_username: "u"`). PUT (upsert) — под матрицей
 * `(department_test_settings, *, update)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/department_test_settings.py`.
 */

import { apiGet, apiPut } from "@/api/client";
import type {
  DepartmentTestSettings,
  DepartmentTestSettingsUpdateRequest,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** `GET /department-test-settings/{department_id}` — эффективные настройки (дефолты, если строки нет). */
export function getDepartmentTestSettings(
  departmentId: string,
): Promise<DepartmentTestSettings> {
  return apiGet<DepartmentTestSettings>(
    `${BASE}/department-test-settings/${departmentId}`,
  );
}

/**
 * `PUT /department-test-settings/{department_id}` — upsert, заданные поля
 * заменяют текущее значение (или дефолт при первом вызове). Доступ:
 * `(department_test_settings, *, update)`.
 */
export function upsertDepartmentTestSettings(
  departmentId: string,
  body: DepartmentTestSettingsUpdateRequest,
): Promise<DepartmentTestSettings> {
  return apiPut<DepartmentTestSettings>(
    `${BASE}/department-test-settings/${departmentId}`,
    body,
  );
}
