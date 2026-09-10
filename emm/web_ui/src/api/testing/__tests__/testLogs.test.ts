import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const apiGetMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
  };
});

vi.mock("@/api/tokenStore", () => ({
  getAccessToken: () => "access-token-1",
}));

import { downloadTestLog, getTestLogText, listLogSegments } from "@/api/testing/testLogs";

function textResponse(body: string, headers: Record<string, string> = {}): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8", ...headers },
  });
}

describe("testLogs wrappers", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getTestLogText — GET без диапазона, парсит имя файла из Content-Disposition", async () => {
    fetchMock.mockResolvedValueOnce(
      textResponse("log body", {
        "Content-Disposition": 'attachment; filename="stand1_test_1.8.5_6.1_2026-09-10.log"',
      }),
    );
    const result = await getTestLogText("qi_1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/testing/v1/queue-items/qi_1/log");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer access-token-1",
    );
    expect(result.text).toBe("log body");
    expect(result.filename).toBe("stand1_test_1.8.5_6.1_2026-09-10.log");
  });

  it("getTestLogText — диапазон from/to в query", async () => {
    fetchMock.mockResolvedValueOnce(textResponse("partial"));
    await getTestLogText("qi_1", { from: 10, to: 20 });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/testing/v1/queue-items/qi_1/log?from=10&to=20");
  });

  it("downloadTestLog — триггерит скачивание через <a download>", async () => {
    fetchMock.mockResolvedValueOnce(
      textResponse("log content", { "Content-Disposition": 'attachment; filename="run.log"' }),
    );
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const result = await downloadTestLog("qi_1");

    expect(result.filename).toBe("run.log");
    expect(createObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    clickSpy.mockRestore();
  });

  it("listLogSegments — прокидывает фильтр по статусу", async () => {
    await listLogSegments("qi_1", { status: "FATAL", limit: 50 });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/queue-items/qi_1/log/segments",
      { query: { status: "FATAL", limit: 50 } },
    );
  });
});
