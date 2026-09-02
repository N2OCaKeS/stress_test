import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type {
  PackageHistoryEntry,
  Server,
} from "@/api/server/types";
import type { PaginatedList } from "@/api/auth/users";

function mkServer(id: string): Server {
  return {
    id,
    hostname: "host-a",
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
    is_managed: true,
    management_user: null,
    prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
  };
}

const HISTORY: PackageHistoryEntry[] = [
  {
    task_id: "tsk_done",
    status: "succeeded",
    pattern: "ssh*",
    patterns: null,
    requested_by: "usr_42",
    requested_at: "2026-06-29T08:00:00Z",
    finished_at: "2026-06-29T08:01:00Z",
    package_count: 2,
    packages: [
      { name: "openssh-server", version: "1:9.2" },
      { name: "openssh-client", version: "1:9.2" },
    ],
    last_error: null,
  },
  {
    task_id: "tsk_running",
    status: "running",
    pattern: null,
    patterns: ["bash*", "*libs*"],
    requested_by: "usr_7",
    requested_at: "2026-06-29T09:00:00Z",
    finished_at: null,
    package_count: null,
    packages: null,
    last_error: null,
  },
];

const HISTORY_PAGE: PaginatedList<PackageHistoryEntry> = {
  items: HISTORY,
  total: 2,
  totalKnown: true,
};

vi.mock("@/api/server/misc", () => ({
  installedPackagesProbe: vi.fn(),
  getTask: vi.fn(() => new Promise(() => {})),
  getPackageHistory: vi.fn(() => Promise.resolve(HISTORY_PAGE)),
}));

vi.mock("@/api/server/servers", () => ({
  getServer: vi.fn(),
  installNodeExporter: vi.fn(),
  prepareServer: vi.fn(),
}));

vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
}));

import { PackagesTab } from "@/pages/server/tabs/packages";
import { getPackageHistory } from "@/api/server/misc";

const getPackageHistoryMock = vi.mocked(getPackageHistory);

function renderTab() {
  const srv = mkServer("srv_a");
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <PackagesTab serverId="srv_a" server={srv} />
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("PackagesTab history", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getPackageHistoryMock.mockClear();
  });

  it("грузит историю по серверу и показывает строки", async () => {
    renderTab();
    await waitFor(() =>
      expect(getPackageHistoryMock).toHaveBeenCalledWith("srv_a", {
        limit: 20,
        offset: 0,
      }),
    );
    expect(await screen.findByText("ssh*")).toBeInTheDocument();
    // patterns join — приоритет над одиночным pattern.
    expect(screen.getByText("bash* *libs*")).toBeInTheDocument();
    expect(screen.getByText("2 пак.")).toBeInTheDocument();
  });

  it("разворачивает завершённый запрос без повторного probe", async () => {
    renderTab();
    const row = await screen.findByText("ssh*");
    fireEvent.click(row);
    expect(await screen.findByText("openssh-server")).toBeInTheDocument();
    expect(screen.getByText("openssh-client")).toBeInTheDocument();
    // Раскрытие истории не должно дёргать историю заново.
    expect(getPackageHistoryMock).toHaveBeenCalledTimes(1);
  });

  it("помечает незавершённый запрос и не показывает пакеты", async () => {
    renderTab();
    const running = await screen.findByText("bash* *libs*");
    expect(screen.getByText("выполняется")).toBeInTheDocument();
    fireEvent.click(running);
    expect(
      await screen.findByText(/Запрос ещё выполняется/),
    ).toBeInTheDocument();
  });
});
