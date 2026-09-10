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
  createDepartmentReportMember,
  deleteDepartmentReportMember,
  listDepartmentReportMembers,
  updateDepartmentReportMember,
} from "@/api/testing/departmentReportMembers";

describe("departmentReportMembers wrappers", () => {
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

  it("list — GET под department_id, прокидывает is_active", async () => {
    await listDepartmentReportMembers("dep_1", { is_active: true });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/report-members",
      { query: { is_active: true } },
    );
  });

  it("create — POST под department_id", async () => {
    const body = { display_name: "Иванов И.И." };
    await createDepartmentReportMember("dep_1", body);
    expect(apiPostMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/report-members",
      body,
    );
  });

  it("update — PATCH по member_id", async () => {
    await updateDepartmentReportMember("dep_1", "drm_1", { is_active: false });
    expect(apiPatchMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/report-members/drm_1",
      { is_active: false },
    );
  });

  it("delete — DELETE по member_id", async () => {
    await deleteDepartmentReportMember("dep_1", "drm_1");
    expect(apiDeleteMock).toHaveBeenCalledWith(
      "/testing/v1/departments/dep_1/report-members/drm_1",
    );
  });
});
