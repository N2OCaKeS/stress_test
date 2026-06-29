import { afterEach, describe, expect, it, vi } from "vitest";

import { pingCluster } from "@/api/cluster/health";

function okResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => ({ status: "ok" }),
    text: async () => '{"status":"ok"}',
  } as unknown as Response;
}

function statusResponse(status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => ({ status: "not_ready" }),
    text: async () => '{"status":"not_ready"}',
  } as unknown as Response;
}

describe("pingCluster", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("pings the four HTTP services on /health and appends server_worker", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        return okResponse();
      }),
    );

    const rows = await pingCluster();

    // four probed services + worker tail row, fixed order.
    expect(rows.map((r) => r.service)).toEqual([
      "auth_service",
      "loging_service",
      "server_service",
      "secret_service",
      "server_worker",
    ]);
    expect(urls).toEqual([
      "/api/auth/v1/health",
      "/api/logging/v1/health",
      "/api/server/v1/health",
      "/api/secret/v1/health",
    ]);

    const auth = rows[0];
    expect(auth.ok).toBe(true);
    expect(auth.status).toBe(200);
    expect(auth.probeable).toBe(true);
    expect(typeof auth.latency_ms).toBe("number");

    const worker = rows[4];
    expect(worker.probeable).toBe(false);
    expect(worker.ok).toBe(false);
    expect(worker.status).toBeNull();
    expect(worker.latency_ms).toBeNull();
  });

  it("marks a non-2xx response as down with the HTTP code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("/server/v1") ? statusResponse(503) : okResponse(),
      ),
    );

    const rows = await pingCluster();
    const server = rows.find((r) => r.service === "server_service")!;

    expect(server.ok).toBe(false);
    expect(server.status).toBe(503);
    expect(server.error).toBe("HTTP 503");
  });

  it("treats a network failure as down without throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/secret/v1")) throw new TypeError("Failed to fetch");
        return okResponse();
      }),
    );

    const rows = await pingCluster();
    const secret = rows.find((r) => r.service === "secret_service")!;

    expect(secret.ok).toBe(false);
    expect(secret.status).toBeNull();
    expect(secret.error).toBe("сеть недоступна");
  });

  it("reports a timeout abort as down with a timeout message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        const signal = init?.signal;
        if (signal?.aborted) {
          throw new DOMException("aborted", "AbortError");
        }
        // never resolves on its own — only the abort path ends it.
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
      }),
    );

    vi.useFakeTimers();
    const promise = pingCluster();
    await vi.advanceTimersByTimeAsync(6000);
    const rows = await promise;

    for (const r of rows.filter((x) => x.probeable)) {
      expect(r.ok).toBe(false);
      expect(r.error).toContain("таймаут");
    }
  });
});
