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

import { apiGet, apiPost } from "@/api/client";
import { listWithTotal, type PaginatedList } from "@/api/auth/users";
import type {
  BulkInstalledPackagesRequest,
  BulkInstalledPackagesResponse,
  InstalledPackagesRequest,
  InstalledPackagesResult,
  ListTasksQuery,
  PackageHistoryEntry,
  PackagesBulkActionRequest,
  PackagesBulkActionResponse,
  TaskCancelRequest,
  TaskCancelResult,
  TaskRead,
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
 * Worker заходит по SSH под управляющим пользователем (`management_user`) —
 * сервер обязан быть подготовлен (`is_managed`, через prepare), иначе backend
 * вернёт 409 `PREPARE_REQUIRED`.
 *
 * Внимание: на стандартной Astra-коробке dpkg-список — порядка 2-3 тысяч строк,
 * сам worker применяет cap в 10 000 строк. Сама HTTP-проба быстрая (диспатч
 * в taskiq), но фоновая task может выполняться **десятки секунд** —
 * pollin'ом таски с разумным таймаутом (≥30 секунд) не злоупотребляй.
 */
export function installedPackagesProbe(
  serverId: string,
  body?: InstalledPackagesRequest,
  _opts: { account_id?: string } = {},
): Promise<InstalledPackagesResult> {
  const query: Record<string, string | undefined> = {};
  if (body?.pattern) query.pattern = body.pattern;
  return apiPost<InstalledPackagesResult>(
    `/server/v1/servers/${serverId}/installed-packages`,
    undefined,
    { query },
  );
}

/**
 * `POST /api/server/v1/servers/installed-packages/bulk` — массовый запрос
 * установленных пакетов по набору серверов.
 *
 * Backend сразу (HTTP 202) возвращает per-server исходы: для подготовленных
 * серверов ставит probe-задачу и отдаёт `task_id` (`status: ok`), остальным —
 * статус-причину (`prepare_required` / `decommissioned` / `not_found` /
 * `auth_failed`). Список пакетов в `results[].packages` может прийти пустым —
 * тогда его добирают поллингом `GET /tasks/{task_id}` (как в single-варианте).
 *
 * Фильтр пакетов поддерживает несколько glob'ов сразу через `patterns`
 * (`["ssh*", "*libs*"]`); одиночный `pattern` оставлен для совместимости.
 */
export function installedPackagesBulk(
  body: BulkInstalledPackagesRequest,
): Promise<BulkInstalledPackagesResponse> {
  return apiPost<BulkInstalledPackagesResponse>(
    "/server/v1/servers/installed-packages/bulk",
    body,
  );
}

/**
 * `POST /api/server/v1/servers/packages/bulk-action` — массовая установка /
 * удаление / обновление пакетов на наборе серверов.
 *
 * `packages` обязателен для `install`/`remove`; для `update` пустой список
 * означает upgrade всех пакетов. Backend сразу (HTTP 202) возвращает per-server
 * исходы: подготовленным серверам ставит задачу и отдаёт `task_id`
 * (`status: ok`), остальным — статус-причину (`prepare_required` / `reserved` /
 * `decommissioned` / `not_found`). Итог каждой задачи добирают поллингом
 * `GET /tasks/{task_id}`.
 *
 * Доступ: action `manage_packages` (server.operator+ / dep_admin своего отдела).
 * Backend перепроверит права и бронь — клиентский гейт прячет заведомо лишние
 * кнопки, но не заменяет серверную проверку.
 */
export function packagesBulkAction(
  body: PackagesBulkActionRequest,
): Promise<PackagesBulkActionResponse> {
  return apiPost<PackagesBulkActionResponse>(
    "/server/v1/servers/packages/bulk-action",
    body,
  );
}

// ── packages history ─────────────────────────────────────────────────────────

/** Параметры пагинации истории запросов пакетов. */
export interface PackageHistoryQuery {
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/server/v1/servers/{server_id}/packages/history` — страница прошлых
 * live-запросов пакетов этого сервера (DESC по времени постановки).
 *
 * Каждый POST `/installed-packages` оставляет такую запись — история даёт
 * оператору уже полученные результаты, не гоняя SSH-probe заново: в строке
 * лежат запрошенный паттерн и найденные пакеты прямо из `task.result`.
 * Backend отдаёт голый `PackageHistoryEntry[]` + `X-Total-Count` в заголовке —
 * протаскиваем оба через `listWithTotal` для пагинации «N из M». Доступ —
 * `(server, view)` + dept-isolation: всю историю по серверу видит любой, кто
 * видит сам сервер (не только свои запросы). Незавершённые (`queued`/`running`)
 * попадают в выдачу с пустым `packages`.
 */
export function getPackageHistory(
  serverId: string,
  query: PackageHistoryQuery = {},
): Promise<PaginatedList<PackageHistoryEntry>> {
  const { limit = 20, offset = 0 } = query;
  return listWithTotal<PackageHistoryEntry>(
    `/server/v1/servers/${serverId}/packages/history`,
    { limit, offset },
  );
}

// ── users/inventory ─────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/servers/{server_id}/users/inventory` — диспатч
 * snapshot'а OS-юзеров с сервера через SSH.
 *
 * Backend возвращает `task_id` сразу (HTTP 202). Worker заходит по SSH под
 * управляющим пользователем (`management_user`), читает `getent passwd` /
 * группы / sudoers, POST'ит результат в `/internal/servers/{id}/users/inventory`,
 * и server_service reconcile'ит снимок с `server_accounts`.
 *
 * Сервер обязан быть подготовлен (`is_managed`, через prepare), иначе backend
 * вернёт 409 `PREPARE_REQUIRED`.
 *
 * Доступ: `(server, inventory_trigger)`. Cross-dept → 404.
 */
export function usersInventory(
  serverId: string,
  _opts: { account_id?: string } = {},
): Promise<UsersInventoryResult> {
  return apiPost<UsersInventoryResult>(
    `/server/v1/servers/${serverId}/users/inventory`,
    undefined,
  );
}

// ── tasks list / detail ─────────────────────────────────────────────────────

/**
 * `GET /api/server/v1/tasks` — страница worker-task'ей с фильтрами.
 *
 * Backend отдаёт голый `TaskRead[]` плюс `X-Total-Count` в заголовке —
 * протаскиваем оба через `listWithTotal` (как list-эндпоинты auth_service),
 * чтобы UI мог показать «N из M» и баннер усечения.
 *
 * Скоуп выдачи определяет backend по роли: server.admin/operator/reader — по
 * матрице, dep_admin — задачи серверов своего отдела, account_admin/
 * logging_admin — 403 (server-зона им закрыта целиком).
 */
export function listTasks(
  query: ListTasksQuery = {},
): Promise<PaginatedList<TaskRead>> {
  const { status, kind, server_id, created_by, limit = 50, offset = 0 } = query;
  return listWithTotal<TaskRead>("/server/v1/tasks", {
    status: status || undefined,
    kind: kind || undefined,
    server_id: server_id || undefined,
    created_by: created_by || undefined,
    limit,
    offset,
  });
}

/**
 * `GET /api/server/v1/tasks/{task_id}` — полная карточка task'и с `result` и
 * `last_error`. 404 `TASK_NOT_FOUND`, если row нет или вне scope'а.
 */
export function getTask(taskId: string): Promise<TaskRead> {
  return apiGet<TaskRead>(`/server/v1/tasks/${taskId}`);
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
