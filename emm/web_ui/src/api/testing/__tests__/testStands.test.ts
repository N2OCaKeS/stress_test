import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPatchMock = vi.fn();
const apiDeleteMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
    apiPatch: (...a: unknown[]) => apiPatchMock(...a),
    apiDelete: (...a: unknown[]) => apiDeleteMock(...a),
  };
});

import {
  createTestStand,
  deleteTestStand,
  findActiveQueueItemForServer,
  findStandByServerId,
  getCurrentQueueItem,
  getTestStand,
  getTestStandCredentials,
  listTestStands,
  updateTestStand,
} from "@/api/testing/testStands";

describe("testStands wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiPatchMock.mockReset();
    apiDeleteMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    apiPostMock.mockResolvedValue({});
    apiPatchMock.mockResolvedValue({});
    apiDeleteMock.mockResolvedValue({ ok: true });
  });

  it("findStandByServerId — фильтрует по server_id, limit=1", async () => {
    apiGetMock.mockResolvedValueOnce({
      items: [{ id: "stand_1", server_id: "srv_1", department_id: "dep_1", queue_enabled: true, is_active: true }],
      total: 1, limit: 1, offset: 0,
    });
    const stand = await findStandByServerId("srv_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-stands", {
      query: { server_id: "srv_1", limit: 1 },
    });
    expect(stand?.id).toBe("stand_1");
  });

  it("getCurrentQueueItem — GET на current-queue-item", async () => {
    apiGetMock.mockResolvedValueOnce(null);
    const item = await getCurrentQueueItem("stand_1");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/test-stands/stand_1/current-queue-item",
    );
    expect(item).toBeNull();
  });

  it("findActiveQueueItemForServer — null, если сервер не стенд", async () => {
    apiGetMock.mockResolvedValueOnce({ items: [], total: 0, limit: 1, offset: 0 });
    const item = await findActiveQueueItemForServer("srv_unknown");
    expect(item).toBeNull();
    expect(apiGetMock).toHaveBeenCalledTimes(1);
  });

  it("list — прокидывает фильтры", async () => {
    await listTestStands({ department_id: "dep_1", is_active: true });
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-stands", {
      query: { department_id: "dep_1", is_active: true },
    });
  });

  it("get — карточка по id", async () => {
    await getTestStand("stand_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-stands/stand_1");
  });

  it("create — POST с телом", async () => {
    const body = { server_id: "srv_1" };
    await createTestStand(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-stands", body);
  });

  it("update — PATCH только queue_enabled/is_active", async () => {
    await updateTestStand("stand_1", { queue_enabled: false });
    expect(apiPatchMock).toHaveBeenCalledWith("/testing/v1/test-stands/stand_1", {
      queue_enabled: false,
    });
  });

  it("delete — DELETE по id", async () => {
    await deleteTestStand("stand_1");
    expect(apiDeleteMock).toHaveBeenCalledWith("/testing/v1/test-stands/stand_1");
  });

  it("test-credentials — reveal=false по умолчанию", async () => {
    await getTestStandCredentials("stand_1");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/test-stands/stand_1/test-credentials",
      { query: { reveal: false } },
    );
  });

  it("test-credentials — reveal=true прокидывается в query", async () => {
    await getTestStandCredentials("stand_1", true);
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/test-stands/stand_1/test-credentials",
      { query: { reveal: true } },
    );
  });
});
