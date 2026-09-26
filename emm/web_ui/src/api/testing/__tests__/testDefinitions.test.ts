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
  createTestDefinition,
  deleteTestDefinition,
  getTestDefinitionByCode,
  listTestDefinitions,
  previewTestLaunch,
  updateTestDefinition,
} from "@/api/testing/testDefinitions";

describe("testDefinitions wrappers", () => {
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

  it("list — прокидывает фильтры department_id/category/readiness", async () => {
    await listTestDefinitions({ department_id: "dep_1", category: "kernel", readiness: "ready" });
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-definitions", {
      query: { department_id: "dep_1", category: "kernel", readiness: "ready" },
    });
  });

  it("get by code", async () => {
    await getTestDefinitionByCode("postgresql.balance");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/test-definitions/by-code/postgresql.balance",
    );
  });

  it("create — POST с телом", async () => {
    const body = { code: "kernel.fill", full_name: "Fill kernel" };
    await createTestDefinition(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-definitions", body);
  });

  it("update — PATCH по id", async () => {
    await updateTestDefinition("tdef_1", { readiness: "deprecated" });
    expect(apiPatchMock).toHaveBeenCalledWith("/testing/v1/test-definitions/tdef_1", {
      readiness: "deprecated",
    });
  });

  it("delete — DELETE по id", async () => {
    await deleteTestDefinition("tdef_1");
    expect(apiDeleteMock).toHaveBeenCalledWith("/testing/v1/test-definitions/tdef_1");
  });

  it("launch-preview — POST с выбранными стендом, РЦ и ядром", async () => {
    const body = { stand_id: "tst_1", os_version_id: "osv_1", kernel: "6.1", debug: true };
    await previewTestLaunch("tdef_1", body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/test-definitions/tdef_1/launch-preview", body);
  });
});
