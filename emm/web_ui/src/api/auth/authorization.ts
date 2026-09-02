/**
 * Authorization debug endpoints — `/api/auth/v1/authorization/*`.
 *
 * Two service-to-service endpoints:
 *   - POST /authorization/introspect      — decode any token, return identity
 *   - POST /authorization/service-access  — caller-side check `service X → service Y?`
 *
 * Both are guarded by `require_service_token`, NOT a user Bearer JWT: the
 * caller must present a `SERVICE_API_KEY` (single shared key in legacy mode,
 * or one of `SERVICE_API_KEYS[identity]` together with `X-Service-Identity`
 * when per-service keys are configured). A normal admin session JWT is
 * rejected with 401 `INVALID_SERVICE_TOKEN`.
 *
 * Therefore these helpers take the service key explicitly and send it as the
 * Bearer credential with `auth: false`, so the regular session token is never
 * attached and the 401-refresh/sign-out path in the client is never engaged.
 * `serviceIdentity` is forwarded as `X-Service-Identity` when supplied (it is
 * mandatory in per-service-key mode and harmless otherwise).
 */

import { apiPost } from "@/api/client";
import type { IdentityContext, ServiceName } from "@/api/auth/types";

export interface ServiceCallAuth {
  /** Value sent as `Authorization: Bearer <serviceKey>`. */
  serviceKey: string;
  /** Sent as `X-Service-Identity` when present. */
  serviceIdentity?: string;
}

function serviceHeaders(auth: ServiceCallAuth): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${auth.serviceKey}`,
  };
  const identity = auth.serviceIdentity?.trim();
  if (identity) headers["X-Service-Identity"] = identity;
  return headers;
}

export interface IntrospectRequest {
  token: string;
}

export interface IntrospectResponse {
  active: boolean;
  subject_type?: "user" | "bot" | "oauth_client" | null;
  sub?: string | null;
  username?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  platform_role?: IdentityContext["platform_role"];
  is_banned?: boolean;
  must_change_password?: boolean;
  allowed_services?: ServiceName[];
  service_roles?: Record<ServiceName, string[]>;
  exp?: number | null;
}

export function introspect(
  req: IntrospectRequest,
  auth: ServiceCallAuth,
): Promise<IntrospectResponse> {
  return apiPost<IntrospectResponse>(
    "/auth/v1/authorization/introspect",
    req,
    { auth: false, headers: serviceHeaders(auth) },
  );
}

export interface ServiceAccessRequest {
  subject_token: string;
  service_name: ServiceName;
}

export interface ServiceAccessResponse {
  allowed: boolean;
  department_id?: string | null;
  service_roles?: string[];
}

export function checkServiceAccess(
  req: ServiceAccessRequest,
  auth: ServiceCallAuth,
): Promise<ServiceAccessResponse> {
  return apiPost<ServiceAccessResponse>(
    "/auth/v1/authorization/service-access",
    req,
    { auth: false, headers: serviceHeaders(auth) },
  );
}
