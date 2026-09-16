import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type {
  RunSummaryComment,
  TestDefinition,
  TestRun,
  TestRunCreateResponse,
  TestRunDetail,
  TestStand,
} from "@/api/testing/types";

vi.mock("@/api/server/osVersions", () => ({ listOsVersions: async () => ({ items: [{ id: "osv_real", name: "1.7.1.44", kernels: ["6.1.1-1-generic", "6.1.1-1-lowlatency"] }] }) }));

const createTestRunMock = vi.fn();
const listTestRunsMock = vi.fn();
const getTestRunMock = vi.fn();
const getRunSummaryCommentMock = vi.fn();
vi.mock("@/api/testing/testRuns", () => ({
  createTestRun: (...args: unknown[]) => createTestRunMock(...args),
  listTestRuns: (...args: unknown[]) => listTestRunsMock(...args),
  getTestRun: (...args: unknown[]) => getTestRunMock(...args),
  getRunSummaryComment: (...args: unknown[]) => getRunSummaryCommentMock(...args),
}));

const listTestStandsMock = vi.fn();
const getTestStandMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...args: unknown[]) => listTestStandsMock(...args),
  getTestStand: (...args: unknown[]) => getTestStandMock(...args),
}));

const getTestDefinitionMock = vi.fn();
vi.mock("@/api/testing/testDefinitions", () => ({
  getTestDefinition: (...args: unknown[]) => getTestDefinitionMock(...args),
}));

const retryQueueItemMock = vi.fn();
vi.mock("@/api/testing/queueItems", () => ({ listQueueItems: async () => ({ items: [], total: 0 }), retryQueueItem: (...args: unknown[]) => retryQueueItemMock(...args) }));

const getTestLogTextMock = vi.fn();
const downloadTestLogMock = vi.fn();
vi.mock("@/api/testing/testLogs", () => ({
  listLogSegments: async () => ({ items: [], total: 0 }),
  getTestLogText: (...args: unknown[]) => getTestLogTextMock(...args),
  downloadTestLog: (...args: unknown[]) => downloadTestLogMock(...args),
}));

import { RunsMiddlePanel, RunsWorkzone, useRunsState } from "@/pages/testing/runs";

const RUN_1: TestRun = {
  id: "run_1",
  os_version_id: "1.8.7.46",
  mode: "orel",
  kernel: "6.12.24-1.el11",
  department_id: "dep_1",
  test_run_stands: ["stand_1", "stand_2"],
  status: "running",
  final: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
  created_by: "usr_1",
};

const RUN_2: TestRun = {
  ...RUN_1,
  id: "run_2",
  created_at: "2026-09-02T00:00:00Z",
  status: "succeeded",
  final: false,
  test_run_stands: ["stand_1"],
};

const DETAIL: TestRunDetail = {
  ...RUN_1,
  queue_items: [
    {
      queue_item_id: "qi_1",
      stand_id: "stand_1",
      test_id: "td_1",
      state: "running",
      is_retry: false,
      // относительное время (не фиксированная дата) — таймер тикает от
      // Date.now(), фиксированная дата в прошлом сделала бы elapsed
      // трёхзначным числом часов и сломала бы регэксп-поиск по ячейке ниже.
      started_at: new Date(Date.now() - 5_000).toISOString(),
      finished_at: null,
      error: null,
    },
    {
      queue_item_id: "qi_2",
      stand_id: "stand_2",
      test_id: "td_2",
      state: "succeeded",
      is_retry: false,
      started_at: "2026-09-10T09:00:00Z",
      finished_at: "2026-09-10T09:30:00Z",
      error: null,
    },
  ],
};

const SUMMARY: RunSummaryComment = {
  id: null,
  test_run_id: "run_1",
  status: null,
  confluence_blog_id: null,
  confluence_comment_id: null,
  stp_page_id: null,
  posted_at: null,
  updated_at: null,
};

function makeStand(id: string, name: string): TestStand {
  return {
    id,
    server_id: `srv_${id}`,
    department_id: "dep_1",
    queue_enabled: true,
    is_active: true,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
    server: { display_name: name },
    server_unavailable: false,
  };
}

