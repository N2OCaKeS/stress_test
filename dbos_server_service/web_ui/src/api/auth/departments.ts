/**
 * Thin wrappers for `/departments/*` endpoints in auth_service.
 */

import { ApiError, apiDelete, apiGet, apiPost } from "@/api/client";
import type { Department, DepartmentCreateRequest } from "@/api/auth/types";

export interface ServiceAccessResponse {
  department_id: string;
  service_name: string;
  enabled: boolean;
  granted_at?: string | null;
}

// Backend `DepartmentResponse` отдаёт `department_id`; UI-тип Department хранит
// поле как `id`. Нормализуем на чтении, чтобы не править ~10 точек потребления.
interface BackendDepartment {
  department_id?: string;
  id?: string;
  name: string;
  display_name: string;
  is_active?: boolean;
  created_at: string;
  updated_at?: string;
}

function normalizeDepartment(d: BackendDepartment): Department {
  return {
    id: d.id ?? d.department_id ?? "",
    name: d.name,
    display_name: d.display_name,
    created_at: d.created_at,
    updated_at: d.updated_at,
  };
}

export async function listDepartments(): Promise<Department[]> {
  const raw = await apiGet<BackendDepartment[]>("/auth/v1/departments");
  return raw.map(normalizeDepartment);
}

export async function createDepartment(
  body: DepartmentCreateRequest,
): Promise<Department> {
  const raw = await apiPost<BackendDepartment>("/auth/v1/departments", body);
  return normalizeDepartment(raw);
}

// Backend endpoint для update отдела (`PATCH /departments/{id}` или
// `PUT /departments/{id}`) отсутствует в auth_service — в
// `src/api/v1/endpoints/departments.py` есть только GET-list, POST-create,
// POST/DELETE для services-связок и DELETE отдела. Поэтому изменить
// `display_name` через API сейчас нельзя; в UI edit-форма выводит ошибку.
// TODO(auth_service): добавить endpoint update отдела (минимум display_name).
export function updateDepartment(
  _departmentId: string,
  _body: Partial<DepartmentCreateRequest>,
): Promise<Department> {
  return Promise.reject(
    new ApiError(501, {
      error: "not_implemented",
      error_code: "NOT_IMPLEMENTED",
      message:
        "update department endpoint отсутствует в auth_service; пересоздай отдел через delete+create",
    }),
  );
}

export function deleteDepartment(
  departmentId: string,
  body: { reason: string },
): Promise<void> {
  return apiDelete<void>(`/auth/v1/departments/${departmentId}`, body);
}

// ── Department ↔ services access ────────────────────────────────────────────
//
// Backend (auth_service) предоставляет только POST/DELETE — GET-листинга нет
// (нельзя узнать, какие сервисы уже привязаны к отделу). UI компенсирует это
// «опциональным» отображением: имя пишется руками или выбирается из общего
// списка сервисов, а статус показывается после первого взаимодействия.
export function listDepartmentServices(
  departmentId: string,
): Promise<string[]> {
  return apiGet<string[]>(
    `/auth/v1/departments/${encodeURIComponent(departmentId)}/services`,
  );
}

export function grantServiceAccess(
  departmentId: string,
  serviceName: string,
): Promise<ServiceAccessResponse> {
  return apiPost<ServiceAccessResponse>(
    `/auth/v1/departments/${encodeURIComponent(departmentId)}/services`,
    { service_name: serviceName },
  );
}

export function revokeServiceAccess(
  departmentId: string,
  serviceName: string,
): Promise<void> {
  return apiDelete<void>(
    `/auth/v1/departments/${encodeURIComponent(departmentId)}/services/${encodeURIComponent(serviceName)}`,
  );
}
