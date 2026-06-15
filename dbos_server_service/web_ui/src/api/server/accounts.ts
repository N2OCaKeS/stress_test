/**
 * Thin wrappers for `server_service` `/server-accounts/*` endpoints + связанные
 * worker-dispatch'и (`provision` / `update_on_host` / `deprovision`).
 *
 * Источник истины — `server_service/src/api/v1/endpoints/server_accounts.py`
 * (CRUD + линковка + rotate_password) и `worker_dispatch.py` секция
 * `/server-accounts/{id}/{provision|update_on_host|deprovision}` (POST с
 * обязательным query `server_id`). Все wrappers — JSON-обёртки поверх
 * `apiGet/apiPost/apiPatch/apiDelete`; envelope ошибок и Bearer-Authorization
 * подкладывает общий `@/api/client`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  ServerAccount,
  ServerAccountCreateRequest,
  ServerAccountUpdateRequest,
} from "@/api/server/types";
import { toBase64 } from "@/lib/base64";

const BASE = "/server/v1";

// ---------------------------------------------------------------------------
// Account CRUD
// ---------------------------------------------------------------------------

/**
 * Параметры `GET /server-accounts`. Backend требует `server_id` (страница
 * аккаунтов привязанных к одному серверу). `scope` оставлен как опциональный
 * расширительный фильтр — пока backend его игнорирует, но wrapper уже
 * принимает (UI-фильтр «свой dept / cross-dept admin view»).
 */
export interface ListAccountsParams {
  /** Backend требует обязательно (`Query(...)`) — без него 422. */
  server_id: string;
  scope?: "department" | "all" | string;
  limit?: number;
  offset?: number;
  /** Включить cursor-envelope (`{items,next_cursor,has_more}`). */
  cursor?: boolean;
  /** Cursor предыдущей страницы для keyset-пагинации. */
  after?: string;
}

/**
 * Список аккаунтов сервера.
 *
 * Backend возвращает либо offset-envelope (`{items,total,limit,offset}`),
 * либо cursor-envelope (`{items,next_cursor,has_more}`) — определяется
 * флагами `cursor`/`after`. Wrapper типизирован union'ом, caller разбирает
 * по наличию полей.
 */
export function listAccounts(
  query: ListAccountsParams,
): Promise<
  OffsetPaginatedResponse<ServerAccount> | CursorPaginatedResponse<ServerAccount>
> {
  return apiGet<
    OffsetPaginatedResponse<ServerAccount> | CursorPaginatedResponse<ServerAccount>
  >(`${BASE}/server-accounts`, {
    query: {
      server_id: query.server_id,
      scope: query.scope,
      limit: query.limit,
      offset: query.offset,
      cursor: query.cursor,
      after: query.after,
    },
  });
}

/**
 * Вход `createAccount` с plaintext-паролем. Кодирование в `password_b64`
 * делает сам wrapper — формы передают сюда сырой пароль.
 */
export type ServerAccountCreateInput = Omit<
  ServerAccountCreateRequest,
  "password_b64"
> & {
  /** Plaintext; пусто/`null` — backend сгенерирует пароль сам. */
  password?: string | null;
};

/** Создать аккаунт сразу на нескольких серверах (пароль шифруется at-rest). */
export function createAccount(
  input: ServerAccountCreateInput,
): Promise<ServerAccount> {
  const { password, ...rest } = input;
  const body: ServerAccountCreateRequest = { ...rest };
  if (password) body.password_b64 = toBase64(password);
  return apiPost<ServerAccount>(`${BASE}/server-accounts`, body);
}

/**
 * Карточка одного аккаунта (с паролем при наличии `view_password`).
 *
 * Backend: `GET /server-accounts/{id}` доступен по `view` или `view_password`.
 * Держателю `view_password` поле `password_b64` несёт base64(plaintext) —
 * его надо декодировать (`lib/base64::fromBase64`); иначе `null`. Reveal
 * пишет CRITICAL audit `server_account.password_revealed` и режется per-IP+
 * account rate-limit'ом (429 RATE_LIMIT_EXCEEDED). Сломанный ciphertext при
 * `view_password` → 500 DECRYPT_FAILED.
 */
