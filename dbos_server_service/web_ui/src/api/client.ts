/**
 * Thin fetch wrapper that speaks the `auth_service` contract.
 *
 * Responsibilities:
 *   - Attach `Authorization: Bearer <access>` from the in-memory token store.
 *   - Attach a fresh `X-Request-ID` (uuid v4) on every call so that backend
 *     audit trails can correlate UI actions.
 *   - Parse the uniform error envelope (`{ error, error_code, message,
 *     details, request_id, timestamp }`) into `ApiError`.
 *   - On `429` extract `Retry-After` (header) and `details.retry_after_seconds`
 *     so the lockout flow has a number to render.
 *   - On `401` for an access-token request, transparently exchange the
 *     refresh token and retry once. Failure → forced sign-out.
 *
 * Use:
 *   ```ts
 *   const me = await apiGet<MeResponse>("/auth/v1/me");
 *   await apiPost("/auth/v1/login", { username, password });
 *   ```
 *
 * The base URL comes from `VITE_API_BASE_URL` (default `/api` so it works
 * through the Vite dev proxy without further config).
 */

import type { ApiErrorPayload, RefreshResponse } from "@/api/auth/types";
import { humanErrMsg } from "@/api/errorMessages";
import {
  clearTokens,
  getAccessToken,
  setAccessToken,
} from "@/api/tokenStore";

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface RequestOptions {
  /** Path *after* the API base, e.g. `"/auth/v1/me"`. */
  path: string;
  method?: HttpMethod;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  /** Override auth — pass `false` for public endpoints (login/refresh). */
  auth?: boolean;
  /** Extra headers, override defaults if same key. */
  headers?: Record<string, string>;
  /** Internal: skip 401-refresh-retry (used by the refresh call itself). */
  skipAuthRetry?: boolean;
  /** Internal: skip emitting `signOut()` on terminal auth failure. */
  skipSignOut?: boolean;
  /** AbortController signal. */
  signal?: AbortSignal;
}

export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly errorCode: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;
  readonly timestamp?: string;
  /** From `Retry-After` header or `details.retry_after_seconds`. */
  readonly retryAfter?: number;

  constructor(
    status: number,
    payload: Partial<ApiErrorPayload>,
    retryAfter?: number,
  ) {
    super(payload.message ?? payload.error ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.error = payload.error ?? "unknown_error";
    this.errorCode = payload.error_code ?? "UNKNOWN";
    this.details = payload.details;
    this.requestId = payload.request_id;
    this.timestamp = payload.timestamp;
    this.retryAfter = retryAfter;
  }
}

/**
 * Единый форматтер ошибок для toast/alert. `ApiError` отдаёт стабильный
 * `error_code` + сообщение, обычный `Error` — только message, всё прочее —
 * fallback. Заменяет ~десяток локальных копий `apiErrMsg` по страницам.
 *
 * Для топовых actionable-кодов (см. `@/api/errorMessages`) показываем
 * человекочитаемую подсказку «что произошло и что делать», а сырой код —
 * приглушённым хвостом для саппорта. Незнакомые коды деградируют к прежнему
 * формату `CODE: message`.
 */
export function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) {
    const hint = humanErrMsg(e);
    if (hint) return `${hint} (${e.errorCode})`;
    return `${e.errorCode}: ${e.message}`;
  }
  if (e instanceof Error) return e.message;
  return fallback;
}

// ---------------------------------------------------------------------------
// Sign-out hook
// ---------------------------------------------------------------------------

/**
 * Forced sign-out hook. AuthContext registers a callback here so that the
 * client can yank the session when a refresh fails. Kept module-local instead
 * of injected per call because every error path needs the same behaviour.
 */
type SignOutHandler = () => void;
let signOutHandler: SignOutHandler | null = null;

export function registerSignOutHandler(handler: SignOutHandler | null): void {
  signOutHandler = handler;
}

function triggerSignOut(): void {
  clearTokens();
  if (signOutHandler) {
    try {
      signOutHandler();
    } catch {
      // never let a UI-side error escape the fetch path
    }
  }
}

// Backend middleware returns 403 PASSWORD_CHANGE_REQUIRED on any endpoint
// outside the narrow allow-list while the user still has
// `must_change_password=true`. AuthContext subscribes here to surface the
// modal even if the `identity` payload from /login or /me didn't carry the
// flag (older builds, stale cache, race during bootstrap).
type PwdRequiredHandler = () => void;
let pwdRequiredHandler: PwdRequiredHandler | null = null;

export function registerPasswordChangeRequiredHandler(
  handler: PwdRequiredHandler | null,
): void {
  pwdRequiredHandler = handler;
}

