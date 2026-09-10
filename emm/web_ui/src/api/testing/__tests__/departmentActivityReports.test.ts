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
  generateDepartmentActivityReport,
  listDepartmentActivityReports,
} from "@/api/testing/departmentActivityReports";

describe("departmentActivityReports wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    apiPostMock.mockResolvedValue({});
  });

  it("generate — POST /generate с периодом", async () => {
    await generateDepartmentActivityReport("dep_1", { period: "2026-09" });
    expect(apiPostMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/activity-reports/generate",
      { period: "2026-09" },
    );
  });

  it("list — GET истории генераций", async () => {
    await listDepartmentActivityReports("dep_1", { limit: 20 });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/activity-reports",
      { query: { limit: 20 } },
    );
  });
});
