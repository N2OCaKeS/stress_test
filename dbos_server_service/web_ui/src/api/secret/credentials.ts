/**
 * Thin wrappers для `secret_service` `/secret/v1/credentials/*`.
 *
 * Один метод на backend-route, типы — из `./types.ts`. Список кред идёт
 * cursor-envelope'ом (`{items,next_cursor}`); guest-роль получает урезанный
 * shape (`CredentialGuestList`) — caller разбирает по наличию полей в строке.
 *
 * Source of truth:
 *   secret_service/src/api/v1/endpoints/credentials.py
 *   secret_service/src/schemas/credentials.py
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  AdminDeleteRequest,
  Credential,
  CredentialCreateRequest,
  CredentialCreateWire,
  CredentialGuestList,
  CredentialList,
  CredentialRevealResponse,
  CredentialUpdateRequest,
  CredentialUpdateWire,
  OkResponse,
  TransferRequest,
} from "@/api/secret/types";
import { toBase64 } from "@/lib/base64";

const BASE = "/secret/v1/credentials";

/** Параметры фильтрации `GET /credentials`. */
export interface ListCredentialsQuery {
  scope?: string;
  service?: string;
  status?: string;
  limit?: number;
  /** Opaque cursor предыдущей страницы (`<iso-ts>|<cred_id>`). */
  cursor?: string;
}

/**
 * `GET /credentials` — список видимых кред (cursor envelope).
 *
 * Для guest-роли backend отдаёт `CredentialGuestList` (урезанные строки).
 * Wrapper типизирован union'ом; UI определяет shape по наличию `owner_user_id`
 * в первой строке.
 */
export function listCredentials(
  query: ListCredentialsQuery = {},
): Promise<CredentialList | CredentialGuestList> {
  return apiGet<CredentialList | CredentialGuestList>(BASE, {
    query: {
      scope: query.scope,
      service: query.service,
      status: query.status,
      limit: query.limit,
      cursor: query.cursor,
    },
  });
}

/** `GET /credentials/{id}` — карточка кред (без секрета). */
export function getCredential(id: string): Promise<Credential> {
  return apiGet<Credential>(`${BASE}/${id}`);
}

/**
 * `POST /credentials` — создать креду (секрет шифруется at-rest, plaintext
 * наружу не возвращается). `scope`/`secret`/`name`/`service` обязательны;
 * `owner_dept_id` обязателен для department/cross_department scope.
 *
 * Caller передаёт plaintext в `secret`; на провод уходит `secret_b64`.
 */
export function createCredential(
  body: CredentialCreateRequest,
): Promise<Credential> {
  const { secret, ...rest } = body;
  const wire: CredentialCreateWire = { ...rest, secret_b64: toBase64(secret) };
  return apiPost<Credential>(BASE, wire);
}

/**
 * `PATCH /credentials/{id}` — partial-обновление name/login/secret/validity.
 *
 * `secret` (plaintext) кодируется в `secret_b64`. Если поле опущено — секрет
 * не перешифровывается и на провод не уходит.
 */
export function updateCredential(
  id: string,
  body: CredentialUpdateRequest,
): Promise<Credential> {
  const { secret, ...rest } = body;
  const wire: CredentialUpdateWire = { ...rest };
  if (secret != null && secret !== "") wire.secret_b64 = toBase64(secret);
  return apiPatch<Credential>(`${BASE}/${id}`, wire);
}

/**
 * `DELETE /credentials/{id}` — удалить креду.
 *
 * Для admin-override (удаляет не owner) backend требует `reason` в body.
 * Owner может слать пустое тело. UI всегда сопровождает delete причиной.
 */
export function deleteCredential(
  id: string,
  body: AdminDeleteRequest = {},
): Promise<OkResponse> {
  return apiDelete<OkResponse>(`${BASE}/${id}`, body);
}

/**
 * `POST /credentials/{id}/reveal` — расшифровать секрет.
 *
 * CRITICAL audit + 5-минутный throttle на стороне бэка; тело не требуется.
 * Возвращает `{login, secret_b64}` (base64-encoded plaintext).
 */
export function revealCredential(
  id: string,
): Promise<CredentialRevealResponse> {
  return apiPost<CredentialRevealResponse>(`${BASE}/${id}/reveal`);
}

/**
 * `POST /credentials/{id}/transfer` — передать ownership заблокированной кред.
 *
 * Ровно одно из `new_owner_user_id` / `new_owner_dept_id` + обязательный
 * `reason` (CRITICAL-операция). Гейтится admin secret_service владеющего
 * dept'а — account_admin к transfer не подпущен (нет dept-scope).
 */
export function transferCredential(
  id: string,
  body: TransferRequest,
): Promise<Credential> {
  return apiPost<Credential>(`${BASE}/${id}/transfer`, body);
}

/**
 * `POST /credentials/{id}/recover` — снять блокировку в окне 30 дней.
 *
 * Тело не требуется. Гейтится admin secret_service владеющего dept'а —
 * account_admin к recover не подпущен (нет dept-scope).
 */
export function recoverCredential(id: string): Promise<Credential> {
  return apiPost<Credential>(`${BASE}/${id}/recover`);
}
