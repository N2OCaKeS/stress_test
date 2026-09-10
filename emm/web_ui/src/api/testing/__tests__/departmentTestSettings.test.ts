import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGetMock = vi.fn();
const apiPutMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPut: (...a: unknown[]) => apiPutMock(...a),
  };
});

import {
  getDepartmentTestSettings,
  upsertDepartmentTestSettings,
} from "@/api/testing/departmentTestSettings";

describe("departmentTestSettings wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPutMock.mockReset();
    apiGetMock.mockResolvedValue({});
    apiPutMock.mockResolvedValue({});
  });

  it("get — GET по department_id", async () => {
    await getDepartmentTestSettings("dep_1");
    expect(apiGetMock).toHaveBeenCalledWith("/testing/v1/department-test-settings/dep_1");
  });

  it("upsert — PUT с телом", async () => {
    const body = { retry_enabled: false, test_username: "tester" };
    await upsertDepartmentTestSettings("dep_1", body);
    expect(apiPutMock).toHaveBeenCalledWith(
      "/testing/v1/department-test-settings/dep_1",
      body,
    );
  });
});
