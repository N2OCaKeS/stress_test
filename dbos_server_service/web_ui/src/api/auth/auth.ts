/**
 * Thin auth_service endpoint wrappers. Higher-level state management
 * (storage, redirect) lives in AuthContext; this module only types the
 * HTTP calls.
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

export function refresh(refreshToken: string): Promise<RefreshResponse> {
  return apiPost<RefreshResponse>(
    "/auth/v1/refresh",
    { refresh_token: refreshToken },
    { auth: false },
  );
}

export function logout(refreshToken: string): Promise<LogoutResponse> {
  return apiPost<LogoutResponse>(
    "/auth/v1/logout",
    { refresh_token: refreshToken },
    { auth: false, skipSignOut: true },
  );
}

export function getMe(): Promise<MeResponse> {
  return apiGet<MeResponse>("/auth/v1/me");
}
