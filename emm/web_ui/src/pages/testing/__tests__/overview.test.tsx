import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import type { RunsState } from "@/pages/testing/runs";

const listTestStandsMock = vi.fn();
const getTestStandMock = vi.fn();
const getCurrentQueueItemMock = vi.fn();
const createTestStandMock = vi.fn();
const updateTestStandMock = vi.fn();
const deleteTestStandMock = vi.fn();
const getTestStandCredentialsMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...a: unknown[]) => listTestStandsMock(...a),
  getTestStand: (...a: unknown[]) => getTestStandMock(...a),
  getCurrentQueueItem: (...a: unknown[]) => getCurrentQueueItemMock(...a),
  createTestStand: (...a: unknown[]) => createTestStandMock(...a),
  updateTestStand: (...a: unknown[]) => updateTestStandMock(...a),
  deleteTestStand: (...a: unknown[]) => deleteTestStandMock(...a),
  getTestStandCredentials: (...a: unknown[]) => getTestStandCredentialsMock(...a),
}));

const listOsVersionsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...a: unknown[]) => listOsVersionsMock(...a),
}));

const listServersMock = vi.fn();
vi.mock("@/api/server/servers", () => ({
  listServers: (...a: unknown[]) => listServersMock(...a),
}));

const getPoolOverviewMock = vi.fn();
vi.mock("@/api/testing/poolOverview", () => ({
  getPoolOverview: (...a: unknown[]) => getPoolOverviewMock(...a),
}));

const listQueueItemsMock = vi.fn();
const launchQueueItemMock = vi.fn();
const retryQueueItemMock = vi.fn();
const skipQueueItemMock = vi.fn();
const pauseQueueItemMock = vi.fn();
const resumeStandQueueMock = vi.fn();
vi.mock("@/api/testing/queueItems", () => ({
  listQueueItems: (...a: unknown[]) => listQueueItemsMock(...a),
  launchQueueItem: (...a: unknown[]) => launchQueueItemMock(...a),
  retryQueueItem: (...a: unknown[]) => retryQueueItemMock(...a),
  skipQueueItem: (...a: unknown[]) => skipQueueItemMock(...a),
  pauseQueueItem: (...a: unknown[]) => pauseQueueItemMock(...a),
  resumeStandQueue: (...a: unknown[]) => resumeStandQueueMock(...a),
}));

const listTestDefinitionsMock = vi.fn();
const getTestDefinitionMock = vi.fn();
vi.mock("@/api/testing/testDefinitions", () => ({
  listTestDefinitions: (...a: unknown[]) => listTestDefinitionsMock(...a),
  getTestDefinition: (...a: unknown[]) => getTestDefinitionMock(...a),
}));

/** `PublicQueueItem`, как его отдаёт `GET /queue-items`. */
function queueItem(overrides: Record<string, unknown> = {}) {
  return {
    id: "qi_1", test_id: "td_1", stand_id: "ts_1", test_run_id: null, retry_of_id: null,
    debug_mode: false, state: "running", interrupt_action: null,
    rc: "osv_1", kernel: "6.12.24", mode: "orel", test_code: "UB-01", test_name: "UnixBench",
    is_current: true, log_status: "available",
    created_at: "2026-09-17T10:00:00Z", started_at: "2026-09-17T10:01:00Z",
    finished_at: null, error: null,
    ...overrides,
  };
}

/** Список очереди: активные элементы первым вызовом, `paused` — вторым. */
function mockQueues(active: unknown[], paused: unknown[] = []) {
  listQueueItemsMock.mockImplementation((query: { states?: string[]; stand_id?: string }) => {
    if (query?.stand_id) return Promise.resolve({ items: [...paused, ...active], total: active.length + paused.length, limit: 200, offset: 0 });
    const wantsPaused = query?.states?.includes("paused");
    const items = wantsPaused ? paused : active;
    return Promise.resolve({ items, total: items.length, limit: 500, offset: 0 });
  });
}

