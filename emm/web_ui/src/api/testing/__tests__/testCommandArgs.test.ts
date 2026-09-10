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
  createTestCommandArg,
  deleteTestCommandArg,
  listTestCommandArgs,
  updateTestCommandArg,
} from "@/api/testing/testCommandArgs";

describe("testCommandArgs wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiPatchMock.mockReset();
    apiDeleteMock.mockReset();
    apiGetMock.mockResolvedValue([]);
    apiPostMock.mockResolvedValue({});
    apiPatchMock.mockResolvedValue({});
    apiDeleteMock.mockResolvedValue({ ok: true });
  });

  it("list — GET по test_id, без query", async () => {
    await listTestCommandArgs("tdef_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/test-definitions/tdef_1/args");
  });

  it("create — POST с телом слота", async () => {
    const body = { kind: "literal" as const, literal_value: "--foo" };
    await createTestCommandArg("tdef_1", body);
    expect(apiPostMock).toHaveBeenCalledWith(
      "/testing/v1/test-definitions/tdef_1/args",
      body,
    );
  });

  it("update — PATCH по arg_id", async () => {
    await updateTestCommandArg("tdef_1", "targ_1", { position: 2 });
    expect(apiPatchMock).toHaveBeenCalledWith(
      "/testing/v1/test-definitions/tdef_1/args/targ_1",
      { position: 2 },
    );
  });

  it("delete — DELETE по arg_id", async () => {
    await deleteTestCommandArg("tdef_1", "targ_1");
    expect(apiDeleteMock).toHaveBeenCalledWith(
      "/testing/v1/test-definitions/tdef_1/args/targ_1",
    );
  });
});
