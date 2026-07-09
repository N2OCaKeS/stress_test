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
  createDefaultVms,
  createVm,
  createVmsBulk,
  createVmDisk,
  createVmIpPool,
  createVmPreset,
  createVmSnapshot,
  deleteVm,
  deleteVmDisk,
  deleteVmIpPool,
  deleteVmPreset,
  deleteVmSnapshot,
  getAvailableIps,
  getVmByNumber,
  getServerByNumber,
  listVmAccounts,
  listVmDisks,
  listVmImages,
  listVmIpPools,
  listVmPackages,
  listVmPresets,
  listVmSnapshots,
  openVmConsole,
  vmConsoleViewerUrl,
  prepareVm,
  prepareVmsHub,
  refreshVmImages,
  resizeVmDisk,
  revertVmSnapshot,
  rotateVmMgmtCreds,
  setVmAutostart,
  setVmCredStrategy,
  setVmNetwork,
  teardownVmsHub,
  updateVm,
  updateVmIpPool,
  updateVmPreset,
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
      department_id: "dep-x",
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

  it("createVmsBulk POST'ит /vms/bulk с items", async () => {
    const items = [
      {
        hub_server_id: "srv-07",
        department_id: "dep-x",
        name: "vm-a",
        hostname: "host-a",
        cpu: 2,
        ram_mb: 4096,
        disk_gb: 40,
        box: "vm_station",
        network_mode: "bridge" as const,
        autostart: true,
        cred_strategy: "per_snapshot" as const,
        accounts: ["acc-1", "acc-2"],
      },
      {
        hub_server_id: "srv-07",
        department_id: "dep-x",
        name: "vm-b",
        cpu: 4,
        ram_mb: 8192,
        disk_gb: 60,
        box: "vm_station",
        network_mode: "nat" as const,
        accounts: [],
      },
    ];
    await createVmsBulk({ items });
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/bulk", { items });
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
      snapshot_type: "disk_only" as const,
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

  it("prepareVm POST'ит /vms/{id}/prepare", async () => {
    const res = await prepareVm("vm-103");
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-103/prepare");
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("rotateVmMgmtCreds POST'ит /vms/{id}/mgmt-creds/rotate", async () => {
    await rotateVmMgmtCreds("vm-101");
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/vms/vm-101/mgmt-creds/rotate",
    );
  });

  it("setVmNetwork POST'ит /vms/{id}/network с телом", async () => {
    const body = {
      network_mode: "bridge" as const,
      ip_address: "10.177.103.55",
      pool_id: "pool-core",
    };
    await setVmNetwork("vm-101", body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/network", body);
  });

  it("getAvailableIps GET'ит /vms/available-ips с pool_id", async () => {
    await getAvailableIps("pool-core");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/available-ips", {
      query: { pool_id: "pool-core" },
    });
  });

  it("listVmIpPools GET'ит /vm-ip-pools с фильтрами", async () => {
    await listVmIpPools({ department_id: "core" });
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vm-ip-pools", {
      query: { department_id: "core", server_id: undefined },
    });
  });

  it("createVmIpPool POST'ит /vm-ip-pools с телом", async () => {
    const body = {
      name: "core-lan",
      cidr: "10.177.103.0/24",
      gateway: "10.177.103.1",
      netmask: "255.255.255.0",
      dns: ["10.177.100.10"],
      range_start: "10.177.103.50",
      range_end: "10.177.103.99",
      department_id: "core",
      server_id: null,
    };
    await createVmIpPool(body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vm-ip-pools", body);
  });

  it("updateVmIpPool PATCH'ит /vm-ip-pools/{id}", async () => {
    await updateVmIpPool("pool-core", { range_end: "10.177.103.120" });
    expect(apiPatch).toHaveBeenCalledWith("/server/v1/vm-ip-pools/pool-core", {
      range_end: "10.177.103.120",
    });
  });

  it("deleteVmIpPool DELETE'ит /vm-ip-pools/{id}", async () => {
    await deleteVmIpPool("pool-core");
    expect(apiDelete).toHaveBeenCalledWith("/server/v1/vm-ip-pools/pool-core");
  });

  it("пресеты: list / create / update / delete", async () => {
    await listVmPresets({ department_id: "core" });
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vm-presets", {
      query: { department_id: "core" },
    });
    const body = {
      name: "core-rc",
      department_id: "core",
      box: "vm_station",
      cpu: 4,
      ram_mb: 8192,
      disk_gb: 80,
      network_mode: "bridge" as const,
      fixed_ip: "10.177.103.60",
      number: 900,
    };
    await createVmPreset(body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vm-presets", body);
    await updateVmPreset("preset-1", { cpu: 8 });
    expect(apiPatch).toHaveBeenCalledWith("/server/v1/vm-presets/preset-1", {
      cpu: 8,
    });
    await deleteVmPreset("preset-1");
    expect(apiDelete).toHaveBeenCalledWith("/server/v1/vm-presets/preset-1");
  });

  it("createDefaultVms POST'ит create-default-vms сервера", async () => {
    const res = await createDefaultVms("srv-07");
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/servers/srv-07/create-default-vms",
    );
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("setVmAutostart POST'ит /vms/{id}/autostart с enabled", async () => {
    await setVmAutostart("vm-101", true);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/autostart", {
      enabled: true,
    });
  });

  it("teardownVmsHub DELETE'ит /servers/{id}/vms-hub с reason", async () => {
    await teardownVmsHub("srv-07", { reason: "reclaim" });
    expect(apiDelete).toHaveBeenCalledWith("/server/v1/servers/srv-07/vms-hub", {
      reason: "reclaim",
    });
  });

  it("openVmConsole POST'ит /vms/{id}/console с kind", async () => {
    await openVmConsole("vm-101", "vnc");
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms/vm-101/console", {
      kind: "vnc",
    });
  });

  it("listVmAccounts GET'ит голый массив учёток ВМ", async () => {
    await listVmAccounts("vm-101");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/accounts");
  });

  it("listVmPackages без refresh GET'ит без query", async () => {
    await listVmPackages("vm-101");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/packages");
  });

  it("listVmPackages с refresh шлёт ?refresh=true", async () => {
    await listVmPackages("vm-101", { refresh: true });
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/packages", {
      query: { refresh: true },
    });
  });

  it("listVmPackages с pattern пробрасывает glob в query", async () => {
    await listVmPackages("vm-101", { refresh: true, pattern: "linux-image*" });
    expect(apiGet).toHaveBeenCalledWith("/server/v1/vms/vm-101/packages", {
      query: { refresh: true, pattern: "linux-image*" },
    });
  });

  it("vmConsoleViewerUrl берёт path+query от текущего origin, host из ws_url игнорит", () => {
    const origin = window.location.origin;
    // Абсолютный host из ws_url (может не резолвиться при заходе по IP) отброшен.
    expect(
      vmConsoleViewerUrl({
        ws_url: "wss://emm.devos.astralinux.ru/vm-console/vnc/vm-101",
        token: "sig abc",
      }),
    ).toBe(`${origin}/vm-console/vnc/vm-101?token=sig%20abc`);
    expect(
      vmConsoleViewerUrl({
        ws_url: "ws://hub.local/vm-console/spice/vm-9?x=1",
        token: "t2",
      }),
    ).toBe(`${origin}/vm-console/spice/vm-9?x=1&token=t2`);
    // Без ws_url (ssh/serial) — null.
    expect(vmConsoleViewerUrl({ ws_url: null, token: "t" })).toBeNull();
  });

  it("createVm с bridge несёт pool_id и ip_address", async () => {
    const body = {
      hub_server_id: "srv-07",
      department_id: "dep-x",
      name: "alse",
      cpu: 2,
      ram_mb: 4096,
      disk_gb: 40,
      box: "vm_station",
      network_mode: "bridge" as const,
      ip_address: "10.177.103.55",
      pool_id: "pool-core",
      number: null,
    };
    await createVm(body);
    expect(apiPost).toHaveBeenCalledWith("/server/v1/vms", body);
  });
});
