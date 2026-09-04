import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
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
    number: null,
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

describe("Server list — состав по фильтру only", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(listServers).mockResolvedValue({
      items: [mkServer({ id: "srv-alpha" }), mkServer({ id: "srv-beta" })],
      total: 2,
      limit: 200,
      offset: 0,
    });
  });

  it("only=servers показывает только сервера, группа ВМ скрыта", async () => {
    renderServer("/server?only=servers");
    expect(await screen.findByText("srv-alpha")).toBeInTheDocument();
    expect(screen.getByText("srv-beta")).toBeInTheDocument();
    expect(screen.getByText(/Серверы · 2/)).toBeInTheDocument();
    expect(screen.queryByText(/Виртуальные машины/)).not.toBeInTheDocument();
    expect(screen.queryByText("alse-1.8-rc")).not.toBeInTheDocument();
  });

  it("only=vms показывает только ВМ, группа серверов скрыта", async () => {
    renderServer("/server?only=vms");
    expect(await screen.findByText(/Виртуальные машины/)).toBeInTheDocument();
    expect(await screen.findByText("alse-1.8-rc")).toBeInTheDocument();
    expect(screen.queryByText("srv-alpha")).not.toBeInTheDocument();
    expect(screen.queryByText(/Серверы · /)).not.toBeInTheDocument();
  });

  it("общий список: обе группы видны и обе сворачиваются", async () => {
    renderServer("/server");
    // Обе группы присутствуют и развёрнуты по умолчанию.
    const serverHeader = await screen.findByText(/Серверы · 2/);
    const vmHeader = await screen.findByText(/Виртуальные машины/);
    expect(await screen.findByText("srv-alpha")).toBeInTheDocument();
    expect(await screen.findByText("alse-1.8-rc")).toBeInTheDocument();

    // Сворачиваем группу серверов — её строки исчезают, ВМ остаются.
    fireEvent.click(serverHeader.closest("button")!);
    expect(screen.queryByText("srv-alpha")).not.toBeInTheDocument();
    expect(screen.getByText("alse-1.8-rc")).toBeInTheDocument();

    // Сворачиваем группу ВМ — её строки тоже исчезают.
    fireEvent.click(vmHeader.closest("button")!);
    expect(screen.queryByText("alse-1.8-rc")).not.toBeInTheDocument();
  });

  it("заголовки групп серверов несут иконку (консистентно с группой ВМ)", async () => {
    renderServer("/server");
    const serverHeader = await screen.findByText(/Серверы · 2/);
    const vmHeader = await screen.findByText(/Виртуальные машины/);
    // У обоих заголовков есть svg-иконка в кнопке-тумблере.
    expect(
      serverHeader.closest("button")!.querySelector("svg"),
    ).not.toBeNull();
    expect(vmHeader.closest("button")!.querySelector("svg")).not.toBeNull();
  });

  it("строка сервера: IP и бейджи не наезжают (shrink-0 на блоке бейджей)", async () => {
    renderServer("/server?only=servers");
    const ip = (await screen.findAllByText("10.10.20.11"))[0];
    // IP-ячейка усечима (truncate) и не растягивает строку.
    expect(ip).toHaveClass("truncate");
    // Бейджи ping + busy собраны в неусыхаемый блок, чтобы не наезжать на IP.
    const badgeWrap = ip.closest(".flex.items-center.gap-2")!;
    const shrinkBox = within(badgeWrap as HTMLElement)
      .getByText("free")
      .closest(".shrink-0");
    expect(shrinkBox).not.toBeNull();
  });
});
