import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import type { RunsState } from "@/pages/testing/runs";

const listTestStandsMock = vi.fn();
const getTestStandMock = vi.fn();
const getCurrentQueueItemMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...a: unknown[]) => listTestStandsMock(...a),
  getTestStand: (...a: unknown[]) => getTestStandMock(...a),
  getCurrentQueueItem: (...a: unknown[]) => getCurrentQueueItemMock(...a),
}));

const listOsVersionsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...a: unknown[]) => listOsVersionsMock(...a),
}));

const getHostDiskUsageMock = vi.fn();
vi.mock("@/api/server/misc", () => ({
  getHostDiskUsage: (...a: unknown[]) => getHostDiskUsageMock(...a),
}));

import { TestingOverview } from "@/pages/testing/overview";

function fakeRunsState(overrides: Partial<RunsState> = {}): RunsState {
  return {
    runs: [],
    total: 7,
    loading: false,
    error: null,
    refetch: () => {},
    search: "",
    setSearch: () => {},
    statusFilter: "all",
    setStatusFilter: () => {},
    sortDir: "desc",
    toggleSort: () => {},
    selectedId: "",
    setSelectedId: () => {},
    selectedRun: null,
    ...overrides,
  };
}

function renderOverview(runsState: RunsState = fakeRunsState()) {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <TestingOverview runsState={runsState} />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("TestingOverview — mock mode (demo)", () => {
  it("рендерит демо-стенды и общее число прогонов из runsState", () => {
    renderOverview(fakeRunsState({ total: 42 }));
    expect(screen.getAllByText("stand1-201").length).toBeGreaterThan(0);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("«Запустить тест» показывает Debug режим, а не Dev режим", () => {
    renderOverview();
    fireEvent.click(screen.getByRole("button", { name: /Запустить тест/ }));
    expect(screen.getByText("Debug режим")).toBeInTheDocument();
    expect(screen.queryByText("Dev режим")).not.toBeInTheDocument();

    // Стенд без своих закреплённых тестов (TEST_CATALOG) — только debug режим
    // открывает доступ к общему каталогу.
    fireEvent.click(screen.getByRole("button", { name: "stand1-201" }));
    const options = screen.getAllByText("stand10-105");
    fireEvent.click(options[options.length - 1]);
    expect(screen.getByText(/Включите debug режим/)).toBeInTheDocument();

    const debugCheckbox = screen.getByRole("checkbox", { name: /Debug режим/ });
    fireEvent.click(debugCheckbox);
    expect(screen.getByText("Все тесты (debug режим)")).toBeInTheDocument();
  });
});

describe("TestingOverview — live mode (testing_service)", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listTestStandsMock.mockReset();
    getTestStandMock.mockReset();
    getCurrentQueueItemMock.mockReset();
    listOsVersionsMock.mockReset();
    getHostDiskUsageMock.mockReset();

    listTestStandsMock.mockResolvedValue({
      items: [{ id: "ts_1", server_id: "srv_1", department_id: "dep_1", queue_enabled: true, is_active: true }],
      total: 1,
      limit: 500,
      offset: 0,
    });
    getTestStandMock.mockResolvedValue({
      id: "ts_1",
      server_id: "srv_1",
      department_id: "dep_1",
      queue_enabled: true,
      is_active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
      server: { hostname: "stand-live-01", ip_address: "10.177.103.201", busy_state: "testing", os_version_id: "osv_1" },
      server_unavailable: false,
    });
    getCurrentQueueItemMock.mockResolvedValue(null);
    listOsVersionsMock.mockResolvedValue({
      items: [{ id: "osv_1", name: "1.8.7.46", description: null, repositories: [], kernels: [], is_urgent_update: false, discovered_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" }],
      total: 1,
      limit: 200,
      offset: 0,
    });
    getHostDiskUsageMock.mockResolvedValue({
      paths: [{ path: "/", total_gb: 100, used_gb: 40, used_percent: 40, available: true, error: null }],
    });
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("грузит реальные стенды через listTestStands/getTestStand и подставляет живую карточку сервера", async () => {
    renderOverview();
    expect((await screen.findAllByText("stand-live-01")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("10.177.103.201").length).toBeGreaterThan(0);
    expect(listTestStandsMock).toHaveBeenCalledTimes(1);
    expect(getTestStandMock).toHaveBeenCalledWith("ts_1");
  });

  it("выводит РЦ из listOsVersions и хранилище хоста из getHostDiskUsage в дашборде", async () => {
    renderOverview();
    await screen.findAllByText("stand-live-01");
    await waitFor(() => expect(listOsVersionsMock).toHaveBeenCalled());
    await waitFor(() => expect(getHostDiskUsageMock).toHaveBeenCalled());
    // «Системный диск» — подпись реального пути "/" из getHostDiskUsage.
    expect(await screen.findByText("Системный диск")).toBeInTheDocument();
  });

  it("если сервер недоступен — стенд показывается offline", async () => {
    getTestStandMock.mockResolvedValue({
      id: "ts_1",
      server_id: "srv_1",
      department_id: "dep_1",
      queue_enabled: true,
      is_active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
      server: null,
      server_unavailable: true,
    });
    renderOverview();
    expect((await screen.findAllByText("srv_1")).length).toBeGreaterThan(0);
    const badges = screen.getAllByText("Недоступен");
    expect(badges.length).toBeGreaterThan(0);
  });
});
