import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import type { RunsState } from "@/pages/testing/runs";
import type { PoolOverviewResponse } from "@/api/testing/types";

const listTestStandsMock = vi.fn();
const getTestStandMock = vi.fn();
const getCurrentQueueItemMock = vi.fn();
const createTestStandMock = vi.fn();
const updateTestStandMock = vi.fn();
const deleteTestStandMock = vi.fn();
const getTestStandCredentialsMock = vi.fn();
const listTestStandMetricsMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...a: unknown[]) => listTestStandsMock(...a),
  getTestStand: (...a: unknown[]) => getTestStandMock(...a),
  getCurrentQueueItem: (...a: unknown[]) => getCurrentQueueItemMock(...a),
  createTestStand: (...a: unknown[]) => createTestStandMock(...a),
  updateTestStand: (...a: unknown[]) => updateTestStandMock(...a),
  deleteTestStand: (...a: unknown[]) => deleteTestStandMock(...a),
  getTestStandCredentials: (...a: unknown[]) => getTestStandCredentialsMock(...a),
  listTestStandMetrics: (...a: unknown[]) => listTestStandMetricsMock(...a),
}));

const listOsVersionsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...a: unknown[]) => listOsVersionsMock(...a),
}));

const listServersMock = vi.fn();
vi.mock("@/api/server/servers", () => ({
  listServers: (...a: unknown[]) => listServersMock(...a),
}));

const getPoolOverviewMock = vi.fn<(query?: unknown) => Promise<PoolOverviewResponse>>();
vi.mock("@/api/testing/poolOverview", () => ({
  getPoolOverview: (query?: unknown) => getPoolOverviewMock(query),
}));

