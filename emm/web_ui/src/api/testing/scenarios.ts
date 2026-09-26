/**
 * Обёртки над `testing_service` `/scenarios/*` — многостендовые сценарии
 *. Сценарий передаётся целиком; запуска пока нет.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/scenarios.py`.
 */

import { apiDelete, apiGet, apiPost, apiPut } from "@/api/client";
import type { Scenario, ScenarioPreview, ScenarioRun, ScenarioSummary, ScenarioWrite } from "@/api/testing/types";

const BASE = "/testing/v1/scenarios";

export function listScenarios(departmentId?: string): Promise<{ items: ScenarioSummary[] }> {
  return apiGet(departmentId ? `${BASE}?department_id=${encodeURIComponent(departmentId)}` : BASE);
}

export function getScenario(id: string): Promise<Scenario> {
  return apiGet(`${BASE}/${encodeURIComponent(id)}`);
}

export function createScenario(body: ScenarioWrite): Promise<Scenario> {
  return apiPost(BASE, body);
}

export function updateScenario(id: string, body: ScenarioWrite): Promise<Scenario> {
  return apiPut(`${BASE}/${encodeURIComponent(id)}`, body);
}

export function deleteScenario(id: string): Promise<void> {
  return apiDelete(`${BASE}/${encodeURIComponent(id)}`);
}

export function previewScenario(
  id: string,
  body: { os_version_id: string; kernel: string; debug?: boolean },
): Promise<ScenarioPreview> {
  return apiPost(`${BASE}/${encodeURIComponent(id)}/preview`, body);
}

// ── запуск ────────────────────────────────────────────────────────

export function startScenarioRun(
  id: string,
  body: { os_version_id: string; kernel: string; mode?: string; debug?: boolean },
): Promise<ScenarioRun> {
  return apiPost(`${BASE}/${encodeURIComponent(id)}/runs`, body);
}

export function listScenarioRuns(id: string): Promise<{ items: ScenarioRun[] }> {
  return apiGet(`${BASE}/${encodeURIComponent(id)}/runs`);
}

export function stopScenarioRun(runId: string): Promise<ScenarioRun> {
  return apiPost(`/testing/v1/scenario-runs/${encodeURIComponent(runId)}/stop`);
}
