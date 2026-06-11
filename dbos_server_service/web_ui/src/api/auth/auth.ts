/**
 * Thin auth_service endpoint wrappers. Higher-level state management
 * (storage, redirect) lives in AuthContext; this module only types the
 * HTTP calls.
 *
 * Refresh / logout полагаются на HttpOnly cookie `dbos_refresh`, которую
 * бэк ставит на login и обновляет на refresh. Поэтому body здесь пустой —
 * cookie прилетает автоматически благодаря path scope /api/auth/v1.
 */

import { apiGet, apiPost } from "@/api/client";
import type {
  LoginRequest,
  LoginResponse,
  LogoutResponse,
  MeResponse,
  RefreshResponse,
} from "@/api/auth/types";

export function login(req: LoginRequest): Promise<LoginResponse> {
  return apiPost<LoginResponse>("/auth/v1/login", req, { auth: false });
}

export function refresh(): Promise<RefreshResponse> {
  return apiPost<RefreshResponse>(
    "/auth/v1/refresh",
    {},
    { auth: false },
  );
}

export function logout(): Promise<LogoutResponse> {
  return apiPost<LogoutResponse>(
    "/auth/v1/logout",
    {},
    { auth: false, skipSignOut: true },
  );
}

export function getMe(): Promise<MeResponse> {
  return apiGet<MeResponse>("/auth/v1/me");
}
