import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server } from "@/api/server/types";

const MOCK_SERVER: Server = {
  id: "srv_smoke_1",
  hostname: "smoke-host-01",
  display_name: "Smoke Box",
  ip_address: "10.10.20.11",
  mgmt_ip_address: null,
  ssh_port: 22,
  os_version_id: null,
  os_last_synced_at: null,
  department_id: "dep_smoke",
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
  storage: [],
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
  created_by: null,
};

vi.mock("@/api/server/servers", () => ({
  getServer: vi.fn(() => Promise.resolve(MOCK_SERVER)),
  listServers: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

// Остальные API-модули, к которым ходят табы — pending-промисы, чтобы
// рендер табов сам не падал по сети.
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/ipmi", () => ({
  getIpmi: vi.fn(() => new Promise(() => {})),
  getPowerStatus: vi.fn(() => new Promise(() => {})),
  getIpmiCredentials: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/api/server/misc", () => ({
  usersInventory: vi.fn(() => new Promise(() => {})),
  installedPackagesProbe: vi.fn(() => new Promise(() => {})),
  cancelTask: vi.fn(),
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

function renderDetail() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <ServerDetail serverId={MOCK_SERVER.id} />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("ServerDetail smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("показывает loading, пока getServer не зарезолвился", () => {
    renderDetail();
    expect(screen.getByText(/Загружаем сервер…/)).toBeInTheDocument();
  });

  it("после ресолва getServer рисует header и табы", async () => {
    renderDetail();
    // Header: display_name мокового сервера (плюс OverviewTab внутри тоже
    // его показывает — берём first match).
    const hits = await screen.findAllByText(/Smoke Box/);
    expect(hits.length).toBeGreaterThan(0);
    // Все восемь табов видны (Tabs рендерит кнопки, не role=tab).
    for (const label of [
      "Обзор",
      "Железо",
      "IPMI",
      "Аккаунты",
      "Консоль",
      "Drift",
      "Пакеты",
      "Управление",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});
