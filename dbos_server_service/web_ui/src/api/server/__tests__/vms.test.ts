import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
  apiPost: vi.fn(() => Promise.resolve({ task_id: "task-1", status: "queued" })),
  apiPatch: vi.fn(() => Promise.resolve({})),
  apiDelete: vi.fn(() => Promise.resolve({ task_id: "task-2", status: "queued" })),
}));

import { apiDelete, apiGet, apiPost } from "@/api/client";
import {
  createVm,
  deleteVm,
  getVmByNumber,
  getServerByNumber,
  prepareVmsHub,
  vmPower,
} from "@/api/server/vms";

describe("vms api client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("createVm POST'ит /server/v1/vms с телом", async () => {
    const body = {
      hub_server_id: "srv-07",
      name: "alse-1.8",
      cpu: 4,
      ram_mb: 8192,
      disk_gb: 80,
      box: "vm_station",
      network_mode: "bridge" as const,
      ip_address: null,
      number: 101,
    };
    const res = await createVm(body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms", body);
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("vmPower шлёт action в /vms/{id}/power", async () => {
    await vmPower("vm-101", "reboot");
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/power", {
      action: "reboot",
    });
  });

  it("prepareVmsHub POST'ит prepare-vms-hub сервера", async () => {
    await prepareVmsHub("srv-31");
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/servers/srv-31/prepare-vms-hub",
    );
  });

  it("deleteVm шлёт reason и уходит на /vms/{id}", async () => {
    await deleteVm("vm-101", { reason: "cleanup" });
    expect(apiDelete).toHaveBeenCalledWith("/server/v1/vms/vm-101", {
      reason: "cleanup",
    });
  });

  it("lookup по номеру: ВМ и сервер", async () => {
    await getVmByNumber(101);
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/by-number/101");
    await getServerByNumber(5);
    expect(apiGet).toHaveBeenCalledWith("/server/v1/servers/by-number/5");
  });
});
