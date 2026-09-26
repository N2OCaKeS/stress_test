/**
 * Обёртки над `testing_service` `/test-definitions/{test_id}/steps/*` —
 * шаги многоступенчатого теста. Доступ — как у слотов команды:
 * чтение — видимость теста, запись — `(test_definition, *, update)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/test_steps.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/api/client";
import type {
  TestStep,
  TestStepCreateRequest,
  TestStepUpdateRequest,
  TestingOkResponse,
} from "@/api/testing/types";

const base = (testId: string) => `/testing/v1/test-definitions/${encodeURIComponent(testId)}/steps`;

/** Шаги теста по порядку (у одношагового — один). */
export function listTestSteps(testId: string): Promise<TestStep[]> {
  return apiGet<TestStep[]>(base(testId));
}

export function createTestStep(testId: string, body: TestStepCreateRequest): Promise<TestStep> {
  return apiPost<TestStep>(base(testId), body);
}

export function updateTestStep(testId: string, stepId: string, body: TestStepUpdateRequest): Promise<TestStep> {
  return apiPatch<TestStep>(`${base(testId)}/${encodeURIComponent(stepId)}`, body);
}

/** Удалить шаг вместе со слотами; последний шаг сервер не удаляет (409 `TEST_STEP_LAST`). */
export function deleteTestStep(testId: string, stepId: string): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(`${base(testId)}/${encodeURIComponent(stepId)}`);
}

/** Переставить шаги: все шаги теста в новом порядке. */
export function reorderTestSteps(testId: string, stepIds: string[]): Promise<TestStep[]> {
  return apiPut<TestStep[]>(`${base(testId)}/order`, { step_ids: stepIds });
}
