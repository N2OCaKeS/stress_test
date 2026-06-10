/**
 * Authorization debug endpoints — `/api/auth/v1/authorization/*`.
 *
 * Two M2M endpoints:
 *   - POST /authorization/introspect      — decode any token, return identity
 *   - POST /authorization/service-access  — caller-side check `service X → service Y?`
 *
 * Both endpoints require `SERVICE_API_KEY` + optional `X-Service-Identity` in
 * prod. From the UI we proxy through the regular Bearer-authed client; the
 * dev backend is configured to accept admin JWTs for these debug calls. If
 * the deployment locks them down hard, the page surfaces the 401 to the user.
 */

import { apiPost } from "@/api/client";
import type { IdentityContext, ServiceName } from "@/api/auth/types";

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

export function introspect(req: IntrospectRequest): Promise<IntrospectResponse> {
  return apiPost<IntrospectResponse>("/auth/v1/authorization/introspect", req);
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
): Promise<ServiceAccessResponse> {
  return apiPost<ServiceAccessResponse>(
    "/auth/v1/authorization/service-access",
    req,
  );
}
