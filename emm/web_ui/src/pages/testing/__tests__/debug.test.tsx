import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ApiError } from "@/api/client";
import type { TestLogSegment } from "@/api/testing/types";

const listQueueItemsMock = vi.fn();
const launchQueueItemMock = vi.fn();
const retryQueueItemMock = vi.fn();
vi.mock("@/api/testing/queueItems", () => ({
  listQueueItems: (...args: unknown[]) => listQueueItemsMock(...args),
  launchQueueItem: (...args: unknown[]) => launchQueueItemMock(...args),
  retryQueueItem: (...args: unknown[]) => retryQueueItemMock(...args),
}));
vi.mock("@/api/testing/testDefinitions", () => ({ listTestDefinitions: async () => ({ items: [
  { id: "t1", code: "STR-SEGFAULT-FUZZ", full_name: "stress test", readiness: "ready", pinned_stand_id: "s1" },
  { id: "t2", code: "DB-PG-TPCC", full_name: "database test", readiness: "development", pinned_stand_id: "s2" },
] }) }));
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: async () => ({ items: [{ id: "s1" }, { id: "s2" }] }),
  getTestStand: async (id: string) => ({ id, server_id: id, department_id: id === "s1" ? "core" : "other", server: { display_name: id === "s1" ? "stand15-110" : "vm-stand1" } }),
}));
vi.mock("@/api/server/osVersions", () => ({ listOsVersions: async () => ({ items: [{ id: "osv_1", name: "1.8.5", kernels: ["6.1"] }] }) }));

vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  const names: Record<string, string> = { usr_holder1: "Иван Петров" };
  return { ...actual, useUserLabel: (id: string | null | undefined) => (id ? names[id] ?? id : "—") };
});

const triggerStatisticsRecalcMock = vi.fn();
const getStatisticsStatusMock = vi.fn();
const getStatisticsCategoriesMock = vi.fn();
vi.mock("@/api/testing/statistics", () => ({
  triggerStatisticsRecalc: (...args: unknown[]) => triggerStatisticsRecalcMock(...args),
  getStatisticsStatus: (...args: unknown[]) => getStatisticsStatusMock(...args),
  getStatisticsCategories: (...args: unknown[]) => getStatisticsCategoriesMock(...args),
}));
const ITEMS = [
  { id: "adhoc-2026090701", test_id: "t1", stand_id: "s1", test_run_id: null, retry_of_id: null, debug_mode: true, state: "running", rc: "1.8.5", kernel: "6.1", mode: "orel", created_at: "2026-09-07T06:40:00Z", started_at: "2026-09-07T06:40:00Z", finished_at: null, error: null },
  { id: "adhoc-2026090612", test_id: "t2", stand_id: "s2", test_run_id: null, retry_of_id: null, debug_mode: true, state: "succeeded", rc: "1.8.5", kernel: "6.1", mode: "smolensk", created_at: "2026-09-06T19:10:00Z", started_at: "2026-09-06T19:10:00Z", finished_at: "2026-09-06T20:10:00Z", error: null },
];

const listLogSegmentsMock = vi.fn();
const getTestLogTextMock = vi.fn();
const downloadTestLogMock = vi.fn();
vi.mock("@/api/testing/testLogs", () => ({
  listLogSegments: (...args: unknown[]) => listLogSegmentsMock(...args),
  getTestLogText: (...args: unknown[]) => getTestLogTextMock(...args),
  downloadTestLog: (...args: unknown[]) => downloadTestLogMock(...args),
}));

const testLogStreamUrlMock = vi.fn(() => "ws://test/testing-log");
const testLogStreamProtocolsMock = vi.fn(() => ["testing-log.v1", "bearer.tok"]);
vi.mock("@/api/testing/logStream", () => ({
  get testLogStreamUrl() {
    return testLogStreamUrlMock;
  },
  get testLogStreamProtocols() {
    return testLogStreamProtocolsMock;
  },
}));

import { AdhocMiddlePanel, AdhocWorkzone, useAdhocState } from "@/pages/testing/debug";