function triggerPasswordChangeRequired(): void {
  if (pwdRequiredHandler) {
    try {
      pwdRequiredHandler();
    } catch {
      // ignore
    }
  }
}

// В force-режиме перешифровки ключа сервис отвечает 503 REENCRYPT_IN_PROGRESS
// на любой запрос, кроме статуса/health, с заголовком Retry-After. Это не
// ошибка, а временное обслуживание — глобальный баннер с обратным отсчётом
// подписывается сюда, чтобы показать состояние вместо обычного тоста.
export interface ReencryptNotice {
  /** Какой сервис ушёл в force-перешифровку (определяется по пути запроса). */
  service: "server" | "secret" | "unknown";
  /** Секунды до предполагаемого возврата — из Retry-After / retry_after / eta_seconds. */
  retryAfter: number;
  etaSeconds?: number;
  remaining?: number;
  message?: string;
}
type ReencryptHandler = (notice: ReencryptNotice) => void;
let reencryptHandler: ReencryptHandler | null = null;

export function registerReencryptHandler(handler: ReencryptHandler | null): void {
  reencryptHandler = handler;
}

function triggerReencrypt(notice: ReencryptNotice): void {
  if (reencryptHandler) {
    try {
      reencryptHandler(notice);
    } catch {
      // never let a UI-side error escape the fetch path
    }
  }
}

