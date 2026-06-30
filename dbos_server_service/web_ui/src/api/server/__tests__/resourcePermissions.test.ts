import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPutMock = vi.fn();
const apiDeleteMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
    apiPut: (...a: unknown[]) => apiPutMock(...a),
    apiDelete: (...a: unknown[]) => apiDeleteMock(...a),
  };
});

import {
  grantResourcePermission,
  listResourcePermissions,
  listResourcePermissionsByRole,
  propagateResourcePermissions,
  revokeResourcePermission,
} from "@/api/server/resourcePermissions";

describe("resourcePermissions wrappers", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiPutMock.mockReset();
    apiDeleteMock.mockReset();
    apiGetMock.mockResolvedValue({ items: [], total: 0 });
    apiPutMock.mockResolvedValue({});
    apiDeleteMock.mockResolvedValue(undefined);
    apiPostMock.mockResolvedValue({ targets: [] });
  });

  it("list by-resource бьёт по верному пути", async () => {
    await listResourcePermissions("server", "srv_1");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/by-resource/server/srv_1",
    );
  });

  it("list by-role прокидывает resource_type как query", async () => {
    await listResourcePermissionsByRole("operator", "server_account");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/by-role/operator",
      { query: { resource_type: "server_account" } },
    );
  });

  it("grant — PUT на полный путь с effect=allow по умолчанию", async () => {
    await grantResourcePermission("server", "srv_1", "operator", "power_reboot");
    expect(apiPutMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server/srv_1/operator/power_reboot",
      undefined,
      { query: { effect: "allow" } },
    );
  });

  it("grant — effect=deny прокидывается в query", async () => {
    await grantResourcePermission(
      "server",
      "srv_1",
      "operator",
      "power_reboot",
      "deny",
    );
    expect(apiPutMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server/srv_1/operator/power_reboot",
      undefined,
      { query: { effect: "deny" } },
    );
  });

  it("revoke — DELETE на полный путь", async () => {
    await revokeResourcePermission(
      "server_account",
      "acc_1",
      "reader",
      "view_password",
    );
    expect(apiDeleteMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server_account/acc_1/reader/view_password",
    );
  });

  it("propagate — POST с телом {target_resource_ids, mode}", async () => {
    await propagateResourcePermissions("server", "srv_src", {
      target_resource_ids: ["srv_a", "srv_b"],
      mode: "mirror",
    });
    expect(apiPostMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server/srv_src/propagate",
      { target_resource_ids: ["srv_a", "srv_b"], mode: "mirror" },
    );
  });
});
