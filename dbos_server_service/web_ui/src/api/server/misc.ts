/**
 * Thin wrappers для разрозненных endpoint'ов `server_service`:
 * `installed-packages` (live SSH-проба), `users/inventory` (snapshot OS-юзеров)
 * и `tasks/{id}/cancel` (отмена worker-task'и).
 *
 * Все три — независимые мелкие маршруты, под отдельный файл (servers/ipmi/
 * accounts/osVersions/permissions) не подходят. Источник истины:
 *   server_service/src/api/v1/endpoints/installed_packages.py
 *   server_service/src/api/v1/endpoints/inventory.py
 *   server_service/src/api/v1/endpoints/tasks.py
 */

import { apiPost } from "@/api/client";
import type {
  InstalledPackagesRequest,
  InstalledPackagesResult,
  TaskCancelRequest,
  TaskCancelResult,
  UsersInventoryResult,
} from "@/api/server/types";

// ── installed-packages ──────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/servers/{server_id}/installed-packages` — диспатч
 * SSH-пробы установленных пакетов через worker.
 *
 * Backend возвращает `task_id` сразу (HTTP 202): фактический сбор пакетов
 * (`dpkg-query` / `rpm -qa` по shell-glob'у) идёт асинхронно в worker'е.
 * UI получает результат, опросив task-row отдельным запросом.
 *
 * `pattern` — shell glob (`htop`, `linux-image*`, `*-dev`), по умолчанию `*`.
 *
 * Внимание: на стандартной Astra-коробке dpkg-список — порядка 2-3 тысяч строк,
 * сам worker применяет cap в 10 000 строк. Сама HTTP-проба быстрая (диспатч
 * в taskiq), но фоновая task может выполняться **десятки секунд** —
 * pollin'ом таски с разумным таймаутом (≥30 секунд) не злоупотребляй.
 */
export function installedPackagesProbe(
  serverId: string,
  body?: InstalledPackagesRequest,
): Promise<InstalledPackagesResult> {
  const query = body?.pattern ? { pattern: body.pattern } : {};
  return apiPost<InstalledPackagesResult>(
    `/server/v1/servers/${serverId}/installed-packages`,
    undefined,
    { query },
  );
}

// ── users/inventory ─────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/servers/{server_id}/users/inventory` — диспатч
 * snapshot'а OS-юзеров с сервера через SSH.
 *
 * Backend возвращает `task_id` сразу (HTTP 202). Worker заходит по SSH (под
 * `management_user`'ом, если сервер `is_managed`, иначе под дефолтным
 * аккаунтом сессии), читает `getent passwd` / группы / sudoers, POST'ит
 * результат в `/internal/servers/{id}/users/inventory`, и server_service
 * reconcile'ит снимок с `server_accounts`.
 *
 * Доступ: `(server, inventory_trigger)`. Cross-dept → 404.
 */
export function usersInventory(serverId: string): Promise<UsersInventoryResult> {
  return apiPost<UsersInventoryResult>(
    `/server/v1/servers/${serverId}/users/inventory`,
  );
}

// ── tasks/cancel ────────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/tasks/{task_id}/cancel` — отмена pending/running
 * worker-task'и.
 *
 * Body опционален: `{reason: string}` пишется в `tasks.cancel_reason` и в
 * audit `task.cancelled`. Pending — отмена сразу через CAS на mark_running;
 * running — graceful (текущий stage доживает, следующий не стартует).
 * Force-kill процесса нет.
 *
 * Доступ: `(task, cancel)` — по-дефолту только `admin`. Системные task'и
 * (heartbeat/sweep/cleanup) — только платформенный `account_admin`.
 * Терминальный статус (succeeded/failed/cancelled) → 409 `TASK_NOT_CANCELLABLE`.
 */
export function cancelTask(
  taskId: string,
  body?: TaskCancelRequest,
): Promise<TaskCancelResult> {
  return apiPost<TaskCancelResult>(
    `/server/v1/tasks/${taskId}/cancel`,
    body ?? {},
  );
}
