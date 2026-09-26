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
  createGlobalVariable,
  deleteGlobalVariable,
  getGlobalVariable,
  getGlobalVariableByCode,
  getGlobalVariableChoices,
  getGlobalVariableSourceOptions,
  listGlobalVariables,
  updateGlobalVariable,
} from "@/api/testing/global_variables";

describe("global_variables wrappers", () => {
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

  it("list — GET на список с query", async () => {
    await listGlobalVariables({ limit: 10, offset: 5 });
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/global-variables", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("get by code — encode'ит код в пути", async () => {
    await getGlobalVariableByCode("TEST USER");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/global-variables/by-code/TEST%20USER",
    );
  });

  it("get by id", async () => {
    await getGlobalVariable("gvar_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/global-variables/gvar_1");
  });

  it("choices — прокидывает произвольные query-параметры резолвера", async () => {
    await getGlobalVariableChoices("gvar_1", { os_version_id: "osv_1" });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/global-variables/gvar_1/choices",
      { query: { os_version_id: "osv_1" } },
    );
  });

  it("create — POST с телом", async () => {
    const body = { code: "RC", label: "РЦ", source: "launch_context" as const };
    await createGlobalVariable(body);
    expect(apiPostMock).toHaveBeenCalledWith("/testing/v1/global-variables", body);
  });

  it("update — PATCH по id", async () => {
    await updateGlobalVariable("gvar_1", { label: "new" });
    expect(apiPatchMock).toHaveBeenCalledWith("/testing/v1/global-variables/gvar_1", {
      label: "new",
    });
  });

  it("delete — DELETE по id", async () => {
    await deleteGlobalVariable("gvar_1");
    expect(apiDeleteMock).toHaveBeenCalledWith("/testing/v1/global-variables/gvar_1");
  });

  it("source-options — GET справочника форм source_ref", async () => {
    await getGlobalVariableSourceOptions();
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/global-variables/source-options");
  });
});