export function getAccount(accountId: string): Promise<ServerAccount> {
  return apiGet<ServerAccount>(`${BASE}/server-accounts/${accountId}`);
}

/**
 * Частичный PATCH аккаунта — без пароля и привязок.
 *
 * При изменении OS-управляемых атрибутов (`has_sudo`/`unix_groups`/`shell`)
 * backend сам рассылает `update_on_host` на привязанные серверы.
 */
export function updateAccount(
  accountId: string,
  body: ServerAccountUpdateRequest,
): Promise<ServerAccount> {
  return apiPatch<ServerAccount>(`${BASE}/server-accounts/${accountId}`, body);
}

/**
 * Hard-delete аккаунта. Связки уходят каскадом; OS-аккаунт на боксе не
 * удаляется (для этого — отдельный `/deprovision`).
 *
 * Backend `DELETE /server-accounts/{id}` тело не читает — причину удаления
 * audit пишет из server-side контекста, отдельного `reason`-поля у endpoint'а
 * нет. Поэтому wrapper тело не шлёт; подтверждение/причина остаются локальной
 * UX-операцией на стороне UI.
 */
export function deleteAccount(accountId: string): Promise<void> {
  return apiDelete<void>(`${BASE}/server-accounts/${accountId}`);
}

// ---------------------------------------------------------------------------
// Password rotation
// ---------------------------------------------------------------------------

/** Ответ `/rotate_password` — без plaintext'а наружу. */
export interface ServerAccountRotateResponse {
  id: string;
  login: string;
  rotated_at: string;
}

/**
 * User-initiated ротация пароля: меняет только ciphertext в БД, без apply'я
 * на серверы.
 *
 * Body `password` опционален — если не задан, backend сгенерирует случайный
 * через `secrets.token_urlsafe(32)`. Plaintext в ответ не возвращается.
 * Гейтится action `rotate_password`, CRITICAL audit + per-IP rate-limit.
 */
export function rotateAccountUserInitiated(
  accountId: string,
  body: { password?: string | null } = {},
): Promise<ServerAccountRotateResponse> {
  const wire: { password_b64?: string } = {};
  if (body.password) wire.password_b64 = toBase64(body.password);
  return apiPost<ServerAccountRotateResponse>(
    `${BASE}/server-accounts/${accountId}/rotate_password`,
    wire,
  );
}

/** Один задиспатченный per-server элемент в ответе worker-rotate. */
export interface AccountRotateTask {
  server_id: string;
  task_id: string;
}

/** Сервер, на который worker-rotate задача не поставлена. */
export interface AccountRotateSkipped {
  server_id: string;
  reason: string;
}

/**
 * Ответ worker-dispatch'а `/rotate`. `mode` — `single` (один сервер) или `all`
 * (массовая ротация на все привязанные). `partial_failure` — true, если в
 * массовом режиме K задач улетели в очередь, а на K+1-ом worker отбил
 * 503; UI должен показать `tasks` + `next_action`.
 */
export interface AccountRotateDispatchResponse {
  mode: "single" | "all" | string;
  status: string;
  tasks: AccountRotateTask[];
  skipped: AccountRotateSkipped[];
  partial_failure: boolean;
  next_action: string | null;
}

/**
 * Worker-rotate: end-to-end ротация (SSH `chpasswd` + сохранение нового
 * ciphertext). Без `server_id` — массово на все привязанные серверы.
 *
 * Гейтится action `rotate_password`. Plaintext клиенту не возвращается.
 */
export function rotateAccountWorker(
  accountId: string,
  query: { server_id?: string } = {},
): Promise<AccountRotateDispatchResponse> {
  return apiPost<AccountRotateDispatchResponse>(
    `${BASE}/server-accounts/${accountId}/rotate`,
    undefined,
    { query },
  );
}