function makeTestDef(id: string, fullName: string): TestDefinition {
  return {
    id,
    code: id.toUpperCase(),
    full_name: fullName,
    category: null,
    owner: null,
    readiness: null,
    mode: "orel",
    department_id: null,
    pinned_stand_id: "stand_1",
    changelog_component: null,
    timeout_seconds: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
  };
}

function Harness() {
  const state = useRunsState();
  return (
    <>
      <RunsMiddlePanel state={state} />
      <RunsWorkzone state={state} />
    </>
  );
}

function renderHarness() {
  return render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listTestRunsMock.mockResolvedValue({ items: [RUN_1, RUN_2], total: 2, limit: 200, offset: 0 });
  getTestRunMock.mockResolvedValue(DETAIL);
  getRunSummaryCommentMock.mockResolvedValue(SUMMARY);
  getTestStandMock.mockImplementation((id: string) =>
    Promise.resolve(makeStand(id, id === "stand_1" ? "stand-A" : "stand-B")),
  );
  getTestDefinitionMock.mockImplementation((id: string) =>
    Promise.resolve(makeTestDef(id, id === "td_1" ? "filesystem test" : "database test")),
  );
  listTestStandsMock.mockResolvedValue({
    items: [makeStand("stand_1", "stand-A"), makeStand("stand_2", "stand-B")],
    total: 2,
    limit: 500,
    offset: 0,
  });
  getTestLogTextMock.mockResolvedValue({ text: "log body", filename: "run_1.log" });
  downloadTestLogMock.mockResolvedValue({ text: "log body", filename: "run_1.log" });
});

