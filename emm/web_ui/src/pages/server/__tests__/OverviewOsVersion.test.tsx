import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { OsVersion, Server } from "@/api/server/types";

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => false),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

const OS_VERSION: OsVersion = {
  id: "osv_1",
  name: "1.8.1.6",
  description: null,
  repositories: [],
  discovered_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [OS_VERSION], total: 1, limit: 200, offset: 0 }),
  ),
}));
vi.mock("@/api/server/servers", () => ({
  updateServer: vi.fn(),
}));

import { OverviewTab } from "@/pages/server/tabs/overview";

function baseServer(): Server {
  return {
    id: "srv_1",
    hostname: "host-01",
    display_name: "Host 01",
    number: null,
    ip_address: "10.0.0.10",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: "osv_1",
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
    is_managed: false,
    management_user: null,
    prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
  };
}

function renderOverview(server: Server) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <OverviewTab serverId={server.id} server={server} />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("OverviewTab — версия ОС + режим защищённости", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("склеивает версию и режим: «1.8.1.6 Smolensk»", async () => {
    renderOverview({ ...baseServer(), os_security_mode: "Smolensk" });
    expect(await screen.findByText("1.8.1.6 Smolensk")).toBeInTheDocument();
  });

  it("без режима показывает только версию", async () => {
    renderOverview({ ...baseServer(), os_security_mode: null });
    expect(await screen.findByText("1.8.1.6")).toBeInTheDocument();
    expect(screen.queryByText(/Smolensk/)).not.toBeInTheDocument();
  });
});
