/**
 * Тонкие обёртки над `testing_service` `/department-test-account/*`.
 *
 * Тестовая учётка отдела: логин, пароль и SSH-ключ пользователя исполнения
 * теста. Хранится в secret_service; backend отдаёт только логин, публичный
 * ключ и признак «пароль задан». Доступ — admin testing_service или
 * department_admin своего отдела (`(department_test_account, view|update)`).
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/department_test_account.py`.
 */

import { apiGet, apiPut } from "@/api/client";
import type {
  DepartmentTestAccount,
  DepartmentTestAccountUpdateRequest,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** `GET /department-test-account/{department_id}` — карточка учётки без секретов. */
export function getDepartmentTestAccount(
  departmentId: string,
): Promise<DepartmentTestAccount> {
  return apiGet<DepartmentTestAccount>(
    `${BASE}/department-test-account/${departmentId}`,
  );
}

/**
 * `PUT /department-test-account/{department_id}` — первая настройка (пароль
 * обязателен, SSH-пара генерируется сервисом) или смена логина/пароля/ключа.
 * Действует со следующей подготовки стенда.
 */
export function upsertDepartmentTestAccount(
  departmentId: string,
  body: DepartmentTestAccountUpdateRequest,
): Promise<DepartmentTestAccount> {
  return apiPut<DepartmentTestAccount>(
    `${BASE}/department-test-account/${departmentId}`,
    body,
  );
}
