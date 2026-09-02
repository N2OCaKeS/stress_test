/**
 * `/api/auth/v1/bots` endpoint wrappers.
 *
 * Covers 10 endpoints from API_ENDPOINTS.md "Bots" section:
 *   1.  POST   /bots                            createBot
 *   2.  GET    /bots                            listBots
 *   3.  GET    /bots/{id}                       getBot
 *   4.  PATCH  /bots/{id}                       patchBot
 *   4a. DELETE /bots/{id}                       deleteBot (account_admin only)
 *   5.  POST   /bots/{id}/tokens                issueBotToken (one-time token)
 *   6.  GET    /bots/{id}/tokens                listBotTokens
 *   7.  DELETE /bots/{id}/tokens/{token_id}     revokeBotToken
 *   8.  GET    /bots/{id}/roles                 listBotRoles
 *   9.  POST   /bots/{id}/roles                 assignBotRoles (replace)
 *   10. DELETE /bots/{id}/roles/{service_name}  revokeBotRoles
 *
 * Convenience helpers (`enableBot` / `disableBot`) re-use `patchBot` with
 * a fixed `status` so the call-site reads naturally.
 */

import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
} from "@/api/client";
import type {
  Bot,
  BotCreateRequest,
  BotPatchRequest,
  BotRoleAssignRequest,
  BotRoleResponse,
  BotTokenCreateRequest,
  BotTokenCreateResponse,
  BotTokenListItem,
  ServiceName,
} from "@/api/auth/types";
import type { PaginationParams } from "@/api/auth/groups";
import { listWithTotal, type PaginatedList } from "@/api/auth/users";

// auth_service возвращает `bot_id`, UI ждёт `id`. Нормализуем единожды.
interface BackendBot extends Omit<Bot, "id"> {
  bot_id?: string;
  id?: string;
}

function normalizeBot(b: BackendBot): Bot {
  return { ...b, id: b.id ?? b.bot_id ?? "" } as Bot;
}

export async function listBots(
  params: PaginationParams & { department_id?: string | null } = {},
): Promise<Bot[]> {
  const raw = await apiGet<BackendBot[]>("/auth/v1/bots", {
    query: {
      limit: params.limit ?? null,
      offset: params.offset ?? null,
      department_id: params.department_id ?? null,
    },
  });
  return raw.map(normalizeBot);
}

/**
 * Как `listBots`, но протаскивает `X-Total-Count` (голый `list[...]`-ответ его
 * теряет). Нужен страницам, которым важно честно показать «N из M» и не молчать
 * про усечение — backend режет `limit` до 200.
 */
export async function listBotsWithTotal(
  params: PaginationParams & { department_id?: string | null } = {},
): Promise<PaginatedList<Bot>> {
  const res = await listWithTotal<BackendBot>("/auth/v1/bots", {
    limit: params.limit ?? null,
    offset: params.offset ?? null,
    department_id: params.department_id ?? null,
  });
  return {
    items: res.items.map(normalizeBot),
    total: res.total,
    totalKnown: res.totalKnown,
  };
}

export async function getBot(botId: string): Promise<Bot> {
  const raw = await apiGet<BackendBot>(
    `/auth/v1/bots/${encodeURIComponent(botId)}`,
  );
  return normalizeBot(raw);
}

export async function createBot(req: BotCreateRequest): Promise<Bot> {
  const raw = await apiPost<BackendBot>("/auth/v1/bots", req);
  return normalizeBot(raw);
}

export async function patchBot(
  botId: string,
  req: BotPatchRequest,
): Promise<Bot> {
  const raw = await apiPatch<BackendBot>(`/auth/v1/bots/${botId}`, req);
  return normalizeBot(raw);
}

export function disableBot(botId: string): Promise<Bot> {
  return patchBot(botId, { status: "disabled" });
}

/**
 * Физическое удаление бота с каскадом токенов / ролей / членств. Доступно
 * только `account_admin`; для остальных backend отвечает 403, для несуще-
 * ствующего бота — 404.
 */
export function deleteBot(botId: string): Promise<void> {
  return apiDelete<void>(`/auth/v1/bots/${encodeURIComponent(botId)}`);
}

export function enableBot(botId: string): Promise<Bot> {
  return patchBot(botId, { status: "active" });
}

// ---------------------------------------------------------------------------
// Tokens (one-time issue)
// ---------------------------------------------------------------------------

export function issueBotToken(
  botId: string,
  req: BotTokenCreateRequest,
): Promise<BotTokenCreateResponse> {
  return apiPost<BotTokenCreateResponse>(
    `/auth/v1/bots/${botId}/tokens`,
    req,
  );
}

export function listBotTokens(botId: string): Promise<BotTokenListItem[]> {
  return apiGet<BotTokenListItem[]>(`/auth/v1/bots/${botId}/tokens`);
}

export function revokeBotToken(
  botId: string,
  tokenId: string,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/bots/${botId}/tokens/${tokenId}`,
  );
}

/**
 * Rotate = выпустить новый токен. Backend держит инвариант «у бота 0 или 1
 * active token» и сам auto-revoke'ает существующие активные токены до выдачи
 * нового, так что rotate сводится к одному POST.
 *
 * `previousTokenId` оставлен сигнатурно для обратной совместимости со старыми
 * вызовами, но игнорируется — backend сам найдёт активные и закроет их.
 */
export async function rotateBotToken(
  botId: string,
  req: BotTokenCreateRequest,
  _previousTokenId?: string,
): Promise<BotTokenCreateResponse> {
  void _previousTokenId;
  return issueBotToken(botId, req);
}

// ---------------------------------------------------------------------------
// Roles (replace-semantics on (bot, service))
// ---------------------------------------------------------------------------

export function listBotRoles(botId: string): Promise<BotRoleResponse[]> {
  return apiGet<BotRoleResponse[]>(`/auth/v1/bots/${botId}/roles`);
}

export function assignBotRoles(
  botId: string,
  req: BotRoleAssignRequest,
): Promise<BotRoleResponse> {
  return apiPost<BotRoleResponse>(`/auth/v1/bots/${botId}/roles`, req);
}

export function revokeBotRoles(
  botId: string,
  serviceName: ServiceName,
): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(
    `/auth/v1/bots/${botId}/roles/${serviceName}`,
  );
}
