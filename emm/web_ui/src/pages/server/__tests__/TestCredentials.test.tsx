import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Persona } from "@/types/persona";
import type { Server, ServerTestCredentials } from "@/api/server/types";

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// Persona управляем напрямую — гейтим карточку через service_roles/platform_role.
let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

const META_EXISTS: ServerTestCredentials = {
  exists: true,
  username: "test_srv_1",
  ssh_public_key: "ssh-ed25519 AAAAtest test-creds",
  rotated_at: "2026-08-01T00:00:00Z",
  password_b64: null,
  ssh_private_key_b64: null,
};

const REVEALED: ServerTestCredentials = {
  ...META_EXISTS,
  password_b64: btoa("s3cr3t"),
  ssh_private_key_b64: btoa("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"),
};

const getServerTestCredentialsMock = vi.fn(
  (_id: string, opts?: { reveal?: boolean }) =>
    Promise.resolve(opts?.reveal ? REVEALED : META_EXISTS),
);

vi.mock("@/api/server/servers", () => ({
  getServerTestCredentials: (id: string, opts?: { reveal?: boolean }) =>
    getServerTestCredentialsMock(id, opts),
  astraUpdate: vi.fn(),
  clearBusy: vi.fn(),
  deleteServer: vi.fn(),
  getServer: vi.fn(() => Promise.resolve({})),
  inventorySync: vi.fn(),
  installNodeExporter: vi.fn(),
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
  getTask: vi.fn(() => Promise.resolve({ status: "running" })),
}));

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
  createOsVersion: vi.fn(),
  updateOsVersion: vi.fn(),
  deleteOsVersion: vi.fn(),
  osSync: vi.fn(),
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

describe("ManageTab — учётка теста (test-credentials)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getServerTestCredentialsMock.mockClear();
  });

  it("без права view_test_credentials блок не рендерится вовсе", async () => {
    currentPersona = persona({ service_roles: { server: "operator" } });
    renderManage(baseServer());
    // Дожидаемся отрисовки соседней карточки, чтобы не поймать ложный негатив.
    await screen.findByText("Управляющие креды");
    expect(screen.queryByText("Учётка теста")).not.toBeInTheDocument();
    expect(getServerTestCredentialsMock).not.toHaveBeenCalled();
  });

  it("server.admin видит метаданные, exists=true", async () => {
    currentPersona = persona({ service_roles: { server: "admin" } });
    renderManage(baseServer());
    expect(await screen.findByText("Учётка теста")).toBeInTheDocument();
    expect(await screen.findByText("test_srv_1")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Показать пароль и ключ/ }),
    ).toBeInTheDocument();
  });

  it("dep_admin своего департамента тоже видит блок", async () => {
    currentPersona = persona({ platform_role: "dep_admin", dept_id: "dep_1" });
    renderManage(baseServer({ department_id: "dep_1" }));
    expect(await screen.findByText("Учётка теста")).toBeInTheDocument();
  });

  it("клик «Показать пароль и ключ» дёргает reveal и раскрывает поля", async () => {
    currentPersona = persona({ service_roles: { server: "admin" } });
    renderManage(baseServer());
    const btn = await screen.findByRole("button", {
      name: /Показать пароль и ключ/,
    });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(getServerTestCredentialsMock).toHaveBeenCalledWith("srv_1", {
        reveal: true,
      }),
    );
    expect(await screen.findByText("s3cr3t")).toBeInTheDocument();
    expect(
      screen.getByText(/BEGIN OPENSSH PRIVATE KEY/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Скрыть/ }));
    expect(screen.queryByText("s3cr3t")).not.toBeInTheDocument();
  });

  it("exists=false показывает подсказку про prepare-for-test", async () => {
    currentPersona = persona({ service_roles: { server: "admin" } });
    getServerTestCredentialsMock.mockResolvedValueOnce({
      ...META_EXISTS,
      exists: false,
      username: null,
      ssh_public_key: null,
      rotated_at: null,
    });
    renderManage(baseServer());
    expect(
      await screen.findByText(/учётка ещё не выпускалась/i),
    ).toBeInTheDocument();
  });
});
