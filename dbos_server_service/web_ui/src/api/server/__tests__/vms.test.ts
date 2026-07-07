import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
  apiPost: vi.fn(() => Promise.resolve({ task_id: "task-1", status: "queued" })),
  apiPatch: vi.fn(() => Promise.resolve({})),
  apiDelete: vi.fn(() => Promise.resolve({ task_id: "task-2", status: "queued" })),
}));

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import {
  alltaUpdateVm,
  astraUpdateVm,
  createVm,
  createVmDisk,
  createVmSnapshot,
  deleteVm,
  deleteVmDisk,
  deleteVmSnapshot,
  getVmByNumber,
  getServerByNumber,
  listVmDisks,
  listVmImages,
  listVmSnapshots,
  prepareVmsHub,
  refreshVmImages,
  resizeVmDisk,
  revertVmSnapshot,
  setVmCredStrategy,
  updateVm,
  vmPasswd,
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

  it("listVmSnapshots GET'ит /vms/{id}/snapshots", async () => {
    await listVmSnapshots("vm-101");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/snapshots");
  });

  it("createVmSnapshot POST'ит /vms/{id}/snapshots с телом", async () => {
    const body = {
      name: "pre-regress",
      description: "перед прогоном",
      kind: "disk_only" as const,
    };
    const res = await createVmSnapshot("vm-101", body);
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/snapshots",
      body,
    );
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("revertVmSnapshot POST'ит /vms/{id}/snapshots/{snap}/revert", async () => {
    await revertVmSnapshot("vm-101", "snap-9");
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/snapshots/snap-9/revert",
    );
  });

  it("deleteVmSnapshot DELETE'ит /vms/{id}/snapshots/{snap}", async () => {
    await deleteVmSnapshot("vm-101", "snap-9");
    expect(apiDelete).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/snapshots/snap-9",
    );
  });

  it("astraUpdateVm POST'ит /vms/{id}/astra-update с rc", async () => {
    await astraUpdateVm("vm-101", { rc: "1.8.1.6" });
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/astra-update", {
      rc: "1.8.1.6",
    });
  });

  it("alltaUpdateVm POST'ит /vms/{id}/allta-update", async () => {
    await alltaUpdateVm("vm-101");
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/allta-update");
  });

  it("vmPasswd POST'ит /vms/{id}/passwd с паролем", async () => {
    await vmPasswd("vm-101", { password: "s3cret-pass" });
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/passwd", {
      password: "s3cret-pass",
    });
  });

  it("setVmCredStrategy PATCH'ит /vms/{id}/cred-strategy", async () => {
    await setVmCredStrategy("vm-101", "reroll");
    expect(apiPatch).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/cred-strategy",
      { cred_strategy: "reroll" },
    );
  });
});
