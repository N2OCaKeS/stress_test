/**
 * Статус ожидания внешних сервисов (preflight) — `GET /preflight/status`
 * `testing_service`.
 *
 * `waiting` — хоть один тест отдела пользователя стоит перед запуском и ждёт
 * недоступные внешние сервисы: левая панель показывает «тестирование
 * приостановлено».
 */

import { apiGet } from "@/api/client";
import type { PreflightStatus } from "@/api/testing/types";

export function getPreflightStatus(): Promise<PreflightStatus> {
  return apiGet<PreflightStatus>("/testing/v1/preflight/status");
}
