import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server as ServerType } from "@/api/server/types";

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => false),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// Мутируемое состояние списка серверов: разные тесты подсовывают свой набор.
const h = vi.hoisted(() => ({
  servers: [] as ServerType[],
}));

vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() =>
    Promise.resolve({
      items: h.servers,
      total: h.servers.length,
      limit: 200,
      offset: 0,
    }),
  ),
  getServer: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

// Частичный мок домена vm: serversToVmHubs и типы — настоящие, сетевые вызовы
// детерминируем (списки ВМ/образов/пулов/IP), чтобы форма ВМ рендерилась без
// реального backend'а.
vi.mock("@/api/server/vms", async (importActual) => {
  const actual = await importActual<typeof import("@/api/server/vms")>();
  return {
    ...actual,
    listVms: vi.fn(() =>
      Promise.resolve({ items: [], total: 0, limit: 500, offset: 0 }),
    ),
    listVmImages: vi.fn(() =>
      Promise.resolve({
        items: [
          {
            name: "vm_station",
            kind: "universal",
            description: "Universal-станция",
            os_versions: ["1.8.1.6"],
            min_disk_gb: 30,
          },
        ],
      }),
    ),
    refreshVmImages: vi.fn(() => Promise.resolve({ items: [] })),
    createVmsBulk: vi.fn(() => Promise.resolve({ results: [] })),
    listVmIpPools: vi.fn(() => new Promise(() => {})),
    getAvailableIps: vi.fn(() => new Promise(() => {})),
  };
});

vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() => new Promise(() => {})),
}));

vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
}));

import { Server } from "@/pages/server/Server";

function mkServer(over: Partial<ServerType> & { id: string }): ServerType {
  return {
    hostname: over.id,
    display_name: null,
    ip_address: "10.10.20.11",
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
    is_managed: false,
    management_user: null,
    prepared_at: null,
    is_vms_hub: false,
    vms_hub_prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
    ...over,
  } as ServerType;
}

function renderServer(entry = "/servers?action=new") {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[entry]}>
            <Server />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Server create — переключатель Сервер/ВМ", () => {
  beforeEach(() => {
    window.localStorage.clear();
    h.servers = [];
  });

  it("рендерит сегмент [Сервер | ВМ] и по умолчанию показывает серверную форму", () => {
    renderServer();
    // Сегмент-контрол: обе кнопки на месте.
    expect(screen.getByRole("button", { name: "Сервер" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ВМ" })).toBeInTheDocument();
    // Дефолт — серверная форма CreatePane (без регрессии старого флоу).
    expect(screen.getByText("Создание сервера")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("srv-node-01")).toBeInTheDocument();
  });

  it("переключение на ВМ при наличии нескольких хабов даёт селектор хаба и форму ВМ", async () => {
    h.servers = [
      mkServer({ id: "srv_hub_1", display_name: "kvm-hub-1", is_vms_hub: true }),
      mkServer({ id: "srv_hub_2", display_name: "kvm-hub-2", is_vms_hub: true }),
      mkServer({ id: "srv_plain", is_vms_hub: false }),
    ];
    renderServer();
    fireEvent.click(screen.getByRole("button", { name: "ВМ" }));
    // Открываем дропдаун хаба — опции рендерятся только пока он раскрыт.
    const hubLabel = (await screen.findByText("VMS-hub *")).closest("label")!;
    fireEvent.click(within(hubLabel).getByRole("button"));
    // Селектор хаба: оба подготовленных хаба — как опции.
    expect(
      await screen.findByRole("option", { name: /kvm-hub-1/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /kvm-hub-2/ })).toBeInTheDocument();
    // Переиспользуемая CreateVmPane отрендерилась для выбранного хаба.
    expect(screen.getByText(/Создание ВМ на хабе/)).toBeInTheDocument();
    // Серверной формы больше нет.
    expect(screen.queryByText("Создание сервера")).not.toBeInTheDocument();
  });

  it("переключение на ВМ без подготовленных хабов показывает подсказку", async () => {
    h.servers = [mkServer({ id: "srv_plain", is_vms_hub: false })];
    renderServer();
    fireEvent.click(screen.getByRole("button", { name: "ВМ" }));
    expect(
      await screen.findByText(/Нет подготовленных VMS-hub/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Создание ВМ на хабе/)).not.toBeInTheDocument();
  });

  it("один хаб выбирается автоматически, без селектора", async () => {
    h.servers = [
      mkServer({ id: "srv_hub_1", display_name: "kvm-hub-1", is_vms_hub: true }),
    ];
    renderServer();
    fireEvent.click(screen.getByRole("button", { name: "ВМ" }));
    expect(
      await screen.findByText(/Создание ВМ на хабе/),
    ).toBeInTheDocument();
    // Единственный хаб — селектор не нужен.
    expect(
      screen.queryByRole("option", { name: /kvm-hub-1/ }),
    ).not.toBeInTheDocument();
  });
});