function serviceFromPath(path: string): ReencryptNotice["service"] {
  if (path.startsWith("/server/")) return "server";
  if (path.startsWith("/secret/")) return "secret";
  return "unknown";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function uuidv4(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  // Fallback for ancient runtimes — produces a v4-shaped string. Quality is
  // not load-bearing here (header is purely for correlation).
  const bytes = new Uint8Array(16);
  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
    .slice(6, 8)
    .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
}

function buildQuery(
  query: RequestOptions["query"],
): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === null || v === undefined) continue;
    params.append(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

function parseRetryAfter(
  headerValue: string | null,
  details: Record<string, unknown> | undefined,
): number | undefined {
  // `Retry-After` may be seconds (int) or HTTP-date. We only handle seconds.
  if (headerValue) {
    const asInt = Number.parseInt(headerValue, 10);
    if (Number.isFinite(asInt) && asInt >= 0) return asInt;
  }
  if (details && typeof details["retry_after_seconds"] === "number") {
    return details["retry_after_seconds"] as number;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Refresh single-flight
// ---------------------------------------------------------------------------

/**
 * In-flight refresh promise. If two concurrent requests both 401, only one
 * refresh call should hit the backend; the other awaits the same promise.
 *
 * Refresh-токен живёт в HttpOnly cookie `dbos_refresh` — JS его не видит,
 * браузер сам отдаст его на POST /auth/v1/refresh. Если cookie отсутствует
 * или истекла, бэк ответит 422/401 — мы интерпретируем это как «refresh
 * не получился» и инициируем sign-out.
 */
let refreshInFlight: Promise<boolean> | null = null;

// Бэкофф при 429 на /refresh. Per-IP rate-limit бьёт по серии быстрых
// hard-reload'ов — это не провал авторизации, поэтому короткий повтор вместо
// немедленного sign-out. Бюджет держим маленьким, чтобы не зависнуть на
// bootstrap'е, если бэк реально лежит под лимитом.
const REFRESH_MAX_RETRIES = 2;
const REFRESH_BACKOFF_CAP_MS = 3000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Из `Retry-After` (секунды) считаем паузу, но не больше потолка; если заголовка
// нет — экспоненциальный дефолт по номеру попытки, тоже под cap'ом.
function refreshBackoffMs(retryAfterSeconds: number | undefined, attempt: number): number {
  if (retryAfterSeconds !== undefined && retryAfterSeconds >= 0) {
    return Math.min(retryAfterSeconds * 1000, REFRESH_BACKOFF_CAP_MS);
  }
  const fallback = 500 * 2 ** attempt;
  return Math.min(fallback, REFRESH_BACKOFF_CAP_MS);
}

async function performRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          const res = await request<RefreshResponse>({
            path: "/auth/v1/refresh",
            method: "POST",
            body: {},
            auth: false,
            skipAuthRetry: true,
            skipSignOut: true,
          });
          setAccessToken(res.access_token);
          return true;
        } catch (e) {
          // 429 — только лимит частоты, не отзыв сессии. Ждём Retry-After и
          // повторяем, пока не кончится бюджет попыток. 401/422 и прочие
          // (включая benign REFRESH_TOKEN_RACE, который приходит как 401) —
          // не retryable, валимся сразу в sign-out-ветку вызывающего.
          const retryable =
            e instanceof ApiError && e.status === 429 && attempt < REFRESH_MAX_RETRIES;
          if (!retryable) return false;
          await sleep(refreshBackoffMs(e instanceof ApiError ? e.retryAfter : undefined, attempt));
        }
      }
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

// ---------------------------------------------------------------------------
// Core request
// ---------------------------------------------------------------------------

export async function request<T>(opts: RequestOptions): Promise<T> {
  const url = `${BASE_URL}${opts.path}${buildQuery(opts.query)}`;
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Request-ID": uuidv4(),
    ...(opts.headers ?? {}),
  };

  const useAuth = opts.auth !== false;
  if (useAuth) {
    const access = getAccessToken();
    if (access) headers["Authorization"] = `Bearer ${access}`;
  }

  let body: BodyInit | undefined;
  if (opts.body !== undefined && opts.body !== null) {
    headers["Content-Type"] ??= "application/json";
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(url, {
    method,
    headers,
    body,
    signal: opts.signal,
    credentials: "same-origin",
  });

  // 401 with auth attached → attempt single refresh + retry. Refresh-токен
  // в HttpOnly cookie — браузер его прикрепит автоматически. Если cookie
  // нет / истёк, /refresh ответит 4xx, performRefresh вернёт false, мы
  // дёрнем signOut.
  if (res.status === 401 && useAuth && !opts.skipAuthRetry) {
    const ok = await performRefresh();
    if (ok) {
      return request<T>({ ...opts, skipAuthRetry: true });
    }
    if (!opts.skipSignOut) triggerSignOut();
  }

  // Повторный запрос после успешного refresh всё равно вернул 401 — токен
  // отозван / поменялись права на лету. Свежий refresh уже не поможет: рвём
  // сессию, иначе пользователь застрянет с «битым» access в памяти.
  if (
    res.status === 401 &&
    useAuth &&
    opts.skipAuthRetry &&
    !opts.skipSignOut
  ) {
    triggerSignOut();
  }

  if (!res.ok) {
    const retryAfterHeader = res.headers.get("Retry-After");
    let payload: Partial<ApiErrorPayload> = {};
    try {
      payload = (await res.json()) as Partial<ApiErrorPayload>;
    } catch {
      // Non-JSON body (HTML error page, empty 204, etc.) — keep payload empty.
    }
    let retryAfter = parseRetryAfter(retryAfterHeader, payload.details);
    if (res.status === 403 && payload.error_code === "PASSWORD_CHANGE_REQUIRED") {
      triggerPasswordChangeRequired();
    }
    // Force-перешифровка: тело несёт retry_after / eta_seconds / remaining на
    // верхнем уровне (вне стандартного details). Собираем отсчёт с приоритетом
    // Retry-After → retry_after → eta_seconds и поднимаем глобальный баннер.
    if (res.status === 503 && payload.error_code === "REENCRYPT_IN_PROGRESS") {
      const raw = payload as Record<string, unknown>;
      const bodyRetry =
        typeof raw["retry_after"] === "number" ? (raw["retry_after"] as number) : undefined;
      const etaSeconds =
        typeof raw["eta_seconds"] === "number" ? (raw["eta_seconds"] as number) : undefined;
      const remaining =
        typeof raw["remaining"] === "number" ? (raw["remaining"] as number) : undefined;
      const countdown = retryAfter ?? bodyRetry ?? etaSeconds ?? 30;
      retryAfter = countdown;
      triggerReencrypt({
        service: serviceFromPath(opts.path),
        retryAfter: countdown,
        etaSeconds,
        remaining,
        message: payload.message,
      });
    }
    throw new ApiError(res.status, payload, retryAfter);
  }

  // 204 / empty body
  if (res.status === 204 || res.headers.get("Content-Length") === "0") {
    return undefined as T;
  }
  const contentType = res.headers.get("Content-Type") ?? "";
  if (!contentType.includes("application/json")) {
    return (await res.text()) as unknown as T;
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Convenience verbs
// ---------------------------------------------------------------------------

export function apiGet<T>(
  path: string,
  opts: Omit<RequestOptions, "path" | "method" | "body"> = {},
): Promise<T> {
  return request<T>({ ...opts, path, method: "GET" });
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestOptions, "path" | "method" | "body"> = {},
): Promise<T> {
  return request<T>({ ...opts, path, method: "POST", body });
}

export function apiPatch<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestOptions, "path" | "method" | "body"> = {},
): Promise<T> {
  return request<T>({ ...opts, path, method: "PATCH", body });
}

export function apiPut<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestOptions, "path" | "method" | "body"> = {},
): Promise<T> {
  return request<T>({ ...opts, path, method: "PUT", body });
}

export function apiDelete<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestOptions, "path" | "method" | "body"> = {},
): Promise<T> {
  return request<T>({ ...opts, path, method: "DELETE", body });
}

export { BASE_URL as API_BASE_URL };
