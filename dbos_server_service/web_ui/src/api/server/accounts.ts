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
  AccountAclAction,
  AccountAclGrant,
  CursorPaginatedResponse,
  IgnoredLogin,
  ImportUnknownUserRequest,
  OffsetPaginatedResponse,
  RecreateLoginDispatch,
  RecreateLoginRequest,
  ServerAccount,
  ServerAccountCreateRequest,
  ServerAccountUpdateRequest,
  SshKeyMode,
  SshKeyRequest,
  SshKeyResponse,
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

/**
 * Создать аккаунт сразу на нескольких серверах (пароль шифруется at-rest).
 *
 * При `ssh_mode: "generate"` ответ ОДИН РАЗ несёт `ssh_private_key` —
 * caller обязан сразу предложить его скачать; повторно backend его не отдаёт.
 */
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
 * Пересоздать OS-аккаунт под новым login (destructive).
 *
 * Запрашивается, когда PATCH `login` отбит 409 `LOGIN_LOCKED` (аккаунт уже
 * present на сервере и обычный rename невозможен). Backend сносит OS-аккаунт
 * со всеми данными `$HOME` на всех привязанных серверах и заводит заново.
 *
 * Ошибки: 403 (нет dep_admin/service-admin), 404 ACCOUNT_NOT_FOUND, 409
 * (новый login уже занят).
 *
 * Возвращает не саму карточку аккаунта, а сводку диспатча (deprovision/
 * provision/skipped) — обновлённый аккаунт надо перезапросить отдельно.
 */
