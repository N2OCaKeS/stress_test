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
  getDepartmentIntegrationSettings,
  upsertDepartmentIntegrationSettings,
} from "@/api/testing/departmentIntegrationSettings";

describe("departmentIntegrationSettings wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPutMock.mockReset();
    apiGetMock.mockResolvedValue({});
    apiPutMock.mockResolvedValue({});
  });

  it("get — GET по department_id", async () => {
    await getDepartmentIntegrationSettings("dep_1");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/department-integration-settings/dep_1",
    );
  });

  it("upsert — PUT с телом, без секрета", async () => {
    const body = { credential_id: "cred_1", jira_base_url: "https://jira.local" };
    await upsertDepartmentIntegrationSettings("dep_1", body);
    expect(apiPutMock).toHaveBeenCalledWith(
      "/testing/v1/department-integration-settings/dep_1",
      body,
    );
  });
});
