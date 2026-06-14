import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiGet,
  registerSignOutHandler,
} from "@/api/client";
import { getAccessToken, setAccessToken } from "@/api/tokenStore";

// Минимальный fetch-ответ под контракт client.request().
function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  const h = new Headers({ "Content-Type": "application/json", ...headers });
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: h,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function errEnvelope(code: string): Record<string, unknown> {
  return {
    error: "error",
    error_code: code,
    message: code,
    request_id: "req_test",
    timestamp: "2026-06-15T00:00:00Z",
  };
}

describe("performRefresh 429 backoff", () => {
  let signOut: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    signOut = vi.fn();
    registerSignOutHandler(signOut);
    setAccessToken("access_old");
  });

  afterEach(() => {
    vi.useRealTimers();
    registerSignOutHandler(null);
    setAccessToken(null);
    vi.restoreAllMocks();
  });

  it("retries /refresh after a 429 and succeeds without sign-out", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push(`${method} ${url}`);
      if (url.includes("/auth/v1/me")) {
        // First protected call 401s, retried call (after refresh) succeeds.
        const meCalls = calls.filter((c) => c.includes("/auth/v1/me")).length;
        if (meCalls === 1) return jsonResponse(401, errEnvelope("ACCESS_TOKEN_EXPIRED"));
        return jsonResponse(200, { id: "usr_1" });
      }
      if (url.includes("/auth/v1/refresh")) {
        const refreshCalls = calls.filter((c) => c.includes("/auth/v1/refresh")).length;
        if (refreshCalls === 1) {
          return jsonResponse(429, errEnvelope("RATE_LIMIT_EXCEEDED"), { "Retry-After": "1" });
        }
        return jsonResponse(200, { access_token: "access_new", token_type: "bearer" });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const promise = apiGet<{ id: string }>("/auth/v1/me");
    await vi.runAllTimersAsync();
    const me = await promise;

    expect(me).toEqual({ id: "usr_1" });
    expect(getAccessToken()).toBe("access_new");
    expect(signOut).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.includes("/auth/v1/refresh")).length).toBe(2);
  });

  it("signs out when /refresh stays 429 past the retry budget", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/auth/v1/me")) return jsonResponse(401, errEnvelope("ACCESS_TOKEN_EXPIRED"));
      if (url.includes("/auth/v1/refresh")) {
        return jsonResponse(429, errEnvelope("RATE_LIMIT_EXCEEDED"), { "Retry-After": "1" });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const promise = apiGet("/auth/v1/me").catch((e) => e);
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result).toBeInstanceOf(ApiError);
    expect(signOut).toHaveBeenCalledTimes(1);
    // initial attempt + REFRESH_MAX_RETRIES (2) = 3 refresh hits.
    expect(calls.filter((c) => c.includes("/auth/v1/refresh")).length).toBe(3);
  });

  it("does not retry a 401 REFRESH_TOKEN_INVALID — immediate sign-out", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/auth/v1/me")) return jsonResponse(401, errEnvelope("ACCESS_TOKEN_EXPIRED"));
      if (url.includes("/auth/v1/refresh")) {
        return jsonResponse(401, errEnvelope("REFRESH_TOKEN_INVALID"));
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const promise = apiGet("/auth/v1/me").catch((e) => e);
    await vi.runAllTimersAsync();
    await promise;

    expect(signOut).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.includes("/auth/v1/refresh")).length).toBe(1);
  });
});
