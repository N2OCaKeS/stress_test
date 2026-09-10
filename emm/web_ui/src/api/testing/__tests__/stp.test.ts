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
  createStpTestCase,
  deleteStpTestCase,
  generateStp,
  listStpTestCases,
  listStpTestRunCells,
  overrideStpCell,
} from "@/api/testing/stp";

describe("stp wrappers", () => {
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

  it("list test-cases — прокидывает department_id", async () => {
    await listStpTestCases({ department_id: "dep_1" });
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/stp/test-cases", {
      query: { department_id: "dep_1" },
    });
  });

  it("create test-case — POST", async () => {
    const body = { code: "kernel.fill", title: "Fill kernel" };
    await createStpTestCase(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/stp/test-cases", body);
  });

  it("delete test-case — DELETE по id", async () => {
    await deleteStpTestCase("case_1");
    expect(apiDeleteMock).toHaveBeenCalledWith("/testing/v1/stp/test-cases/case_1");
  });

  it("generate — POST /stp/generate с телом", async () => {
    const body = {
      os_version_id: "osv_1", mode: "orel", kernel: "6.1", department_id: "dep_1",
    };
    await generateStp(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/stp/generate", body);
  });

  it("list cells — GET по run_id", async () => {
    apiGetMock.mockResolvedValueOnce([]);
    await listStpTestRunCells("run_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/stp/test-runs/run_1/cells");
  });

  it("override cell — PATCH статуса", async () => {
    await overrideStpCell("cell_1", { status: "pass" });
    expect(apiPatchMock).toHaveBeenCalledWith("/testing/v1/stp/cells/cell_1", {
      status: "pass",
    });
  });
});
