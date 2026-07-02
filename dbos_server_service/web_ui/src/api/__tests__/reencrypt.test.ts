import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiGet,
  registerReencryptHandler,
  type ReencryptNotice,
} from "@/api/client";

// Минимальный fetch-ответ под контракт client.request().
function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  const h = new Headers({ "Content-Type": "application/json", ...headers });
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: h,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

// Тело 503 force-перешифровки: поля retry_after / eta_seconds / remaining
// лежат на верхнем уровне, вне стандартного details-конверта.
function reencryptBody(
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    error: "service_unavailable",
    error_code: "REENCRYPT_IN_PROGRESS",
    message: "Идёт экстренная перешифровка ключа",
    retry_after: 30,
    eta_seconds: 90,
    remaining: 42,
    ...extra,
  };
}

describe("503 REENCRYPT_IN_PROGRESS", () => {
  let notices: ReencryptNotice[];

  beforeEach(() => {
    notices = [];
    registerReencryptHandler((n) => notices.push(n));
  });

  afterEach(() => {
    registerReencryptHandler(null);
    vi.restoreAllMocks();
  });

  it("поднимает баннер с Retry-After и всё равно бросает ApiError (server)", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(503, reencryptBody(), { "Retry-After": "25" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err = await apiGet("/server/v1/admin/encryption/rotate").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).errorCode).toBe("REENCRYPT_IN_PROGRESS");
    // Retry-After из заголовка приоритетнее тела.
    expect((err as ApiError).retryAfter).toBe(25);

    expect(notices).toHaveLength(1);
    expect(notices[0].service).toBe("server");
    expect(notices[0].retryAfter).toBe(25);
    expect(notices[0].etaSeconds).toBe(90);
    expect(notices[0].remaining).toBe(42);
  });

  it("различает secret по пути и берёт retry_after из тела без заголовка", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(503, reencryptBody()));
    vi.stubGlobal("fetch", fetchMock);

    const err = await apiGet("/secret/v1/credentials").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(notices).toHaveLength(1);
    expect(notices[0].service).toBe("secret");
    // Нет заголовка Retry-After → падаем на retry_after из тела.
    expect(notices[0].retryAfter).toBe(30);
  });

  it("не трогает баннер на обычной 503 без кода REENCRYPT_IN_PROGRESS", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(503, {
        error: "service_unavailable",
        error_code: "LOGING_SERVICE_UNAVAILABLE",
        message: "down",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err = await apiGet("/server/v1/servers").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(notices).toHaveLength(0);
  });
});
