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
  createTestRun,
  getRunSummaryComment,
  getTestRun,
  listTestRuns,
} from "@/api/testing/testRuns";

describe("testRuns wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    apiPostMock.mockResolvedValue({});
  });

  it("create — POST с телом кампании", async () => {
    const body = {
      os_version_id: "osv_1",
      mode: "orel",
      kernel: "6.1",
      test_run_stands: ["stand_1", "stand_2"],
      final: true,
    };
    await createTestRun(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-runs", body);
  });

  it("list — прокидывает фильтры", async () => {
    await listTestRuns({ department_id: "dep_1", status: "running", final: false });
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-runs", {
      query: { department_id: "dep_1", status: "running", final: false },
    });
  });

  it("get — карточка кампании по id", async () => {
    await getTestRun("run_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-runs/run_1");
  });

  it("summary-comment — GET по run_id", async () => {
    await getRunSummaryComment("run_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-runs/run_1/summary-comment");
  });
});
