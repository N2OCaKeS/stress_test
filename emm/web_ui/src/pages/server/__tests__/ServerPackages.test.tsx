import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type {
  OffsetPaginatedResponse,
  PackagesBulkActionResponse,
  Server,
} from "@/api/server/types";
import type { Vm } from "@/api/server/vms";

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

function mkVm(id: string, name: string): Vm {
  return {
    id,
    name,
    hostname: null,
    number: 7,
    hub_server_id: "srv_a",
    department_id: "core",
    os_version: "Astra 1.8",
    box: "vm_station",
    network_mode: "nat",
    ip_address: "10.20.0.5",
    status: "free",
    power_state: "on",
    cpu: 2,
    ram_mb: 2048,
    disk_gb: 20,
    autostart: false,
    cred_strategy: "per_snapshot",
    busy_state: null,
    is_managed: true,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
  };
}

const VM_PAGE: OffsetPaginatedResponse<Vm> = {
  items: [mkVm("vm_x", "vm-one")],
  total: 1,
  limit: 200,
  offset: 0,
};

vi.mock("@/api/server/vms", () => ({
  listVms: vi.fn(() => Promise.resolve(VM_PAGE)),
  listVmPackages: vi.fn(() =>
    Promise.resolve({
      vm_id: "vm_x",
      packages: [],
      package_count: 0,
      dispatched: true,
      task_id: "vt1",
    }),
  ),
  vmPackagesAction: vi.fn(() =>
    Promise.resolve({ task_id: "vat1", status: "queued" }),
  ),
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
import { listServers } from "@/api/server/servers";
import { listVms, listVmPackages, vmPackagesAction } from "@/api/server/vms";

const installedPackagesBulkMock = vi.mocked(installedPackagesBulk);
const packagesBulkActionMock = vi.mocked(packagesBulkAction);
const listServersMock = vi.mocked(listServers);
const listVmsMock = vi.mocked(listVms);
const listVmPackagesMock = vi.mocked(listVmPackages);
const vmPackagesActionMock = vi.mocked(vmPackagesAction);

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
    listServersMock.mockClear();
    listVmsMock.mockClear();
    listVmPackagesMock.mockClear();
    vmPackagesActionMock.mockClear();
  });

  it("показывает подсказку про мультипаттерн", async () => {
    renderPage();
    await screen.findByText("host-a");
    expect(
      screen.getByPlaceholderText("ssh* bash* *libs*"),
    ).toBeInTheDocument();
  });

  it("сервер в селекторе показывается по имени, без сырого IP", async () => {
    listServersMock.mockResolvedValueOnce({
      items: [
        {
          ...mkServer("srv_named", "raw-host"),
          display_name: "Красивое имя",
          ip_address: "10.9.9.9",
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    renderPage();
    // Основной текст строки — display_name, а не hostname/IP.
    await screen.findByText("Красивое имя");
    expect(screen.queryByText("raw-host")).not.toBeInTheDocument();
    expect(screen.queryByText("10.9.9.9")).not.toBeInTheDocument();
  });

  it("без display_name сервер в селекторе показывается по hostname", async () => {
    renderPage();
    // display_name у host-a/host-b пуст — падаем на hostname, IP не выводим.
    await screen.findByText("host-a");
    await screen.findByText("host-b");
    expect(screen.queryByText("10.10.20.11")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /Установить/ }));
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
    fireEvent.click(screen.getByRole("button", { name: /Установить/ }));
    await screen.findByText(/Укажите хотя бы один пакет/);
    expect(packagesBulkActionMock).not.toHaveBeenCalled();
  });

  it("отличает «пакет не установлен» (—) от «сервер не опрошен» (·)", async () => {
    // srv_a — опрошен, пакет есть; srv_b — опрошен, пакета нет (точно «—»);
    // srv_c — не подготовлен, опроса не было (пустота ≠ «не установлен»).
    installedPackagesBulkMock.mockResolvedValueOnce({
      pattern: "*",
      requested: 3,
      dispatched: 1,
      results: [
        {
          server_id: "srv_a",
          hostname: "host-a",
          os_version_id: "osv_1",
          status: "ok",
          task_id: null,
          packages: [{ name: "htop", version: "1.2" }],
        },
        {
          server_id: "srv_b",
          hostname: "host-b",
          os_version_id: "osv_1",
          status: "ok",
          task_id: null,
          packages: [],
        },
        {
          server_id: "srv_c",
          hostname: "host-c",
          os_version_id: "osv_1",
          status: "prepare_required",
          task_id: null,
          packages: [],
        },
      ],
    });

    renderPage();
    await screen.findByText("host-a");
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByRole("button", { name: /Запросить/ }));

    const table = await screen.findByRole("table");
    // Версия отображается как есть.
    expect(within(table).getByText("1.2")).toBeInTheDocument();
    // Ровно один «—» — опрошенный srv_b без пакета. srv_c (не опрошен) под
    // маркер «не установлен» не попадает.
    expect(within(table).getAllByText("—")).toHaveLength(1);
    // Неопрошенный сервер показан маркером «·».
    expect(within(table).getByText("·")).toBeInTheDocument();

    // Транспонированный режим — те же инварианты.
    fireEvent.click(screen.getByRole("button", { name: /серверы × пакеты/ }));
    const table2 = await screen.findByRole("table");
    expect(within(table2).getAllByText("—")).toHaveLength(1);
    expect(within(table2).getByText("·")).toBeInTheDocument();
  });

  it("ВМ показывается в селекторе отдельной секцией", async () => {
    renderPage();
    await screen.findByText("vm-one");
    // Секция ВМ подписана и содержит имя ВМ.
    expect(screen.getByText("ВМ (1)")).toBeInTheDocument();
    // Серверная секция никуда не делась.
    expect(screen.getByText("Серверы (2)")).toBeInTheDocument();
  });

  it("выбор ВМ ставит per-VM probe пакетов, а не серверный bulk", async () => {
    renderPage();
    await screen.findByText("vm-one");
    // Чекбоксы: два сервера, затем ВМ последней.
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[checkboxes.length - 1]);
    fireEvent.change(screen.getByPlaceholderText("ssh* bash* *libs*"), {
      target: { value: "linux-image*" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Запросить/ }));
    await waitFor(() =>
      expect(listVmPackagesMock).toHaveBeenCalledTimes(1),
    );
    expect(listVmPackagesMock.mock.calls[0][0]).toBe("vm_x");
    expect(listVmPackagesMock.mock.calls[0][1]).toMatchObject({
      refresh: true,
      pattern: "linux-image*",
    });
    // Серверный bulk не дёргается, раз выбрана только ВМ.
    expect(installedPackagesBulkMock).not.toHaveBeenCalled();
  });

  it("action по ВМ шлёт vmPackagesAction, а не серверный bulk", async () => {
    renderPage();
    await screen.findByText("vm-one");
    // Выбираем только ВМ (последний чекбокс).
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[checkboxes.length - 1]);
    const pkgInput = screen.getByPlaceholderText("htop nginx git");
    fireEvent.change(pkgInput, { target: { value: "htop" } });
    fireEvent.click(screen.getByRole("button", { name: /Установить/ }));
    await waitFor(() =>
      expect(vmPackagesActionMock).toHaveBeenCalledTimes(1),
    );
    expect(vmPackagesActionMock.mock.calls[0][0]).toBe("vm_x");
    expect(vmPackagesActionMock.mock.calls[0][1]).toEqual({
      action: "install",
      packages: ["htop"],
    });
    // Серверный bulk-action не дёргается — выбрана только ВМ.
    expect(packagesBulkActionMock).not.toHaveBeenCalled();
  });

  it("action по серверу и ВМ вместе шлёт оба клиента", async () => {
    renderPage();
    await screen.findByText("vm-one");
    const checkboxes = screen.getAllByRole("checkbox");
    // Первый сервер + ВМ.
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[checkboxes.length - 1]);
    const pkgInput = screen.getByPlaceholderText("htop nginx git");
    fireEvent.change(pkgInput, { target: { value: "htop" } });
    fireEvent.click(screen.getByRole("button", { name: /Установить/ }));
    await waitFor(() =>
      expect(vmPackagesActionMock).toHaveBeenCalledTimes(1),
    );
    expect(packagesBulkActionMock).toHaveBeenCalledTimes(1);
    expect(
      (packagesBulkActionMock.mock.calls[0][0] as { server_ids: string[] })
        .server_ids,
    ).toEqual(["srv_a"]);
  });
});
