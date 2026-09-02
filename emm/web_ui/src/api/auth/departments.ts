/**
 * Thin wrappers for `/departments/*` endpoints in auth_service.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  Department,
  DepartmentCreateRequest,
  DepartmentUpdateRequest,
} from "@/api/auth/types";

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
  description?: string | null;
  is_active?: boolean;
  user_count?: number;
  created_at: string;
  updated_at?: string;
}

function normalizeDepartment(d: BackendDepartment): Department {
  return {
    id: d.id ?? d.department_id ?? "",
    name: d.name,
    description: d.description ?? null,
    user_count: d.user_count ?? 0,
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

// PATCH принимает только `name` и/или `description`. Пустое тело backend
// отбивает 422 `EMPTY_UPDATE`.
export async function updateDepartment(
  departmentId: string,
  body: DepartmentUpdateRequest,
): Promise<Department> {
  const raw = await apiPatch<BackendDepartment>(
    `/auth/v1/departments/${encodeURIComponent(departmentId)}`,
    body,
  );
  return normalizeDepartment(raw);
}

export function deleteDepartment(
  departmentId: string,
  body: { reason: string },
): Promise<void> {
  return apiDelete<void>(`/auth/v1/departments/${departmentId}`, body);
}

// ── Department ↔ services access ────────────────────────────────────────────
//
// Backend (auth_service) даёт GET (список привязанных сервисов) + POST/DELETE.
// `listDepartmentServices` возвращает массив `service_name` активных грантов.
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
