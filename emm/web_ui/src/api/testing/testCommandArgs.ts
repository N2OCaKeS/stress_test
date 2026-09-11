/**
 * Тонкие обёртки над `testing_service` `/test-definitions/{test_id}/args/*` —
 * слоты конструктора команды теста (§3.2-3.3 плана миграции). Слоты не несут
 * собственной защищаемой сущности верхнего уровня: read открыт любому
 * аутентифицированному актору, write проверяет `(test_definition, *, update)`
 * — редактирование команды это часть редактирования теста.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/test_command_args.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  TestCommandArg,
  TestCommandArgCreateRequest,
  TestCommandArgUpdateRequest,
  TestingOkResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** Заменить параметры теста копией параметров источника одной операцией. */
export function copyTestCommandArgs(testId: string, sourceTestId: string): Promise<TestCommandArg[]> {
  return apiPost<TestCommandArg[]>(`${BASE}/test-definitions/${testId}/args/copy-from`, {
    source_test_id: sourceTestId,
  });
}

/** `GET /test-definitions/{test_id}/args` — слоты теста по порядку `position`. */
export function listTestCommandArgs(testId: string): Promise<TestCommandArg[]> {
  return apiGet<TestCommandArg[]>(`${BASE}/test-definitions/${testId}/args`);
}

/**
 * `POST /test-definitions/{test_id}/args` — добавить слот (в конец списка,
 * либо на явную `position`). Доступ: `(test_definition, *, update)`.
 */
export function createTestCommandArg(
  testId: string,
  body: TestCommandArgCreateRequest,
): Promise<TestCommandArg> {
  return apiPost<TestCommandArg>(
    `${BASE}/test-definitions/${testId}/args`,
    body,
  );
}

/**
 * `PATCH /test-definitions/{test_id}/args/{arg_id}` — частичное обновление
 * слота (значение/тип/позиция). Доступ: `(test_definition, *, update)`.
 */
export function updateTestCommandArg(
  testId: string,
  argId: string,
  body: TestCommandArgUpdateRequest,
): Promise<TestCommandArg> {
  return apiPatch<TestCommandArg>(
    `${BASE}/test-definitions/${testId}/args/${argId}`,
    body,
  );
}

/**
 * `DELETE /test-definitions/{test_id}/args/{arg_id}` — убрать слот из команды.
 * Доступ: `(test_definition, *, update)`.
 */
export function deleteTestCommandArg(
  testId: string,
  argId: string,
): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(
    `${BASE}/test-definitions/${testId}/args/${argId}`,
  );
}