function emptyPoolOverview() {
  return {
    context: "all" as const,
    test_run_id: null,
    test_run: null,
    remaining: 0,
    running: 0,
    succeeded: 0,
    failed: 0,
    stands: [],
    stand_status_counts: { recovering: 0, unreachable: 0, testing: 0, ready: 0, no_data: 0 },
    generated_at: "2026-09-15T12:00:00Z",
  };
}

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
        <ToastProvider>
          <ConfirmProvider>
            <TestingOverview runsState={runsState} />
          </ConfirmProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("TestingOverview — mock mode (demo)", () => {
  it("по умолчанию открывается концепт «Полоски», переключатель остаётся", () => {
    renderOverview();
    // «Детали» — кнопка карточки; в полосках её нет.
    expect(screen.queryByRole("button", { name: "Детали" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Полоски/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Очереди/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Карточки/ }));
    expect(screen.getAllByRole("button", { name: "Детали" }).length).toBeGreaterThan(0);
  });

  it("демо-заглушек «Старт»/«Стоп» больше нет", () => {
    renderOverview();
    expect(screen.queryByRole("button", { name: /Старт/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Стоп/ })).not.toBeInTheDocument();
  });

  it("рендерит демо-стенды и обзор пула на demo-данных", () => {
    renderOverview(fakeRunsState({ total: 42 }));
    expect(screen.getAllByText("stand1-201").length).toBeGreaterThan(0);
    expect(screen.getByText("Обзор пула")).toBeInTheDocument();
    expect(screen.getByText("Осталось выполнить")).toBeInTheDocument();
    expect(screen.getAllByText("Готов").length).toBeGreaterThan(0);
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
    createTestStandMock.mockReset();
    updateTestStandMock.mockReset();
    deleteTestStandMock.mockReset();
    getTestStandCredentialsMock.mockReset();
    listOsVersionsMock.mockReset();
    listServersMock.mockReset();
    getPoolOverviewMock.mockReset();
    listQueueItemsMock.mockReset();
    launchQueueItemMock.mockReset();
    retryQueueItemMock.mockReset();
    skipQueueItemMock.mockReset();
    pauseQueueItemMock.mockReset();
    resumeStandQueueMock.mockReset();
    listTestDefinitionsMock.mockReset();
    mockQueues([]);
    launchQueueItemMock.mockResolvedValue(queueItem({ state: "queued" }));
    skipQueueItemMock.mockResolvedValue(queueItem({ interrupt_action: "skip" }));
    pauseQueueItemMock.mockResolvedValue(queueItem({ interrupt_action: "pause" }));
    resumeStandQueueMock.mockResolvedValue(queueItem({ state: "queued" }));
    listTestDefinitionsMock.mockResolvedValue({
      items: [
        { id: "td_1", code: "UB-01", full_name: "UnixBench", category: null, owner: null, readiness: "ready", mode: "orel", department_id: "dep_1", pinned_stand_id: "ts_1", changelog_component: null, timeout_seconds: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", created_by: null },
      ],
      total: 1, limit: 500, offset: 0,
    });
    getPoolOverviewMock.mockResolvedValue(emptyPoolOverview());
    listServersMock.mockResolvedValue({
      items: [{ id: "srv_2", hostname: "stand-02", display_name: "stand-02", ip_address: "10.177.103.202" }],
      total: 1,
      limit: 500,
      offset: 0,
    });

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
      items: [{ id: "osv_1", name: "1.8.7.46", description: null, repositories: [], kernels: ["6.12.24"], is_urgent_update: false, discovered_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" }],
      total: 1,
      limit: 200,
      offset: 0,
    });
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("грузит реальные стенды через listTestStands/getTestStand и подставляет живую карточку сервера", async () => {
    renderOverview();
    expect((await screen.findAllByText("stand-live-01")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("10.177.103.201").length).toBeGreaterThan(0);
    expect(screen.queryByText("Управление стендами пула")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить стенд" })).not.toBeInTheDocument();
    expect(listTestStandsMock).toHaveBeenCalledTimes(1);
    expect(getTestStandMock).toHaveBeenCalledWith("ts_1");
  });

  it("показывает ошибку загрузки пула и позволяет повторить без панели управления", async () => {
    listTestStandsMock.mockRejectedValueOnce(new Error("pool unavailable"));
    renderOverview();
    expect(await screen.findByRole("alert")).toHaveTextContent("pool unavailable");
    expect(screen.queryByRole("button", { name: "Добавить стенд" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await screen.findAllByText("stand-live-01");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("показывает реальные агрегаты обзора пула из GET /pool-overview", async () => {
    getPoolOverviewMock.mockResolvedValue({
      context: "all",
      test_run_id: null,
      test_run: null,
      remaining: 3,
      running: 2,
      succeeded: 5,
      failed: 1,
      stands: [
        {
          stand_id: "ts_1", server_id: "srv_1", status: "recovering",
          busy_state: "acs", busy_service_name: "testing_service",
          ping_reachable: false, ping_checked_at: "2026-09-15T12:00:00Z",
        },
      ],
      stand_status_counts: { recovering: 1, unreachable: 0, testing: 0, ready: 0, no_data: 0 },
      generated_at: "2026-09-15T12:00:05Z",
    });
    renderOverview();
    await screen.findAllByText("stand-live-01");
    await waitFor(() => expect(getPoolOverviewMock).toHaveBeenCalledWith({ context: "all", test_run_id: undefined }));
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getAllByText("Восстанавливается").length).toBeGreaterThan(0);
  });

  it("переключение на «Выбранный прогон» запрашивает выбранный test_run_id", async () => {
    renderOverview(fakeRunsState({
      runs: [{
        id: "run_abc123", os_version_id: "1.8.5.46", mode: "orel", kernel: "6.1.0",
        department_id: "dep_1", test_run_stands: [], status: "running", final: false,
        created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z", created_by: null,
      }],
    }));
    await screen.findAllByText("stand-live-01");
    fireEvent.click(screen.getByRole("button", { name: "Выбранный прогон" }));
    fireEvent.click(screen.getByRole("button", { name: /Прогон/ }));
    fireEvent.click(screen.getByText(/run_abc123/));
    await waitFor(() =>
      expect(getPoolOverviewMock).toHaveBeenCalledWith({ context: "run", test_run_id: "run_abc123" }),
    );
  });

  it("у стенда с активным item'ом есть «Пропустить»/«Остановить», но нет «Продолжить»", async () => {
    mockQueues([queueItem()]);
    renderOverview();
    expect(await screen.findByRole("button", { name: /Пропустить/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Остановить/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Продолжить/ })).not.toBeInTheDocument();
  });

  it("«Пропустить» зовёт skip по id активного item'а", async () => {
    mockQueues([queueItem({ id: "qi_42" })]);
    renderOverview();
    fireEvent.click(await screen.findByRole("button", { name: /Пропустить/ }));
    await waitFor(() => expect(skipQueueItemMock).toHaveBeenCalledWith("qi_42"));
    expect(pauseQueueItemMock).not.toHaveBeenCalled();
  });

  it("«Остановить» зовёт pause по id активного item'а", async () => {
    mockQueues([queueItem({ id: "qi_43" })]);
    renderOverview();
    fireEvent.click(await screen.findByRole("button", { name: /Остановить/ }));
    await waitFor(() => expect(pauseQueueItemMock).toHaveBeenCalledWith("qi_43"));
  });

  it("пока interrupt_action не снят — кнопки дизейблены и виден статус «Останавливается…»", async () => {
    mockQueues([queueItem({ interrupt_action: "skip" })]);
    renderOverview();
    expect(await screen.findByText("Останавливается…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Пропустить/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Остановить/ })).toBeDisabled();
  });

  it("остановленный стенд показывает только «Продолжить» и зовёт resume-queue по id стенда", async () => {
    mockQueues([], [queueItem({ id: "qi_50", state: "paused" })]);
    renderOverview();
    const resume = await screen.findByRole("button", { name: /Продолжить/ });
    expect(screen.queryByRole("button", { name: /Пропустить/ })).not.toBeInTheDocument();
    fireEvent.click(resume);
    await waitFor(() => expect(resumeStandQueueMock).toHaveBeenCalledWith("ts_1"));
  });

  it("бэкенд без состояния paused не роняет страницу", async () => {
    listQueueItemsMock.mockImplementation((query: { states?: string[] }) =>
      query?.states?.includes("paused")
        ? Promise.reject(new Error("422 unknown state"))
        : Promise.resolve({ items: [queueItem()], total: 1, limit: 500, offset: 0 }),
    );
    renderOverview();
    expect(await screen.findByRole("button", { name: /Пропустить/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Продолжить/ })).not.toBeInTheDocument();
  });

  it("«Добавить в очередь» ставит каждый выбранный тест отдельным POST /queue-items", async () => {
    mockQueues([]);
    renderOverview();
    await screen.findAllByText("stand-live-01");
    fireEvent.click(screen.getByRole("button", { name: /Запустить тест/ }));
    await screen.findByText("Тесты, закреплённые за стендом");
    fireEvent.click(screen.getByRole("button", { name: "Добавить в очередь" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledTimes(1));
    expect(launchQueueItemMock).toHaveBeenCalledWith(
      expect.objectContaining({
        test_id: "td_1", stand_id: "ts_1", os_version_id: "osv_1",
        kernel: "6.12.24", debug_mode: false,
      }),
    );
  });

  it("очередь стенда в модалке — реальный список без drag-and-drop и удаления", async () => {
    mockQueues([queueItem({ id: "qi_60" })], []);
    renderOverview();
    fireEvent.click((await screen.findAllByRole("button", { name: /Очередь/ }))[0]);
    await waitFor(() =>
      expect(listQueueItemsMock).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "all", stand_id: "ts_1", order: "desc" }),
      ),
    );
    expect(await screen.findByText("qi_60")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить/ })).not.toBeInTheDocument();
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
