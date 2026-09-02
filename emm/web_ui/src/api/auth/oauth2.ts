/**
 * OAuth2 clients & token flow — `/api/auth/v1/oauth2/*`.
 *
 * Five backend endpoints:
 *   - POST   /oauth2/clients              — create
 *   - GET    /oauth2/clients              — list
 *   - DELETE /oauth2/clients/{client_id}  — soft delete
 *   - GET    /oauth2/authorize            — authorize redirect (302)
 *   - POST   /oauth2/token                — exchange / client_credentials
 *
 * `GET /oauth2/authorize` is a redirect endpoint; the UI builds the URL and
 * opens it in a new tab so the browser handles the 302 normally. The auth
 * tester page also provides a `/oauth2/token` form that hits the public
 * endpoint without a Bearer header.
 */

import { apiDelete, apiGet, apiPost } from "@/api/client";
import type {
  OAuth2Client,
  OAuth2ClientCreateRequest,
  OAuth2ClientCreatedResponse,
} from "@/api/auth/types";

const CLIENTS = "/auth/v1/oauth2/clients";

export function listClients(departmentId?: string): Promise<OAuth2Client[]> {
  return apiGet<OAuth2Client[]>(CLIENTS, {
    query: departmentId ? { department_id: departmentId } : undefined,
  });
}

export function createClient(
  req: OAuth2ClientCreateRequest,
): Promise<OAuth2ClientCreatedResponse> {
  return apiPost<OAuth2ClientCreatedResponse>(CLIENTS, req);
}

export function deleteClient(clientId: string): Promise<void> {
  return apiDelete<void>(`${CLIENTS}/${encodeURIComponent(clientId)}`);
}

// ---------------------------------------------------------------------------
// OAuth2 flow helpers (used by the AuthorizeTester page)
// ---------------------------------------------------------------------------

export interface AuthorizeUrlParams {
  client_id: string;
  redirect_uri: string;
  response_type?: "code";
  scope?: string;
  state?: string;
  code_challenge?: string;
  code_challenge_method?: "S256" | "plain";
}

/**
 * Builds the `/api/auth/v1/oauth2/authorize` URL. The page redirects
 * (`window.location.href = ...`) so the browser preserves the user JWT in the
 * `Authorization` header — actually no, browsers do not carry that header on
 * top-level navigation. The tester therefore renders the URL and explains
 * the caller has to inject Bearer manually (curl/fetch). Real OAuth flows
 * happen between an external app and the backend, not via this UI.
 */
export function buildAuthorizeUrl(
  params: AuthorizeUrlParams,
  basePath = "/api/auth/v1/oauth2/authorize",
): string {
  const u = new URLSearchParams();
  u.set("client_id", params.client_id);
  u.set("redirect_uri", params.redirect_uri);
  u.set("response_type", params.response_type ?? "code");
  if (params.scope) u.set("scope", params.scope);
  if (params.state) u.set("state", params.state);
  if (params.code_challenge) u.set("code_challenge", params.code_challenge);
  if (params.code_challenge_method)
    u.set("code_challenge_method", params.code_challenge_method);
  return `${basePath}?${u.toString()}`;
}

export interface OAuthTokenRequest {
  grant_type: "authorization_code" | "client_credentials" | "refresh_token";
  code?: string;
  redirect_uri?: string;
  client_id?: string;
  client_secret?: string;
  code_verifier?: string;
}

export interface OAuthTokenResponse {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  scope?: string;
}

/**
 * `POST /oauth2/token`. Public endpoint (no Bearer). Used by the tester to
 * exercise `client_credentials` flows from the UI.
 */
export function exchangeToken(req: OAuthTokenRequest): Promise<OAuthTokenResponse> {
  return apiPost<OAuthTokenResponse>("/auth/v1/oauth2/token", req, {
    auth: false,
  });
}