let sockets: FakeWebSocket[] = [];
class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  protocols: string[];
  constructor(url: string, protocols: string[]) {
    this.url = url;
    this.protocols = protocols;
    sockets.push(this);
  }
  close(code = 1000, reason = "") {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

function Harness() {
  const state = useAdhocState();
  return (
    <>
      <AdhocMiddlePanel state={state} />
      <AdhocWorkzone state={state} />
    </>
  );
}

function renderHarness() {
  return render(
    <ToastProvider>
      <PersonaProvider>
        <Harness />
      </PersonaProvider>
    </ToastProvider>,
  );
}

function makeSegment(over: Partial<TestLogSegment> & Pick<TestLogSegment, "id" | "position">): TestLogSegment {
  return {
    log_id: "log_1",
    kind: "checkpoint",
    label: "prepare-stand",
    command_text_masked: null,
    status: "OK",
    started_at: "2026-09-06T22:10:00Z",
    finished_at: "2026-09-06T22:10:05Z",
    byte_offset_start: 0,
    byte_offset_end: null,
    ...over,
  };
}

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  listQueueItemsMock.mockResolvedValue({ items: ITEMS, total: 2 });
  launchQueueItemMock.mockResolvedValue({ ...ITEMS[0], id: "qi_new", state: "queued" });
  triggerStatisticsRecalcMock.mockResolvedValue({
    status: "running", triggered_by: "manual", category: null, test_run_id: null,
    started_at: "2026-09-15T10:00:00Z", finished_at: null, error: null, updated_at: null,
  });
  getStatisticsStatusMock.mockResolvedValue({
    status: "idle", triggered_by: null, category: null, test_run_id: null,
    started_at: null, finished_at: null, error: null, updated_at: null,
  });
  getStatisticsCategoriesMock.mockResolvedValue([
    { key: "apache", label: "Apache" },
    { key: "parsec", label: "Parsec" },
  ]);
  sockets = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

describe("AdhocMiddlePanel — реальные одиночные запуски", () => {
  it("пагинация, поиск и статусы запрашиваются на сервере", async () => {
    listQueueItemsMock.mockResolvedValue({ items: ITEMS, total: 62 });
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    await waitFor(() => expect(listQueueItemsMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 50 })));
    fireEvent.change(screen.getByPlaceholderText("Поиск по 62 запускам…"), { target: { value: "FS-CHECK" } });
    await waitFor(() => expect(listQueueItemsMock).toHaveBeenLastCalledWith(expect.objectContaining({ q: "FS-CHECK", offset: 0 })));
    fireEvent.click(screen.getByRole("button", { name: "В очереди" }));
    await waitFor(() => expect(listQueueItemsMock).toHaveBeenLastCalledWith(expect.objectContaining({ states: ["queued", "preparing", "ready"] })));
  });

  it("загружает одиночные запуски и помечает debug", async () => {
    renderHarness();
    // "adhoc-2026090701" встречается дважды: строка средней панели + заголовок
    // рабочей зоны (он выбран по умолчанию, первый в списке).
    expect((await screen.findAllByText("adhoc-2026090701")).length).toBeGreaterThan(0);
    expect(listQueueItemsMock).toHaveBeenCalled();
    expect(screen.getAllByText("Debug").length).toBeGreaterThan(0);
  });

  it("создаёт одиночный запуск с явным debug и выбранным контекстом", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(screen.getByRole("button", { name: /Запустить разовый тест/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Выберите тест" }));
    fireEvent.click(await screen.findByRole("option", { name: "stress test · STR-SEGFAULT-FUZZ" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Debug" }));
    fireEvent.click(screen.getByRole("button", { name: "Выберите стенд" }));
    fireEvent.click(screen.getByRole("option", { name: "stand15-110" }));
    fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(screen.getByRole("option", { name: "1.8.5" }));
    fireEvent.click(screen.getByRole("button", { name: "Запустить тест" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledWith(expect.objectContaining({ test_id: "t1", stand_id: "s1", debug_mode: true, os_version_id: "osv_1", kernel: "6.1" })));
  });

  it("при повторе после сетевой ошибки сохраняет идентификатор запроса", async () => {
    launchQueueItemMock.mockRejectedValueOnce(new Error("network failure"));
    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /Запустить разовый тест/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Выберите тест" }));
    fireEvent.click(await screen.findByRole("option", { name: "stress test · STR-SEGFAULT-FUZZ" }));
    fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(screen.getByRole("option", { name: "1.8.5" }));
    fireEvent.click(screen.getByRole("button", { name: "Запустить тест" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Запустить тест" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledTimes(2));
    expect(launchQueueItemMock.mock.calls[0][0].request_id).toBe(launchQueueItemMock.mock.calls[1][0].request_id);
    expect(launchQueueItemMock.mock.calls[0][0].debug_mode).toBe(false);
  });

  async function openLaunchModalAndSubmit(options: { debugStand?: string } = {}) {
    renderHarness();
    fireEvent.click(screen.getByRole("button", { name: /Запустить разовый тест/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Выберите тест" }));
    fireEvent.click(await screen.findByRole("option", { name: "stress test · STR-SEGFAULT-FUZZ" }));
    if (options.debugStand) {
      fireEvent.click(screen.getByRole("checkbox", { name: "Debug" }));
      fireEvent.click(screen.getByRole("button", { name: "Выберите стенд" }));
      fireEvent.click(screen.getByRole("option", { name: options.debugStand }));
    }
    fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(screen.getByRole("option", { name: "1.8.5" }));
    fireEvent.click(screen.getByRole("button", { name: "Запустить тест" }));
  }
  const busyError = (details: Record<string, unknown>) =>
    new ApiError(409, { error_code: "STAND_BUSY", message: "Стенд сейчас занят — запуск недоступен", details });
  const queueActiveError = () =>
    new ApiError(409, {
      error_code: "STAND_QUEUE_ACTIVE", message: "На стенде уже идёт очередь",
      details: { queued_count: 2, current: { queue_item_id: "qi_1", state: "running", test_code: "DB-PG-TPCC" } },
    });

  it("занятый стенд: админ своего отдела забирает его кнопкой «Забрать стенд у …»", async () => {
    launchQueueItemMock.mockRejectedValueOnce(busyError({ busy_state: "busy", busy_user_id: "petrov", takeover_possible: true }));
    await openLaunchModalAndSubmit();
    fireEvent.click(await screen.findByRole("button", { name: "Забрать стенд у petrov и запустить" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledTimes(2));
    expect(launchQueueItemMock.mock.calls[0][0].force).toBe(false);
    expect(launchQueueItemMock.mock.calls[1][0]).toEqual(expect.objectContaining({ force: true, stand_id: "s1" }));
    expect(launchQueueItemMock.mock.calls[1][0].on_active_queue).toBeUndefined();
    expect(screen.queryByText(/провалится|Обходит только/)).not.toBeInTheDocument();
  });

  it("занятый стенд: вместо usr_ id в сообщении и на кнопке — имя держателя", async () => {
    launchQueueItemMock.mockRejectedValueOnce(busyError({ busy_state: "busy", busy_user_id: "usr_holder1", busy_actor_type: "user", takeover_possible: true }));
    await openLaunchModalAndSubmit();
    await screen.findByText(/Стенд занят \(Иван Петров\)/);
    expect(screen.getByRole("button", { name: "Забрать стенд у Иван Петров и запустить" })).toBeInTheDocument();
    expect(screen.queryByText(/usr_holder1/)).not.toBeInTheDocument();
  });

  it("занятый стенд, который отобрать нельзя (обновление/восстановление): кнопки нет, есть пояснение", async () => {
    launchQueueItemMock.mockRejectedValueOnce(busyError({ busy_state: "updating", takeover_possible: false }));
    await openLaunchModalAndSubmit();
    await screen.findByText(/Стенд забрать нельзя/);
    expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
  });

  it("занятый стенд: не-админ видит только уведомление", async () => {
    window.localStorage.setItem("dbos-persona", "erin");
    try {
      launchQueueItemMock.mockRejectedValueOnce(busyError({ busy_state: "busy", busy_user_id: "petrov", takeover_possible: true }));
      await openLaunchModalAndSubmit();
      await screen.findByText(/Стенд занят \(petrov\)/);
      expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
    } finally {
      window.localStorage.removeItem("dbos-persona");
    }
  });

  it("занятый стенд другого отдела: кнопка забора не показывается даже админу", async () => {
    launchQueueItemMock.mockRejectedValueOnce(busyError({ busy_state: "busy", busy_user_id: "petrov", takeover_possible: true }));
    await openLaunchModalAndSubmit({ debugStand: "vm-stand1" });
    await screen.findByText(/Стенд занят \(petrov\)/);
    expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
  });

  it("активная очередь: «Добавить в конец очереди» повторяет запрос с on_active_queue=append", async () => {
    launchQueueItemMock.mockRejectedValueOnce(queueActiveError());
    await openLaunchModalAndSubmit();
    await screen.findByText(/в очереди ещё 2/);
    expect(within(screen.getByRole("group", { name: "Очередь стенда занята" })).getByText("DB-PG-TPCC")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Добавить в конец очереди" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledTimes(2));
    expect(launchQueueItemMock.mock.calls[1][0]).toEqual(expect.objectContaining({ on_active_queue: "append", force: false }));
  });

  it("активная очередь: «Очистить очередь и запустить сразу» повторяет запрос с on_active_queue=replace", async () => {
    launchQueueItemMock.mockRejectedValueOnce(queueActiveError());
    await openLaunchModalAndSubmit();
    fireEvent.click(await screen.findByRole("button", { name: "Очистить очередь и запустить сразу" }));
    await waitFor(() => expect(launchQueueItemMock).toHaveBeenCalledTimes(2));
    expect(launchQueueItemMock.mock.calls[1][0]).toEqual(expect.objectContaining({ on_active_queue: "replace" }));
    // Новый режим — новое тело, значит и новый ключ идемпотентности.
    expect(launchQueueItemMock.mock.calls[1][0].request_id).not.toBe(launchQueueItemMock.mock.calls[0][0].request_id);
  });

  it("нигде не осталось текста про legacy «dev mode»/«dev режим»", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(screen.getByText(/Debug: результат не засчитывается/)).toBeInTheDocument();
    expect(screen.queryByText(/dev mode/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/dev режим/i)).not.toBeInTheDocument();
  });

  it("кнопка «Пересчитать статистику» запускает фоновый пересчёт и не ждёт его окончания", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(screen.getByRole("button", { name: /Пересчитать статистику/ }));
    await waitFor(() => expect(triggerStatisticsRecalcMock).toHaveBeenCalledTimes(1));
    // Полный пересчёт — без category, как и раньше.
    expect(triggerStatisticsRecalcMock).toHaveBeenCalledWith({});
    expect(await screen.findByText(/запущен в фоне/)).toBeInTheDocument();
  });

  it("на каждое семейство тестов с бекенда есть своя кнопка пересчёта", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(await screen.findByRole("button", { name: "Apache" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Parsec" })).toBeInTheDocument();
  });

  it("пер-категорийная кнопка передаёт свой ключ в category", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(await screen.findByRole("button", { name: "Parsec" }));
    await waitFor(() => expect(triggerStatisticsRecalcMock).toHaveBeenCalledWith({ category: "parsec" }));
    expect(await screen.findByText(/«Parsec» запущен в фоне/)).toBeInTheDocument();
  });

  it("индикатор расчёта называет семейство, если считается не всё сразу", async () => {
    getStatisticsStatusMock.mockResolvedValue({
      status: "running", triggered_by: "manual", category: "parsec", test_run_id: null,
      started_at: "2026-09-18T10:00:00Z", finished_at: null, error: null, updated_at: null,
    });
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(await screen.findByText("Идёт расчёт статистики: Parsec…")).toBeInTheDocument();
  });

  it("индикатор «Идёт расчёт статистики» виден только пока фактически идёт пересчёт", async () => {
    getStatisticsStatusMock.mockResolvedValueOnce({
      status: "running", triggered_by: "manual", category: null, test_run_id: null,
      started_at: "2026-09-15T10:00:00Z", finished_at: null, error: null, updated_at: null,
    });
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(await screen.findByText("Идёт расчёт статистики…")).toBeInTheDocument();
  });

  it("нет индикатора расчёта статистики, если пересчёт не идёт", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(screen.queryByText("Идёт расчёт статистики…")).not.toBeInTheDocument();
  });

  it("показывает ошибку тостом, если запуск пересчёта статистики отклонён", async () => {
    triggerStatisticsRecalcMock.mockRejectedValueOnce(new Error("нет прав"));
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(screen.getByRole("button", { name: /Пересчитать статистику/ }));
    expect(await screen.findByText("нет прав")).toBeInTheDocument();
  });
});

describe("AdhocWorkzone — живой лог во время исполнения (running)", () => {
  it("подключает реальный WS живого лога и дописывает входящий текст", async () => {
    renderHarness();
    // adhoc-2026090701 — running, выбран по умолчанию (первый в списке),
    // лог живого запуска подключается сразу, без клика на отдельную кнопку
    await screen.findAllByText("adhoc-2026090701");

    await waitFor(() => expect(sockets).toHaveLength(1));
    expect(sockets[0].url).toBe("ws://test/testing-log");
    expect(testLogStreamUrlMock).toHaveBeenCalledWith("adhoc-2026090701");

    act(() => {
      sockets[0].readyState = FakeWebSocket.OPEN;
      sockets[0].onopen?.();
      sockets[0].onmessage?.({ data: "первая строка лога\n" });
    });

    expect(await screen.findByText(/первая строка лога/)).toBeInTheDocument();
  });
});

describe("AdhocWorkzone — завершённый лог (реальные сегменты + реальный текст)", () => {
  const FULL_TEXT = "ЧЕКПОИНТ ОДИН\nOK\nКОМАНДА ДВА\nCHANGED ✅\n";

  beforeEach(() => {
    const characters = Array.from(FULL_TEXT);
    const splitAt = Array.from("ЧЕКПОИНТ ОДИН\nOK\n").length;
    listLogSegmentsMock.mockResolvedValue({
      items: [
        makeSegment({ id: "seg_1", position: 0, label: "prepare-stand", status: "OK", byte_offset_start: 0, byte_offset_end: splitAt }),
        makeSegment({ id: "seg_2", position: 1, kind: "command", label: "run-test", status: "CHANGED", byte_offset_start: splitAt, byte_offset_end: characters.length }),
      ],
      total: 2,
      limit: 500,
      offset: 0,
    });
    getTestLogTextMock.mockResolvedValue({ text: FULL_TEXT, filename: "adhoc-2026090612.log" });
    downloadTestLogMock.mockResolvedValue({ text: FULL_TEXT, filename: "adhoc-2026090612.log" });
  });

  it("рендерит реальные сегменты с их статусами и текстом, нарезанным по Unicode-офсетам", async () => {
    renderHarness();
    // adhoc-2026090612 — done, DB-PG-TPCC на vm-stand1
    fireEvent.click(await screen.findByText("adhoc-2026090612"));

    await waitFor(() => expect(getTestLogTextMock).toHaveBeenCalledWith("adhoc-2026090612"));
    // "prepare-stand"/"run-test" — метки сегментов, встречаются и в навигации, и в содержимом
    expect((await screen.findAllByText("prepare-stand")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("run-test").length).toBeGreaterThan(0);
    expect(screen.getByText("ЧЕКПОИНТ ОДИН")).toBeInTheDocument();
    expect(screen.getByText("КОМАНДА ДВА")).toBeInTheDocument();
    expect(screen.getAllByText("CHANGED").length).toBeGreaterThan(0);
  });

  it("показывает полный текст, если у лога нет сегментов", async () => {
    listLogSegmentsMock.mockResolvedValue({ items: [], total: 0 });
    renderHarness();
    fireEvent.click(await screen.findByText("adhoc-2026090612"));
    expect(await screen.findByText(/ЧЕКПОИНТ ОДИН.*OK.*КОМАНДА ДВА/s)).toBeInTheDocument();
  });

  it("загружает следующие страницы сегментов", async () => {
    listLogSegmentsMock.mockResolvedValueOnce({ items: [makeSegment({ id: "first", position: 0, label: "first page" })], total: 2 })
      .mockResolvedValueOnce({ items: [makeSegment({ id: "last", position: 1, label: "last page" })], total: 2 });
    renderHarness();
    fireEvent.click(await screen.findByText("adhoc-2026090612"));
    expect((await screen.findAllByText("last page")).length).toBeGreaterThan(0);
    expect(listLogSegmentsMock).toHaveBeenCalledWith("adhoc-2026090612", { limit: 500, offset: 1 });
  });

  it("скачивание лога вызывает downloadTestLog с queue_item_id запуска", async () => {
    renderHarness();
    fireEvent.click(await screen.findByText("adhoc-2026090612"));
    const downloadBtn = await screen.findByRole("button", { name: /Скачать лог/ });
    fireEvent.click(downloadBtn);
    await waitFor(() => expect(downloadTestLogMock).toHaveBeenCalledWith("adhoc-2026090612"));
  });

  it("фильтр «только не-OK» убирает OK-сегмент из навигации, но не из содержимого", async () => {
    renderHarness();
    fireEvent.click(await screen.findByText("adhoc-2026090612"));
    await screen.findAllByText("prepare-stand");
    // до фильтра "prepare-stand" встречается дважды: кнопка навигации + блок содержимого
    expect(screen.getAllByText("prepare-stand")).toHaveLength(2);

    fireEvent.click(screen.getByLabelText(/Только не-OK/));

    // после фильтра кнопка навигации пропала, блок содержимого остался (притемнён)
    await waitFor(() => expect(screen.getAllByText("prepare-stand")).toHaveLength(1));
    // "run-test" — CHANGED, фильтром не тронут, остаётся и в навигации, и в содержимом
    expect(screen.getAllByText("run-test").length).toBeGreaterThan(0);
  });
});
