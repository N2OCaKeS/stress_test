import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Persona } from "@/types/persona";
import type { OsVersion, Server } from "@/api/server/types";

// confirm всегда «да» — чтобы клик доходил до dispatch'а.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// Persona управляем напрямую — гейтим кнопку обновления через service_roles.
let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

const OS_A: OsVersion = {
  id: "osv_orel",
  name: "Astra 1.8 Orel",
  description: null,
  repositories: ["deb http://repo/orel stable main"],
  discovered_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [OS_A], total: 1, limit: 200, offset: 0 }),
  ),
  createOsVersion: vi.fn(),
  updateOsVersion: vi.fn(),
  deleteOsVersion: vi.fn(),
  osSync: vi.fn(),
}));

const astraUpdateMock = vi.fn(
  (_id: string, _body: { os_version_id: string }) =>
    Promise.resolve({ task_id: "tsk_astra_1", status: "queued" }),
);
vi.mock("@/api/server/servers", () => ({
  astraUpdate: (id: string, body: { os_version_id: string }) =>
    astraUpdateMock(id, body),
  clearBusy: vi.fn(),
  deleteServer: vi.fn(),
  getServer: vi.fn(() => Promise.resolve({})),
  inventorySync: vi.fn(),
  prepareServer: vi.fn(),
  rotateManagementCredentials: vi.fn(),
  setBusy: vi.fn(),
}));

vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
}));

vi.mock("@/api/server/misc", () => ({
  usersInventory: vi.fn(),
  // useTaskOutcome поллит статус задачи после dispatch'а — держим её вечно
  // running, чтобы фоновой опрос не падал на немокнутом getTask.
  getTask: vi.fn(() => Promise.resolve({ status: "running" })),
}));

import { ManageTab } from "@/pages/server/tabs/manage";

function persona(overrides: Partial<Persona>): Persona {
  return {
    id: "p",
    username: "p",
    email: "p@dbos.local",
    initials: "P",
    display_name: "p",
    dept_id: "dep_1",
    platform_role: null,
    service_roles: {},
    accessible_services: ["server"],
    has_admin: false,
    tagline: "",
    ...overrides,
  } as Persona;
}

function baseServer(overrides: Partial<Server> = {}): Server {
  return {
    id: "srv_1",
    hostname: "host-01",
    display_name: "Host 01",
    ip_address: "10.0.0.10",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: null,
    os_last_synced_at: null,
    department_id: "dep_1",
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
    management_user: "dbos",
    prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
    ...overrides,
  } as Server;
}

function renderManage(server: Server) {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <MemoryRouter>
          <ManageTab serverId={server.id} server={server} />
        </MemoryRouter>
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ManageTab — обновление ОС Astra", () => {
  beforeEach(() => {
    window.localStorage.clear();
    astraUpdateMock.mockClear();
    currentPersona = persona({ service_roles: { server: "operator" } });
  });

  it("рисует карточку, выбор версии и диспатчит обновление", async () => {
    renderManage(baseServer());
    expect(
      await screen.findByText("Обновление ОС Astra"),
    ).toBeInTheDocument();

    // Дропдаун подтянул версию из каталога.
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "osv_orel" } });

    const btn = screen.getByRole("button", { name: /Обновить ОС/i });
    fireEvent.click(btn);

    await waitFor(() => expect(astraUpdateMock).toHaveBeenCalledTimes(1));
    expect(astraUpdateMock).toHaveBeenCalledWith("srv_1", {
      os_version_id: "osv_orel",
    });
  });

  it("блокирует кнопку, пока сервер в состоянии updating", async () => {
    renderManage(baseServer({ busy_state: "updating" }));
    // Баннер «идёт обновление».
    expect(
      await screen.findAllByText(/идёт обновление/i),
    ).not.toHaveLength(0);
    const btn = screen.getByRole("button", { name: /Обновление идёт/i });
    expect(btn).toBeDisabled();
  });

  it("на неподготовленном сервере предлагает сначала prepare", async () => {
    renderManage(baseServer({ is_managed: false }));
    expect(
      await screen.findByText(/сначала выполните prepare/i),
    ).toBeInTheDocument();
  });
});
