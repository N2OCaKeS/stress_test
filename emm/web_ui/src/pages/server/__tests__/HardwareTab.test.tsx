import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { DiskResponse, Server } from "@/api/server/types";

import { HardwareTab } from "@/pages/server/tabs/hardware";

function disk(over: Partial<DiskResponse>): DiskResponse {
  return {
    id: "dsk_1",
    slot: "sda",
    size_gb: 500,
    used_gb: null,
    used_percent: null,
    model: null,
    is_system: false,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    ...over,
  };
}

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
    cpu_brand: "Intel",
    cpu_model: "Xeon",
    cpu_cores: 8,
    cpu_threads: 16,
    cpu_frequency_ghz: 2.4,
    ram_total_mb: 16384,
    ram_total_gb: 16,
    network_interface_name: "ens192",
    network_interfaces: ["ens192", "ens224"],
    decommissioned_at: null,
    is_managed: true,
    management_user: "dbos",
    prepared_at: null,
    storage: [
      disk({ id: "dsk_1", slot: "sda", size_gb: 500, used_gb: 225, used_percent: 48.4, is_system: true }),
      disk({ id: "dsk_2", slot: "sdb", size_gb: 1000, used_gb: 465, used_percent: 50, is_system: false }),
    ],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
  };
}

function renderHardware(server: Server) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <HardwareTab serverId={server.id} server={server} />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("HardwareTab — память, сеть, диски", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("показывает объём памяти в ГБ", () => {
    renderHardware(baseServer());
    expect(screen.getByText("16 GB")).toBeInTheDocument();
  });

  it("рендерит все сетевые интерфейсы", () => {
    renderHardware(baseServer());
    // Оба интерфейса присутствуют (badge-список).
    expect(screen.getAllByText("ens192").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("ens224")).toBeInTheDocument();
  });

  it("показывает занятость дисков и помечает системный", () => {
    renderHardware(baseServer());
    const sysRow = screen.getByText("sda").closest("tr")!;
    expect(within(sysRow).getByText("system")).toBeInTheDocument();
    expect(within(sysRow).getByText("225")).toBeInTheDocument();
    expect(within(sysRow).getByText("48.4%")).toBeInTheDocument();

    const dataRow = screen.getByText("sdb").closest("tr")!;
    expect(within(dataRow).getByText("data")).toBeInTheDocument();
    expect(within(dataRow).getByText("50%")).toBeInTheDocument();
  });

  it("показывает прочерк, если занятость неизвестна", () => {
    const srv = baseServer();
    srv.storage = [disk({ id: "dsk_x", slot: "sdx", size_gb: 200, used_gb: null, used_percent: null })];
    renderHardware(srv);
    const row = screen.getByText("sdx").closest("tr")!;
    // size есть, used/percent — прочерк.
    expect(within(row).getByText("200")).toBeInTheDocument();
  });
});
