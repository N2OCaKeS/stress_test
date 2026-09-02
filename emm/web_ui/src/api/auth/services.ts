/**
 * Platform services catalog — `/api/auth/v1/services/*`.
 *
 * Three endpoints:
 *   - GET    /services                 — list
 *   - POST   /services                 — register a new service
 *   - DELETE /services/{service_name}  — remove (cascades dept access + roles)
 *
 * `account_admin` only. Used by the security UI to render the universe of
 * services the platform knows about; the same data underpins the per-dept
 * `allowed_services` checks elsewhere.
 */

import { apiDelete, apiGet, apiPost } from "@/api/client";
import type { Service, ServiceCreateRequest } from "@/api/auth/types";

const BASE = "/auth/v1/services";

export function listServices(): Promise<Service[]> {
  return apiGet<Service[]>(BASE);
}

export function createService(req: ServiceCreateRequest): Promise<Service> {
  return apiPost<Service>(BASE, req);
}

export function deleteService(serviceName: string): Promise<void> {
  return apiDelete<void>(`${BASE}/${encodeURIComponent(serviceName)}`);
}