export function recreateLogin(
  accountId: string,
  body: RecreateLoginRequest,
): Promise<RecreateLoginDispatch> {
  return apiPost<RecreateLoginDispatch>(
    `${BASE}/server-accounts/${accountId}/recreate_login`,
    body,
  );
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
// SSH-ключи аккаунта
// ---------------------------------------------------------------------------

/**
 * Выдать (или заменить) SSH-ключ аккаунта.
 *
 * `ssh_mode: "generate"` — backend генерирует пару и ОДИН РАЗ возвращает
 * `ssh_private_key`; `ssh_mode: "supply"` — оператор передаёт готовый публичный
 * ключ (`ssh_public_key`), приватный сервису не известен. Раскатка на
 * привязанные серверы — на стороне backend автоматически (`tasks`/`skipped`).
 *
 * Гейтится `update`. Ошибки: 403, 404 ACCOUNT_NOT_FOUND, 422 (кривой
 * публичный ключ / отсутствует при `supply`).
 */
export function setAccountSshKey(
  accountId: string,
  body: SshKeyRequest,
): Promise<SshKeyResponse> {
  return apiPost<SshKeyResponse>(
    `${BASE}/server-accounts/${accountId}/ssh_key`,
    body,
  );
}

/**
 * Ротировать SSH-ключ аккаунта (кейс компрометации).
 *
 * Тела не принимает — backend всегда генерирует новую пару и раскатывает на
 * привязанные серверы; после раскатки старый ключ перестаёт работать.
 * Приватный ключ приходит в ответе ОДИН РАЗ.
 */
export function rotateAccountSshKey(
  accountId: string,
): Promise<SshKeyResponse> {
  return apiPost<SshKeyResponse>(
    `${BASE}/server-accounts/${accountId}/rotate_ssh_key`,
    {},
  );
}

export type { SshKeyMode };

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
// Ревизия атрибутов: применить найденное на боксе значение в БД
// ---------------------------------------------------------------------------

/**
 * Тело `POST /server-accounts/{id}/adopt_from_host`.
 *
 * Шлём только те поля, расхождение по которым оператор подтвердил в модалке
 * ревизии — со значениями из `found` (то, что реально на боксе). `server_id`
 * обязателен: ревизия привязана к конкретному серверу. Пустое тело без полей
 * backend отбивает 422 `NO_FIELDS_TO_ADOPT`.
 */
export interface AdoptFromHostRequest {
  server_id: string;
  has_sudo?: boolean;
  unix_groups?: string[];
  shell?: string | null;
}

/**
 * Подтянуть в БД атрибуты OS-пользователя, найденные на сервере ревизией
 * (`users/inventory`). Возвращает обновлённую карточку аккаунта (как
 * `getAccount`).
 *
 * Ошибки: 403 PERMISSION_DENIED, 404 ACCOUNT_NOT_FOUND, 422
 * NO_FIELDS_TO_ADOPT / невалидные unix_groups.
 */
export function adoptFromHost(
  accountId: string,
  body: AdoptFromHostRequest,
): Promise<ServerAccount> {
  return apiPost<ServerAccount>(
    `${BASE}/server-accounts/${accountId}/adopt_from_host`,
    body,
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

// ---------------------------------------------------------------------------
// Discovery: импорт незнакомого OS-юзера в БД
// ---------------------------------------------------------------------------

/**
 * Завести найденного ревизией OS-юзера в БД как discovered-аккаунт
 * (`present_on_server=true`). `source` по умолчанию `discovered`.
 *
 * Ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND, 409 ACCOUNT_DUPLICATE
 * (login уже заведён на этом сервере).
 */
export function importUnknownUser(
  body: ImportUnknownUserRequest,
): Promise<ServerAccount> {
  return apiPost<ServerAccount>(`${BASE}/server-accounts/import`, {
    source: "discovered",
    ...body,
  });
}

// ---------------------------------------------------------------------------
// Per-account ACL: прямые гранты доступа к учётке пользователям/ботам
// ---------------------------------------------------------------------------

/**
 * Список прямых грантов учётки. Гранты добавляют доступ поверх ролей отдела;
 * dep_admin и server.admin видят учётку всегда независимо от грантов.
 * Гейтится `manage_account_acl` — без права 403, cross-dept учётка → 404.
 */
export function listAccountAcl(accountId: string): Promise<AccountAclGrant[]> {
  return apiGet<AccountAclGrant[]>(
    `${BASE}/server-accounts/${accountId}/acl`,
  );
}

/**
 * Выдать (или заменить) грант пользователю. Повторный POST на тот же
 * (account, user) перезаписывает набор действий целиком.
 *
 * Ошибки: 422 INVALID_ACL_ACTION / INVALID_USER_ID, 403 (нет
 * manage_account_acl), 404 (учётка не найдена / cross-dept).
 */
export function addAccountAcl(
  accountId: string,
  body: { user_id: string; actions: AccountAclAction[] },
): Promise<AccountAclGrant> {
  return apiPost<AccountAclGrant>(
    `${BASE}/server-accounts/${accountId}/acl`,
    body,
  );
}

/**
 * Снять грант пользователя. Нет гранта → 404 ACL_GRANT_NOT_FOUND.
 */
export function revokeAccountAcl(
  accountId: string,
  userId: string,
): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(
    `${BASE}/server-accounts/${accountId}/acl/${encodeURIComponent(userId)}`,
  );
}

// ---------------------------------------------------------------------------
// Ignore-list (на отдел): логины, которые ревизия не считает незнакомыми
// ---------------------------------------------------------------------------

/**
 * Ignore-list отдела. Гейтится `manage_ignored_logins` — без права 403.
 */
export function listIgnoredLogins(): Promise<IgnoredLogin[]> {
  return apiGet<IgnoredLogin[]>(`${BASE}/server-accounts/ignored-logins`);
}

/**
 * Добавить логин в ignore-list. 409 IGNORED_LOGIN_DUPLICATE, если уже есть.
 */
export function addIgnoredLogin(body: {
  login: string;
  reason?: string | null;
}): Promise<IgnoredLogin> {
  return apiPost<IgnoredLogin>(
    `${BASE}/server-accounts/ignored-logins`,
    body,
  );
}

/**
 * Снять логин с ignore-list. 404 IGNORED_LOGIN_NOT_FOUND, если записи нет.
 */
export function removeIgnoredLogin(login: string): Promise<void> {
  return apiDelete<void>(
    `${BASE}/server-accounts/ignored-logins/${encodeURIComponent(login)}`,
  );
}