describe("RunsMiddlePanel + RunsWorkzone — реальные кампании", () => {
  it("рендерит список кампаний из listTestRuns и по умолчанию выбирает верхнюю строку списка", async () => {
    renderHarness();
    expect(await screen.findByText("run_1")).toBeInTheDocument();
    expect(screen.getByText("run_2")).toBeInTheDocument();
    expect(listTestRunsMock).toHaveBeenCalledWith({ limit: 200 });
    // сортировка по умолчанию — "новые сверху" (desc по времени создания), значит верхняя
    // строка списка — run_2; деталь подтягивается сразу для неё.
    await waitFor(() => expect(getTestRunMock).toHaveBeenCalledWith("run_2"));
  });

  it("показывает дочерние queue_items с реальными именами стендов/тестов и статусами", async () => {
    renderHarness();
    await screen.findByText("run_1");

    expect(await screen.findByText("stand-A")).toBeInTheDocument();
    expect(screen.getByText("stand-B")).toBeInTheDocument();
    expect(screen.getByText("filesystem test")).toBeInTheDocument();
    expect(screen.getByText("database test")).toBeInTheDocument();
    // "Выполняется"/"Выполнено" — те же подписи, что и у кнопок фильтра статуса
    // в средней панели, поэтому уточняем узел до бейджа таблицы (span.badge).
    expect(screen.getAllByText("Выполняется", { selector: "span.badge" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Выполнено", { selector: "span.badge" })).toBeInTheDocument();
  });

  it("клик по второй кампании выбирает её и подгружает её детали", async () => {
    renderHarness();
    await screen.findByText("run_1");

    fireEvent.click(screen.getByText("run_2"));
    await waitFor(() => expect(getTestRunMock).toHaveBeenCalledWith("run_2"));
  });

  it("создание прогона собирает пул стендов и вызывает createTestRun", async () => {
    const response: TestRunCreateResponse = {
      ...RUN_1,
      id: "run_new",
      stands_without_tests: [],
      enqueue_errors: [],
    };
    createTestRunMock.mockResolvedValue(response);

    renderHarness();
    await screen.findByText("run_1");

    fireEvent.click(screen.getByRole("button", { name: "Запустить прогон" }));
    await screen.findByText("Все привязанные тесты выбранных стендов на всех доступных ядрах ОС");

    fireEvent.click(screen.getByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(await screen.findByRole("option", { name: "1.7.1.44" }));
    await waitFor(() => expect(listTestStandsMock).toHaveBeenCalledWith({ is_active: true, queue_enabled: true, limit: 500, offset: 0 }));

    const submitButtons = screen.getAllByRole("button", { name: /Запустить прогон/ });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() =>
      expect(createTestRunMock).toHaveBeenCalledWith(
        expect.objectContaining({
          test_run_stands: ["stand_1", "stand_2"],
          final: false,
        }),
      ),
    );
  });

  it("realtime-таймер: элапсед running queue_item растёт со временем без повторного getTestRun", async () => {
    renderHarness();
    const standACell = await screen.findByText("stand-A");
    const row = standACell.closest("tr");
    if (!row) throw new Error("строка стенда не найдена");

    const callsBefore = getTestRunMock.mock.calls.length;
    const timeCellBefore = within(row).getAllByRole("cell")[5];
    const firstValue = timeCellBefore.textContent;

    await new Promise((resolve) => setTimeout(resolve, 1200));

    const timeCellAfter = within(row).getAllByRole("cell")[5];
    expect(timeCellAfter.textContent).not.toBe(firstValue);
    // таймер тикает на клиенте — детали прогона за это время повторно не запрашивались
    expect(getTestRunMock.mock.calls.length).toBe(callsBefore);
  });

  it("показывает последний результат, сохраняет доступ к старому логу и имени из состава", async () => {
    getTestRunMock.mockResolvedValue({
      ...DETAIL,
      entries: [{ id: "entry_1", test_run_id: "run_1", stand_id: "stand_1", test_id: "td_1", test_code: "OLD", test_name: "Тест при старте прогона", enqueue_error_code: null, enqueue_error: null }],
      progress: { total: 1, attempts: 2, succeeded: 1 },
      queue_items: [
        { ...DETAIL.queue_items[0], state: "failed", error: "Ошибка первой попытки", is_current: false, test_run_entry_id: "entry_1" },
        { ...DETAIL.queue_items[0], queue_item_id: "qi_retry", state: "succeeded", error: null, is_retry: true, retry_of_id: "qi_1", is_current: true, test_run_entry_id: "entry_1" },
      ],
    });
    renderHarness();
    await screen.findByText("Тест при старте прогона");
    expect(screen.queryByText("Ошибка первой попытки")).not.toBeInTheDocument();
    expect(screen.getByText(/Успешно: 1 · С ошибкой: 0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Показать предыдущие попытки" }));
    expect(screen.getByText("Ошибка первой попытки")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Лог" })[0]);
    await waitFor(() => expect(getTestLogTextMock).toHaveBeenCalledWith("qi_1"));
  });

  it("ручной повтор отправляет ID текущей попытки и обновляет прогон", async () => {
    retryQueueItemMock.mockResolvedValue({ id: "qi_retry" });
    renderHarness();
    await screen.findByText("stand-B");
    fireEvent.click(screen.getByRole("button", { name: "Повторить тест" }));
    await waitFor(() => expect(retryQueueItemMock).toHaveBeenCalledWith("qi_2", expect.any(String)));
    await waitFor(() => expect(getTestRunMock.mock.calls.length).toBeGreaterThan(1));
  });

  it("клик «Лог» заменяет рабочую зону и крестик возвращает результаты", async () => {
    renderHarness();
    await screen.findByText("stand-A");

    fireEvent.click(screen.getAllByRole("button", { name: "Лог" })[1]);
    expect(await screen.findByText("log body")).toBeInTheDocument();
    expect(getTestLogTextMock).toHaveBeenCalledWith("qi_2");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Закрыть лог" }));
    expect(await screen.findByText("stand-A")).toBeInTheDocument();
  });
  it("для ротированного лога отключает просмотр, сохраняя повтор и результат", async () => {
    getTestRunMock.mockResolvedValue({ ...DETAIL, queue_items: [{ ...DETAIL.queue_items[1], log_status: "rotated" }] });
    renderHarness();
    expect(await screen.findByTitle("Лог ротирован")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Повторить тест" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Обновить результаты" })).not.toBeInTheDocument();
  });

});
