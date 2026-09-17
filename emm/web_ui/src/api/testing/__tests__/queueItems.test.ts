import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGetMock = vi.fn();
const apiPostMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
  };
});

import {
  launchQueueItem,
  listQueueItems,
  pauseQueueItem,
  resumeStandQueue,
  retryQueueItem,
  skipQueueItem,
} from "@/api/testing/queueItems";

describe("queueItems wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    apiPostMock.mockResolvedValue({ id: "qi_1", state: "running", interrupt_action: "skip" });
  });

  it("listQueueItems — фильтр по стенду и сортировка попадают в query", async () => {
    await listQueueItems({ kind: "all", stand_id: "ts_1", order: "asc", limit: 200 });
    const url = apiGetMock.mock.calls[0][0] as string;
    expect(url.startsWith("/testing/v1/queue-items?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("kind")).toBe("all");
    expect(params.get("stand_id")).toBe("ts_1");
    expect(params.get("order")).toBe("asc");
    expect(params.get("limit")).toBe("200");
  });

  it("listQueueItems — states уходит повторяющимся параметром", async () => {
    await listQueueItems({ kind: "all", states: ["queued", "running"] });
    const url = apiGetMock.mock.calls[0][0] as string;
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.getAll("states")).toEqual(["queued", "running"]);
  });

  it("launchQueueItem — POST /queue-items с телом запуска", async () => {
    const body = {
      request_id: "req_00000001", test_id: "td_1", stand_id: "ts_1",
      os_version_id: "osv_1", kernel: "6.12.24", debug_mode: false,
    };
    await launchQueueItem(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/queue-items", body);
  });

  it("retryQueueItem — POST /queue-items/{id}/retry с request_id", async () => {
    await retryQueueItem("qi_1", "req_00000002");
    expect(apiPostMock).toHaveBeenCalledWith(
      "/testing/v1/queue-items/qi_1/retry",
      { request_id: "req_00000002" },
    );
  });

  it("skipQueueItem — POST /queue-items/{id}/skip, ответ несёт interrupt_action", async () => {
    const item = await skipQueueItem("qi_1");
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/queue-items/qi_1/skip", {});
    expect(item.state).toBe("running");
    expect(item.interrupt_action).toBe("skip");
  });

  it("pauseQueueItem — POST /queue-items/{id}/pause", async () => {
    apiPostMock.mockResolvedValueOnce({ id: "qi_1", state: "running", interrupt_action: "pause" });
    const item = await pauseQueueItem("qi_1");
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/queue-items/qi_1/pause", {});
    expect(item.interrupt_action).toBe("pause");
  });

  it("resumeStandQueue — POST /test-stands/{id}/resume-queue", async () => {
    await resumeStandQueue("ts_1");
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-stands/ts_1/resume-queue", {});
  });

  it("id элемента и стенда экранируются в пути", async () => {
    await skipQueueItem("qi/1 2");
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/queue-items/qi%2F1%202/skip", {});
    apiPostMock.mockClear();
    await resumeStandQueue("ts/1");
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-stands/ts%2F1/resume-queue", {});
  });
});
