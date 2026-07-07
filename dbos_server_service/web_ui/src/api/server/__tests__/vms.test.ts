import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
  apiPost: vi.fn(() => Promise.resolve({ task_id: "task-1", status: "queued" })),
  apiPatch: vi.fn(() => Promise.resolve({})),
  apiDelete: vi.fn(() => Promise.resolve({ task_id: "task-2", status: "queued" })),
}));

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import {
  createVm,
  createVmDisk,
  deleteVm,
  deleteVmDisk,
  getVmByNumber,
  getServerByNumber,
  listVmDisks,
  listVmImages,
  prepareVmsHub,
  refreshVmImages,
  resizeVmDisk,
  updateVm,
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

  it("updateVm PATCH'ит /vms/{id} с cpu/ram", async () => {
    await updateVm("vm-101", { cpu: 8, ram_mb: 16384 });
    expect(apiPatch).toHaveBeenCalledWith("/server/v1/vms/vm-101", {
      cpu: 8,
      ram_mb: 16384,
    });
  });

  it("listVmDisks GET'ит /vms/{id}/disks", async () => {
    await listVmDisks("vm-101");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/disks");
  });

  it("createVmDisk POST'ит /vms/{id}/disks с телом", async () => {
    const body = { name: "data", size_gb: 20, fs: "ext4", mount: "/data" };
    const res = await createVmDisk("vm-101", body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/disks", body);
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("deleteVmDisk шлёт reason и уходит на /vms/{id}/disks/{disk}", async () => {
    await deleteVmDisk("vm-101", "disk-9", { reason: "ui" });
    expect(apiDelete).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/disks/disk-9",
      { reason: "ui" },
    );
  });

  it("resizeVmDisk POST'ит /vms/{id}/disks/{disk}/resize", async () => {
    await resizeVmDisk("vm-101", "disk-9", { size_gb: 60 });
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/disks/disk-9/resize",
      { size_gb: 60 },
    );
  });

  it("каталог образов: list и refresh", async () => {
    await listVmImages();
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vm-images");
    await refreshVmImages();
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vm-images/refresh");
  });
});