// ---------------------------------------------------------------------------
// Server linking
// ---------------------------------------------------------------------------

/**
 * Привязать аккаунт к доп. серверам.
 *
 * Идемпотентно: уже привязанные `server_ids` игнорируются. Cross-dept сервер
 * → 404 (скрыто). Login занят на каком-то из серверов другим аккаунтом → 409.
 */
export function bindAccountServers(
  accountId: string,
  body: { server_ids: string[] },
): Promise<ServerAccount> {
  return apiPost<ServerAccount>(
    `${BASE}/server-accounts/${accountId}/servers`,
    body,
  );
}

/**
 * Отвязать аккаунт от сервера.
 *
 * Backend `DELETE /server-accounts/{id}/servers` принимает body `{server_ids}`
 * — отвязка идёт батчем. Wrapper экспортирует single-server форму для
 * UI-удобства, поэтому одиночный `serverId` оборачивается в массив.
 *
 * Нельзя отвязать последний сервер — 409 `ACCOUNT_NO_SERVERS`. На реальном
 * сервере OS-аккаунт не удаляется (для этого `/deprovision`).
 */
export function unbindAccountServer(
  accountId: string,
  serverId: string,
): Promise<ServerAccount> {
  return apiDelete<ServerAccount>(
    `${BASE}/server-accounts/${accountId}/servers`,
    { server_ids: [serverId] },
  );
}

// ---------------------------------------------------------------------------
// Provisioning dispatch (per-server OS-user lifecycle)
// ---------------------------------------------------------------------------

/**
 * Ответ worker-dispatch'а provision/update_on_host/deprovision.
 *
 * `operation` — `provision` (useradd), `update` (usermod) либо `deprovision`
 * (userdel). `server_id` — сервер, на котором применяется операция.
 */
export interface AccountProvisionDispatchResponse {
  operation: "provision" | "update" | "deprovision" | string;
  server_id: string;
  task_id: string;
  status: string;
}

/**
 * Завести OS-пользователя на сервере через worker (`useradd`).
 *
 * Discovered-аккаунт без сохранённого пароля отбивается 409
 * `ACCOUNT_HAS_NO_PASSWORD` — `force_password=true` форсит генерацию нового
 * и overwrite на боксе через `chpasswd`. Managed-аккаунт флаг игнорирует.
 */
export function provisionOnHost(
  serverId: string,
  accountId: string,
  options: { force_password?: boolean } = {},
): Promise<AccountProvisionDispatchResponse> {
  return apiPost<AccountProvisionDispatchResponse>(
    `${BASE}/server-accounts/${accountId}/provision`,
    undefined,
    { query: { server_id: serverId, ...options } },
  );
}

/**
 * Синхронизировать атрибуты OS-пользователя на сервере (`usermod`).
 *
 * Гейтится `update`. Пароль этой операцией не меняется — для пароля
 * `/rotate`. `home_dir` через `usermod` не двигается (отдельный сценарий
 * с переносом данных).
 */
export function updateOnHost(
  serverId: string,
  accountId: string,
): Promise<AccountProvisionDispatchResponse> {
  return apiPost<AccountProvisionDispatchResponse>(
    `${BASE}/server-accounts/${accountId}/update_on_host`,
    undefined,
    { query: { server_id: serverId } },
  );
}

/**
 * Удалить OS-пользователя с сервера через worker (`userdel`).
 *
 * `remove_home=true` пропихивает `userdel --remove`. Связку аккаунт ↔ сервер
 * эта операция НЕ снимает (для отвязки — `unbindAccountServer`).
 */
export function deprovisionOnHost(
  serverId: string,
  accountId: string,
  options: { remove_home?: boolean } = {},
): Promise<AccountProvisionDispatchResponse> {
  return apiPost<AccountProvisionDispatchResponse>(
    `${BASE}/server-accounts/${accountId}/deprovision`,
    undefined,
    { query: { server_id: serverId, ...options } },
  );
}
