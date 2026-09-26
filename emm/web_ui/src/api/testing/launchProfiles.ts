/**
 * Обёртки над `testing_service` `/launch-profiles/*`.
 *
 * Профиль запуска: текст starter.sh, клонирование, пути на стенде, команды
 * запуска и остановки, pty, testenv. Правка содержимого — новая версия.
 * Чтение — свой отдел (видны профили отдела и общие); запись — admin
 * testing_service или department_admin, общий профиль — только admin.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/launch_profiles.py`.
 */

import { apiGet, apiPatch, apiPost } from "@/api/client";
import type { LaunchProfile, LaunchProfileVersion, LaunchProfileVersionInput } from "@/api/testing/types";

const BASE = "/testing/v1/launch-profiles";

export function listLaunchProfiles(departmentId: string): Promise<{ items: LaunchProfile[] }> {
  return apiGet(`${BASE}?department_id=${encodeURIComponent(departmentId)}`);
}

export function createLaunchProfile(body: {
  department_id: string | null;
  name: string;
  is_default?: boolean;
  version: LaunchProfileVersionInput;
}): Promise<LaunchProfile> {
  return apiPost(BASE, body);
}

export function updateLaunchProfile(id: string, body: { name?: string; is_default?: boolean }): Promise<LaunchProfile> {
  return apiPatch(`${BASE}/${encodeURIComponent(id)}`, body);
}

export function listLaunchProfileVersions(id: string): Promise<LaunchProfileVersion[]> {
  return apiGet(`${BASE}/${encodeURIComponent(id)}/versions`);
}

export function createLaunchProfileVersion(id: string, body: LaunchProfileVersionInput): Promise<LaunchProfileVersion> {
  return apiPost(`${BASE}/${encodeURIComponent(id)}/versions`, body);
}
