import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server } from "@/api/server/types";

// Мутируемое состояние мока: vi.hoisted, чтобы фабрики vi.mock (поднимаются в
// начало модуля) видели переприсваивание сервера между тестами.
const h = vi.hoisted(() => ({
  server: null as Server | null,
  prepareVmsHub: vi.fn(() =>
    Promise.resolve({ task_id: "task-prep-1", status: "queued" }),
  ),
  teardownVmsHub: vi.fn(() =>
    Promise.resolve({ task_id: "task-td-1", status: "queued" }),
  ),
}));

// confirm/prompt подтверждают действие — иначе клик не дойдёт до API.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: true, reason: "разбор для теста" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

vi.mock("@/api/server/vms", () => ({
  astraUpdateVm: vi.fn(),
  deleteVm: vi.fn(),
  getAvailableIps: vi.fn(() => new Promise(() => {})),
  inventorySyncVm: vi.fn(),
  installNodeExporterVm: vi.fn(),
  listVmIpPools: vi.fn(() => new Promise(() => {})),
  prepareVm: vi.fn(),
  prepareVmsHub: h.prepareVmsHub,
  releaseVm: vi.fn(),
  reserveVm: vi.fn(),
  rotateVmMgmtCreds: vi.fn(),
  setVmNetwork: vi.fn(),
  teardownVmsHub: h.teardownVmsHub,
  usersInventoryVm: vi.fn(),
}));
vi.mock("@/api/server/servers", () => ({
  getServer: vi.fn(() => Promise.resolve(h.server)),
  setBusy: vi.fn(),
  clearBusy: vi.fn(),
  astraUpdate: vi.fn(),
  deleteServer: vi.fn(),
  inventorySync: vi.fn(),
  installNodeExporter: vi.fn(),
  prepareServer: vi.fn(),
  rotateManagementCredentials: vi.fn(),
  listServers: vi.fn(() => new Promise(() => {})),
  getServerTestCredentials: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() => new Promise(() => {})),
  createOsVersion: vi.fn(),
  deleteOsVersion: vi.fn(),
  osSync: vi.fn(),
  updateOsVersion: vi.fn(),
}));
vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/misc", () => ({
  usersInventory: vi.fn(() => new Promise(() => {})),
  getTask: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/ipmi", () => ({
  getIpmi: vi.fn(() => new Promise(() => {})),
  getPowerStatus: vi.fn(() => new Promise(() => {})),
  getIpmiCredentials: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/permissions", () => ({
  listPermissions: vi.fn(() => new Promise(() => {})),
  getPermissionCatalog: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
  getDepartment: vi.fn(() => new Promise(() => {})),
}));

import { ServerDetail } from "@/pages/server/ServerDetail";

function baseServer(over: Partial<Server>): Server {
  return {
    id: "srv_hub_1",
    hostname: "kvm-host-01",
    display_name: "KVM Host",
    number: null,
    ip_address: "10.10.30.11",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: null,
    os_last_synced_at: null,
    department_id: "core",
    status: "online",
    power_state: "on",
    busy_state: "free",
    busy_user_id: null,
    busy_since: null,
    busy_note: null,
    serial_number: null,
    asset_tag: null,
    location: null,
    cpu_brand: null,
    cpu_model: null,
    cpu_cores: null,
    cpu_threads: null,
    cpu_frequency_ghz: null,
    ram_total_mb: null,
    network_interface_name: null,
    decommissioned_at: null,
    is_managed: true,
    management_user: "dbosmgr",
    prepared_at: "2026-06-11T00:00:00Z",
    virtualization: null,
    is_vms_hub: false,
    vms_hub_prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
    ...over,
  };
}

function renderDetail() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <ServerDetail serverId="srv_hub_1" />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

async function openManageTab() {
  await screen.findAllByText(/KVM Host/);
  fireEvent.click(screen.getByRole("button", { name: "Управление" }));
  await screen.findByRole("heading", { name: /Жизненный цикл/ });
}

describe("ServerDetail — VMS-hub", () => {
  beforeEach(() => {
    window.localStorage.clear();
    h.prepareVmsHub.mockClear();
    h.teardownVmsHub.mockClear();
  });

  it("для virtualization-сервера рисует «Подготовить как VMS-hub» и вызывает prepareVmsHub", async () => {
    h.server = baseServer({ virtualization: true, is_vms_hub: false });
    renderDetail();
    await openManageTab();

    const btn = await screen.findByRole("button", {
      name: /Подготовить как VMS-hub/,
    });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    await waitFor(() =>
      expect(h.prepareVmsHub).toHaveBeenCalledWith("srv_hub_1"),
    );
  });

  it("для готового hub'а показывает индикатор «VMS-hub», teardown и вызывает teardownVmsHub", async () => {
    h.server = baseServer({
      virtualization: true,
      is_vms_hub: true,
      vms_hub_prepared_at: "2026-06-11T01:00:00Z",
    });
    renderDetail();
    await openManageTab();

    // Индикатор hub'а.
    expect(await screen.findByText("VMS-hub")).toBeInTheDocument();
    // Ссылка на ВМ этого hub'а.
    const vmsLink = screen.getByRole("link", { name: /ВМ этого hub'а/ });
    expect(vmsLink).toHaveAttribute("href", "/vm?hub=srv_hub_1");
    // Разбор hub'а.
    const teardown = screen.getByRole("button", { name: /Разобрать VMS-hub/ });
    fireEvent.click(teardown);
    await waitFor(() =>
      expect(h.teardownVmsHub).toHaveBeenCalledWith("srv_hub_1", {
        reason: "разбор для теста",
      }),
    );
  });

  it("managed-сервер с virtualization=null — кнопка видна, но заблокирована (идёт проверка)", async () => {
    h.server = baseServer({ virtualization: null, is_vms_hub: false });
    renderDetail();
    await openManageTab();
    const btn = await screen.findByRole("button", {
      name: /Подготовить как VMS-hub/,
    });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Идёт проверка виртуализации…");
  });

  it("managed-сервер без KVM (virtualization=false) — кнопка заблокирована", async () => {
    h.server = baseServer({ virtualization: false, is_vms_hub: false });
    renderDetail();
    await openManageTab();
    const btn = await screen.findByRole("button", {
      name: /Подготовить как VMS-hub/,
    });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute(
      "title",
      "Нет аппаратной виртуализации (KVM) — сервер нельзя сделать VMS-hub",
    );
  });

  it("не подготовленный сервер — кнопка заблокирована (нужен prepare)", async () => {
    h.server = baseServer({
      is_managed: false,
      virtualization: null,
      is_vms_hub: false,
    });
    renderDetail();
    await openManageTab();
    const btn = await screen.findByRole("button", {
      name: /Подготовить как VMS-hub/,
    });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Сначала нужен prepare сервера");
  });
});
