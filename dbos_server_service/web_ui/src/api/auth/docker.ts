/**
 * Docker registry endpoints — `/api/auth/v1/docker/*`.
 *
 * Seven endpoints:
 *   - PUT    /docker/registry/{department_id}        — replace config
 *   - PATCH  /docker/registry/{department_id}        — partial update
 *   - GET    /docker/registry/{department_id}        — read config
 *   - DELETE /docker/registry/{department_id}        — remove config
 *   - GET    /docker/token                           — Basic auth issue JWT
 *   - GET    /docker/certs                           — PEM public key
 *   - GET    /docker/jwks                            — JWKS
 */

import { apiDelete, apiGet, apiPatch, apiPut } from "@/api/client";
import type {
  DockerRegistryConfig,
  DockerRegistryConfigPatchRequest,
  DockerRegistryConfigPutRequest,
  DockerTokenResponse,
} from "@/api/auth/types";

const REGISTRY = "/auth/v1/docker/registry";

export function getRegistry(deptId: string): Promise<DockerRegistryConfig> {
  return apiGet<DockerRegistryConfig>(
    `${REGISTRY}/${encodeURIComponent(deptId)}`,
  );
}

export function putRegistry(
  deptId: string,
  req: DockerRegistryConfigPutRequest,
): Promise<DockerRegistryConfig> {
  return apiPut<DockerRegistryConfig>(
    `${REGISTRY}/${encodeURIComponent(deptId)}`,
    req,
  );
}

export function patchRegistry(
  deptId: string,
  req: DockerRegistryConfigPatchRequest,
): Promise<DockerRegistryConfig> {
  return apiPatch<DockerRegistryConfig>(
    `${REGISTRY}/${encodeURIComponent(deptId)}`,
    req,
  );
}

export function deleteRegistry(deptId: string): Promise<void> {
  return apiDelete<void>(`${REGISTRY}/${encodeURIComponent(deptId)}`);
}

// ---------------------------------------------------------------------------
// Token / certs / jwks — public-ish helpers used by the diagnostics UI.
// ---------------------------------------------------------------------------

export interface DockerTokenQuery {
  service?: string;
  scope?: string;
  account?: string;
}

/**
 * `GET /docker/token` — Basic auth (`username:password`). The browser cannot
 * elegantly inject a one-off Basic header onto an unauthenticated fetch
 * without prompting; we build the `Authorization` ourselves and send it via
 * the standard client (which only auto-injects Bearer when `auth: true`).
 *
 * Returned 401/403 surfaces in the UI as a token-issue failure.
 */
export async function getDockerToken(
  username: string,
  password: string,
  query: DockerTokenQuery = {},
): Promise<DockerTokenResponse> {
  const auth = btoa(`${username}:${password}`);
  const params = new URLSearchParams();
  if (query.service) params.set("service", query.service);
  if (query.scope) params.set("scope", query.scope);
  if (query.account) params.set("account", query.account);
  const qs = params.toString();
  // Direct fetch — `apiGet` would inject Bearer if any was stored. Keep this
  // path lean and intentionally bypass the client wrapper.
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";
  const res = await fetch(
    `${base}/auth/v1/docker/token${qs ? `?${qs}` : ""}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Basic ${auth}`,
      },
    },
  );
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text().catch(() => "");
    }
    throw new Error(
      `Docker token request failed (${res.status}): ${JSON.stringify(body)}`,
    );
  }
  return (await res.json()) as DockerTokenResponse;
}

/** PEM-encoded RSA public key. Backend returns `text/plain`. */
export async function getDockerCerts(): Promise<string> {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";
  const res = await fetch(`${base}/auth/v1/docker/certs`);
  if (!res.ok) throw new Error(`Failed to load certs (${res.status})`);
  return res.text();
}

/** JWKS document. */
export interface JwksResponse {
  keys: Array<Record<string, unknown>>;
}

export function getDockerJwks(): Promise<JwksResponse> {
  return apiGet<JwksResponse>("/auth/v1/docker/jwks", { auth: false });
}
