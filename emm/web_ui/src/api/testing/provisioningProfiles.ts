/**
 * Обёртки над `testing_service` `/provisioning-profiles/*`.
 *
 * Профиль подготовки стенда: какие упавшие юниты допустимы после
 * перезагрузки, сколько раз перезагружать при `degraded`, PAM-правка.
 * Чтение — свой отдел (видны профили отдела и общие); запись — admin
 * testing_service или department_admin, общий профиль — только admin.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/provisioning_profiles.py`.
 */

import { apiGet, apiPatch, apiPost } from "@/api/client";
import type { ProvisioningProfile, ProvisioningProfileFields } from "@/api/testing/types";

const BASE = "/testing/v1/provisioning-profiles";

export function listProvisioningProfiles(departmentId: string): Promise<{ items: ProvisioningProfile[] }> {
  return apiGet(`${BASE}?department_id=${encodeURIComponent(departmentId)}`);
}

export function createProvisioningProfile(
  body: ProvisioningProfileFields & { department_id: string | null; name: string; is_default?: boolean },
): Promise<ProvisioningProfile> {
  return apiPost(BASE, body);
}

export function updateProvisioningProfile(
  id: string,
  body: Partial<ProvisioningProfileFields> & { name?: string; is_default?: boolean },
): Promise<ProvisioningProfile> {
  return apiPatch(`${BASE}/${encodeURIComponent(id)}`, body);
}