const listQueueItemsMock = vi.fn();
const launchQueueItemMock = vi.fn();
const retryQueueItemMock = vi.fn();
const skipQueueItemMock = vi.fn();
const pauseQueueItemMock = vi.fn();
const resumeStandQueueMock = vi.fn();
const reorderStandQueueMock = vi.fn();
vi.mock("@/api/testing/queueItems", () => ({
  reorderStandQueue: (...a: unknown[]) => reorderStandQueueMock(...a),
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
    mode: "rolling_24h" as const,
    test_run_id: null,
    test_run: null,
    remaining: 0,
    running: 0,
    succeeded: 0,
    failed: 0,
    stands: [],
    stand_status_counts: { recovering: 0, unreachable: 0, testing: 0, testing_done: 0, ready: 0, no_data: 0 },
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

/**
 * Нажать кнопку в открытом ConfirmDialog. Ищем именно его, а не первый
 * попавшийся `role="dialog"`: confirm может всплыть поверх модалки очереди,
 * и тогда на странице два диалога сразу.
 */
async function clickInConfirm(label: RegExp) {
  const dialog = await screen.findByText(/будет принудительно убит/);
  const box = dialog.closest(".modal-content") as HTMLElement | null;
  if (!box) throw new Error("ConfirmDialog не найден");
  // Клик резолвит промис confirm'а, продолжение которого само дёргает
  // состояние — без act вокруг него React ругается на обновление вне act.
  await act(async () => {
    fireEvent.click(within(box).getByRole("button", { name: label }));
  });
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
    listTestStandMetricsMock.mockReset();
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
    listTestStandMetricsMock.mockResolvedValue({ items: [] });
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
      mode: "rolling_24h",
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
      stand_status_counts: { recovering: 1, unreachable: 0, testing: 0, testing_done: 0, ready: 0, no_data: 0 },
      generated_at: "2026-09-15T12:00:05Z",
    });
    renderOverview();
    await screen.findAllByText("stand-live-01");
    await waitFor(() => expect(getPoolOverviewMock).toHaveBeenCalledWith(undefined));
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getAllByText("Восстанавливается").length).toBeGreaterThan(0);
    // Переключателя контекста («Все задания»/«Выбранный прогон»/«Одиночное
    // тестирование») больше нет — «Прогоны» и «Одиночные запуски» это уже
    // отдельные вкладки верхнего уровня.
    expect(screen.queryByRole("button", { name: "Выбранный прогон" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Все задания" })).not.toBeInTheDocument();
  });

  it("режим «active_run» показывает заголовок текущей кампании вместо переключателя", async () => {
    getPoolOverviewMock.mockResolvedValue({
      ...emptyPoolOverview(),
      mode: "active_run",
      test_run_id: "run_abc123",
      test_run: {
        id: "run_abc123", os_version_id: "1.8.5.46", kernel: "6.1.0",
        mode: "orel", status: "running", final: false, created_at: "2026-09-15T00:00:00Z",
      },
    });
    renderOverview();
    await screen.findAllByText("stand-live-01");
    expect(await screen.findByText(/id run_abc123/)).toBeInTheDocument();
    expect(screen.getByText(/по текущему незавершённому прогону/)).toBeInTheDocument();
  });

  it("панель «Обзор пула» показывает счётчик статуса «Тестирование завершено»", async () => {
    getPoolOverviewMock.mockResolvedValue({
      ...emptyPoolOverview(),
      stands: [
        {
          stand_id: "ts_1", server_id: "srv_1", status: "testing_done",
          busy_state: "testing_done", busy_service_name: "testing_service",
          ping_reachable: true, ping_checked_at: "2026-09-15T12:00:00Z",
        },
      ],
      stand_status_counts: { recovering: 0, unreachable: 0, testing: 0, testing_done: 9, ready: 0, no_data: 0 },
    });
    renderOverview();
    await screen.findAllByText("stand-live-01");

    const label = await screen.findByText("Тестирование завершено");
    const card = label.parentElement as HTMLElement;
    expect(within(card).getByText("9")).toBeInTheDocument();
  });

  it("у стенда с активным item'ом есть «Пропустить»/«Остановить», но нет «Продолжить»", async () => {
    mockQueues([queueItem()]);
    renderOverview();
    expect(await screen.findByRole("button", { name: /Пропустить/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Остановить/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Продолжить/ })).not.toBeInTheDocument();
  });

  it("«Пропустить» зовёт skip по id активного item'а после подтверждения", async () => {
    mockQueues([queueItem({ id: "qi_42" })]);
    renderOverview();
    fireEvent.click(await screen.findByRole("button", { name: /Пропустить/ }));
    expect(await screen.findByText("Пропустить тест")).toBeInTheDocument();
    expect(skipQueueItemMock).not.toHaveBeenCalled();
    await clickInConfirm(/Пропустить/);
    await waitFor(() => expect(skipQueueItemMock).toHaveBeenCalledWith("qi_42"));
    expect(pauseQueueItemMock).not.toHaveBeenCalled();
  });

  it("«Остановить» зовёт pause по id активного item'а после подтверждения", async () => {
    mockQueues([queueItem({ id: "qi_43" })]);
    renderOverview();
    fireEvent.click(await screen.findByRole("button", { name: /Остановить/ }));
    expect(await screen.findByText("Остановить тест")).toBeInTheDocument();
    expect(pauseQueueItemMock).not.toHaveBeenCalled();
    await clickInConfirm(/Остановить/);
    await waitFor(() => expect(pauseQueueItemMock).toHaveBeenCalledWith("qi_43"));
  });

  it("отмена подтверждения не трогает API — ни skip, ни pause", async () => {
    mockQueues([queueItem({ id: "qi_44" })]);
    renderOverview();
    fireEvent.click(await screen.findByRole("button", { name: /Пропустить/ }));
    await clickInConfirm(/Отмена/);
    fireEvent.click(screen.getByRole("button", { name: /Остановить/ }));
    await clickInConfirm(/Отмена/);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(skipQueueItemMock).not.toHaveBeenCalled();
    expect(pauseQueueItemMock).not.toHaveBeenCalled();
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

  it("очередь стенда в модалке — реальный список без удаления", async () => {
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

  describe("перестановка ещё не начатых элементов очереди (D15)", () => {
    const head = () => queueItem({ id: "qi_head", test_code: "HEAD", state: "running", position: 0 });
    // Созданы в одном порядке, а `position` уже переставлены — модалка обязана идти по `position`.
    const first = () => queueItem({
      id: "qi_first", test_code: "FIRST", state: "queued", position: 1, created_at: "2026-09-17T10:05:00Z", started_at: null,
    });
    const second = () => queueItem({
      id: "qi_second", test_code: "SECOND", state: "queued", position: 2, created_at: "2026-09-17T10:02:00Z", started_at: null,
    });

    function mockStandQueue() {
      listQueueItemsMock.mockImplementation((query: { stand_id?: string; states?: string[] }) => {
        if (query?.stand_id) {
          // Как у бэкенда с `order: "desc"` — от новых к старым по `created_at`.
          return Promise.resolve({ items: [first(), second(), head()], total: 3, limit: 200, offset: 0 });
        }
        const wantsPaused = query?.states?.includes("paused");
        return Promise.resolve({ items: wantsPaused ? [] : [head()], total: 1, limit: 500, offset: 0 });
      });
    }

    async function openQueue() {
      renderOverview();
      fireEvent.click((await screen.findAllByRole("button", { name: /Очередь/ }))[0]);
      await screen.findByText("qi_second");
    }

    function queuedRowIds() {
      return screen.getAllByTestId("queue-row-queued").map((row) => within(row).getByText(/^qi_/).textContent);
    }

    beforeEach(() => {
      reorderStandQueueMock.mockReset().mockResolvedValue({ items: [] });
      mockStandQueue();
    });

    it("ещё не начатые идут после активного и в порядке position", async () => {
      await openQueue();
      expect(queuedRowIds()).toEqual(["qi_first", "qi_second"]);
      // Активный элемент перетаскивать нельзя — у него нет кнопок «Выше»/«Ниже».
      expect(screen.queryByRole("button", { name: /^Выше: HEAD/ })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^Выше: FIRST/ })).toBeDisabled();
      expect(screen.getByRole("button", { name: /^Ниже: SECOND/ })).toBeDisabled();
    });

    it("«Ниже» зовёт PATCH queue/order с новым порядком queued-элементов", async () => {
      await openQueue();
      fireEvent.click(screen.getByRole("button", { name: /^Ниже: FIRST/ }));
      await waitFor(() => expect(reorderStandQueueMock).toHaveBeenCalledWith("ts_1", ["qi_second", "qi_first"]));
    });

    it("перетаскивание queued-элемента на другой меняет порядок", async () => {
      await openQueue();
      const [rowFirst, rowSecond] = screen.getAllByTestId("queue-row-queued");
      expect(rowSecond).toHaveAttribute("draggable", "true");
      const dataTransfer = { setData: vi.fn(), getData: vi.fn(() => "qi_second"), effectAllowed: "" };
      fireEvent.dragStart(rowSecond, { dataTransfer });
      fireEvent.dragOver(rowFirst, { dataTransfer });
      fireEvent.drop(rowFirst, { dataTransfer });
      await waitFor(() => expect(reorderStandQueueMock).toHaveBeenCalledWith("ts_1", ["qi_second", "qi_first"]));
    });

    it("ошибка сервиса (очередь изменилась) показывается и очередь перечитывается", async () => {
      reorderStandQueueMock.mockRejectedValue(new Error("Очередь стенда изменилась"));
      await openQueue();
      const callsBefore = listQueueItemsMock.mock.calls.filter((args) => (args[0] as { stand_id?: string })?.stand_id).length;
      fireEvent.click(screen.getByRole("button", { name: /^Выше: SECOND/ }));
      await waitFor(() => expect(reorderStandQueueMock).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(
          listQueueItemsMock.mock.calls.filter((args) => (args[0] as { stand_id?: string })?.stand_id).length,
        ).toBeGreaterThan(callsBefore),
      );
      await waitFor(() => expect(queuedRowIds()).toEqual(["qi_first", "qi_second"]));
    });
  });

  it("очередь стенда запрашивает только активные/остановленные/проваленные состояния — не всю историю", async () => {
    mockQueues([queueItem({ id: "qi_60" })], []);
    renderOverview();
    fireEvent.click((await screen.findAllByRole("button", { name: /Очередь/ }))[0]);
    await waitFor(() => expect(listQueueItemsMock).toHaveBeenCalledWith(expect.objectContaining({ stand_id: "ts_1" })));
    const call = listQueueItemsMock.mock.calls.find((args) => (args[0] as { stand_id?: string })?.stand_id === "ts_1");
    const states = (call?.[0] as { states?: string[] })?.states ?? [];
    expect(states).toEqual(expect.arrayContaining(["running", "queued", "paused", "failed"]));
    expect(states).not.toContain("succeeded");
    expect(states).not.toContain("skipped");
  });

  it("проваленный тест, у которого уже есть retry, не висит в очереди рядом с текущей работой", async () => {
    listQueueItemsMock.mockImplementation((query: { stand_id?: string; states?: string[] }) => {
      if (query?.stand_id) {
        return Promise.resolve({
          items: [
            queueItem({ id: "qi_old_failed", state: "failed", is_current: false }),
            queueItem({ id: "qi_running", state: "running", is_current: true }),
          ],
          total: 2, limit: 200, offset: 0,
        });
      }
      const wantsPaused = query?.states?.includes("paused");
      return Promise.resolve({ items: wantsPaused ? [] : [queueItem({ id: "qi_running" })], total: 1, limit: 500, offset: 0 });
    });
    renderOverview();
    fireEvent.click((await screen.findAllByRole("button", { name: /Очередь/ }))[0]);
    expect(await screen.findByText("qi_running")).toBeInTheDocument();
    expect(screen.queryByText("qi_old_failed")).not.toBeInTheDocument();
  });

  it("«Пропустить» из модалки очереди тоже спрашивает подтверждение", async () => {
    mockQueues([queueItem({ id: "qi_61" })], []);
    renderOverview();
    fireEvent.click((await screen.findAllByRole("button", { name: /Очередь/ }))[0]);
    await screen.findByText("qi_61");
    fireEvent.click(screen.getAllByRole("button", { name: /Пропустить/ })[0]);
    expect(await screen.findByText("Пропустить тест")).toBeInTheDocument();
    expect(skipQueueItemMock).not.toHaveBeenCalled();
    await clickInConfirm(/Пропустить/);
    await waitFor(() => expect(skipQueueItemMock).toHaveBeenCalledWith("qi_61"));
  });

  it("CPU/RAM берутся из GET /test-stands/metrics, не из заглушки", async () => {
    listTestStandMetricsMock.mockResolvedValue({
      items: [{ stand_id: "ts_1", cpu_percent: 63, ram_percent: 41 }],
    });
    renderOverview();
    await screen.findAllByText("stand-live-01");
    // "63%" рендерится и в компактном метре, и в развёрнутой детальной сетке
    // (cpuUser несёт всё значение, cpuSystem — 0) — оба вхождения законны.
    expect((await screen.findAllByText("63%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("41%").length).toBeGreaterThan(0);
  });

  it("стенд без записи в /test-stands/metrics показывает CPU/RAM как 0, не выдумку", async () => {
    listTestStandMetricsMock.mockResolvedValue({ items: [] });
    renderOverview();
    await screen.findAllByText("stand-live-01");
    expect((await screen.findAllByText("0%")).length).toBeGreaterThan(0);
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

  it("ВМ-стенд показывается с признаком «ВМ» и именем ВМ", async () => {
    getTestStandMock.mockResolvedValue({
      id: "ts_1",
      target_type: "vm",
      server_id: null,
      vm_id: "vm_1",
      department_id: "dep_1",
      queue_enabled: true,
      is_active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
      server: { name: "stand6", ip_address: "10.177.120.11", busy_state: null },
      server_unavailable: false,
    });
    renderOverview();
    expect((await screen.findAllByText("stand6")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("ВМ").length).toBeGreaterThan(0);
  });
});
