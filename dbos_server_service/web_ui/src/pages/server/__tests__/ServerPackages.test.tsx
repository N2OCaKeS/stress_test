import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type {
  OffsetPaginatedResponse,
  PackagesBulkActionResponse,
  Server,
} from "@/api/server/types";

// Подтверждение деструктива — confirm всегда «да», чтобы Remove/Update
// доходили до dispatch'а в тесте.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

function mkServer(id: string, hostname: string): Server {
  return {
    id,
    hostname,
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

const SERVER_PAGE: OffsetPaginatedResponse<Server> = {
  items: [mkServer("srv_a", "host-a"), mkServer("srv_b", "host-b")],
  total: 2,
  limit: 200,
  offset: 0,
};

vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => Promise.resolve(SERVER_PAGE)),
}));

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
}));

const ACTION_RESPONSE: PackagesBulkActionResponse = {
  action: "install",
  packages: ["htop"],
  requested: 1,
  dispatched: 1,
  results: [{ server_id: "srv_a", hostname: "host-a", status: "ok", task_id: "t1" }],
};

vi.mock("@/api/server/misc", () => ({
  installedPackagesBulk: vi.fn(() =>
    Promise.resolve({ pattern: "*", requested: 0, dispatched: 0, results: [] }),
  ),
  packagesBulkAction: vi.fn(() => Promise.resolve(ACTION_RESPONSE)),
  // getTask висит — нам важен сам dispatch, не доезд поллинга.
  getTask: vi.fn(() => new Promise(() => {})),
}));

import { ServerPackages } from "@/pages/server/ServerPackages";
import {
  installedPackagesBulk,
  packagesBulkAction,
} from "@/api/server/misc";

const installedPackagesBulkMock = vi.mocked(installedPackagesBulk);
const packagesBulkActionMock = vi.mocked(packagesBulkAction);

function renderPage() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={["/server/packages"]}>
            <ServerPackages />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("ServerPackages", () => {
  beforeEach(() => {
    window.localStorage.clear();
    installedPackagesBulkMock.mockClear();
    packagesBulkActionMock.mockClear();
  });

  it("показывает подсказку про мультипаттерн", async () => {
    renderPage();
    await screen.findByText("host-a");
    expect(
      screen.getByPlaceholderText("ssh* bash* *libs*"),
    ).toBeInTheDocument();
  });

  it("шлёт patterns (split по пробелам) в installedPackagesBulk", async () => {
    renderPage();
    await screen.findByText("host-a");
    // Выбираем оба сервера.
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.change(screen.getByPlaceholderText("ssh* bash* *libs*"), {
      target: { value: "ssh*  bash*   *libs*" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Запросить/ }));
    await waitFor(() => expect(installedPackagesBulkMock).toHaveBeenCalledTimes(1));
    const body = installedPackagesBulkMock.mock.calls[0][0] as {
      server_ids: string[];
      patterns?: string[];
    };
    expect(body.patterns).toEqual(["ssh*", "bash*", "*libs*"]);
    expect(body.server_ids.sort()).toEqual(["srv_a", "srv_b"]);
  });

  it("dep_admin видит блок действий и ставит install с пакетами", async () => {
    renderPage();
    await screen.findByText("host-a");
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    // Блок действий с пакетами доступен под dep_admin.
    const pkgInput = screen.getByPlaceholderText("htop nginx git");
    fireEvent.change(pkgInput, { target: { value: "htop nginx" } });
    fireEvent.click(screen.getByRole("button", { name: /Install/ }));
    await waitFor(() => expect(packagesBulkActionMock).toHaveBeenCalledTimes(1));
    const body = packagesBulkActionMock.mock.calls[0][0] as {
      action: string;
      packages?: string[];
      server_ids: string[];
    };
    expect(body.action).toBe("install");
    expect(body.packages).toEqual(["htop", "nginx"]);
    expect(body.server_ids).toEqual(["srv_a"]);
  });

  it("install без пакетов не диспатчится (требуется список)", async () => {
    renderPage();
    await screen.findByText("host-a");
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByRole("button", { name: /Install/ }));
    await screen.findByText(/Укажите хотя бы один пакет/);
    expect(packagesBulkActionMock).not.toHaveBeenCalled();
  });
});
