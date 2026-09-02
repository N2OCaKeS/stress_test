import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "@/api/client";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("fetchWithRetry — ретрай GET при network-fail", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("GET ретраится один раз и возвращает данные", async () => {
    let callCount = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      callCount++;
      if (callCount === 1) throw new TypeError("Failed to fetch");
      return jsonResponse({ ok: true });
    }));

    const promise = apiGet<{ ok: boolean }>("/auth/v1/health");
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(callCount).toBe(2);
  });

  it("POST НЕ ретраится при network-fail", async () => {
    let callCount = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      callCount++;
      throw new TypeError("Failed to fetch");
    }));

    const promise = apiPost("/auth/v1/login", {}).catch((e) => e);
    await vi.runAllTimersAsync();
    const err = await promise;

    expect(err).toBeInstanceOf(TypeError);
    expect(callCount).toBe(1);
  });

  it("GET сдаётся после исчерпания ретраев (3 попытки)", async () => {
    let callCount = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      callCount++;
      throw new TypeError("Failed to fetch");
    }));

    const promise = apiGet("/auth/v1/health").catch((e) => e);
    await vi.runAllTimersAsync();
    const err = await promise;

    expect(err).toBeInstanceOf(TypeError);
    expect(callCount).toBe(3);
  });
});
