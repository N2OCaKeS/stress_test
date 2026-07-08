import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import type { Server as ServerType } from "@/api/server/types";

vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
}));

vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(),
  getServer: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

import { listServers } from "@/api/server/servers";
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
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
    ...over,
  };
}

function renderServer(entry: string) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={[entry]}>
              <Server />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Server list — иконка строки сервера", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(listServers).mockResolvedValue({
      items: [
        mkServer({ id: "srv-plain" }),
        mkServer({ id: "srv-hub", is_vms_hub: true }),
        mkServer({ id: "srv-dead", status: "decommissioned" }),
      ],
      total: 3,
      limit: 200,
      offset: 0,
    });
  });

  it("каждая строка сервера рендерит иконку", async () => {
    renderServer("/server?only=servers");
    await screen.findByText("srv-plain");
    const icons = screen.getAllByTestId("server-row-icon");
    expect(icons.length).toBe(3);
  });

  it("VMS-hub помечается иконкой и подписью, обычный сервер — своей", async () => {
    renderServer("/server?only=servers");
    await screen.findByText("srv-plain");
    // Подпись строки различает тип сущности.
    expect(screen.getByText("VMS-hub")).toBeInTheDocument();
    // Иконки несут осмысленный aria-label по типу/статусу.
    const labels = screen
      .getAllByTestId("server-row-icon")
      .map((el) => el.getAttribute("aria-label"));
    expect(labels).toContain("VMS-hub (несёт ВМ)");
    expect(labels).toContain("выведен из эксплуатации");
    expect(labels).toContain("сервер");
  });
});
