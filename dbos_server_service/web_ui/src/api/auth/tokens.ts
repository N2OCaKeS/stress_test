/**
 * Personal Access Tokens — `/api/auth/v1/tokens`.
 *
 * Backend lists 3 endpoints (POST/GET/DELETE under `/tokens`). All operations
 * are scoped to the current bearer principal — there is no admin "view all
 * PATs" endpoint, the path was discussed and intentionally not implemented.
 */

import { apiDelete, apiGet, apiPost } from "@/api/client";
import type {
  PATCreateRequest,
  PATCreateResponse,
  PersonalAccessToken,
} from "@/api/auth/types";

const BASE = "/auth/v1/tokens";

export function listMyTokens(): Promise<PersonalAccessToken[]> {
  return apiGet<PersonalAccessToken[]>(BASE);
}

export function createToken(req: PATCreateRequest): Promise<PATCreateResponse> {
  return apiPost<PATCreateResponse>(BASE, req);
}

export function revokeToken(tokenId: string): Promise<void> {
  return apiDelete<void>(`${BASE}/${encodeURIComponent(tokenId)}`);
}
