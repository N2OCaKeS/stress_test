import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { TestLogSegment } from "@/api/testing/types";

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
      <Harness />
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
  sockets = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

describe("AdhocMiddlePanel — список debug-запусков остаётся демо (нет публичного backend-эндпоинта)", () => {
  it("рендерит список запусков и помечает виртуальный стенд", async () => {
    renderHarness();
    // "adhoc-2026090701" встречается дважды: строка средней панели + заголовок
    // рабочей зоны (он выбран по умолчанию, первый в списке).
    expect((await screen.findAllByText("adhoc-2026090701")).length).toBeGreaterThan(0);
    // "ВМ" — у двух демо-запусков виртуальный стенд (vm-stand1/vm-stand2)
    expect(screen.getAllByText("ВМ").length).toBeGreaterThan(0);
  });

  it("кнопка запуска остаётся демо-заглушкой — нет вызовов к бэкенду", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    fireEvent.click(screen.getByRole("button", { name: /Запустить разовый тест/ }));
    expect(await screen.findByText(/демо-заглушкой/)).toBeInTheDocument();
    expect(listLogSegmentsMock).not.toHaveBeenCalled();
  });

  it("нигде не осталось текста про legacy «dev mode»/«dev режим»", async () => {
    renderHarness();
    await screen.findAllByText("adhoc-2026090701");
    expect(screen.getByText(/debug-режим/)).toBeInTheDocument();
    expect(screen.queryByText(/dev mode/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/dev режим/i)).not.toBeInTheDocument();
  });
});

describe("AdhocWorkzone — живой лог во время исполнения (running)", () => {
  it("подключает реальный WS живого лога и дописывает входящий текст", async () => {
    renderHarness();
    // adhoc-2026090701 — running, выбран по умолчанию (первый в списке)
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
  const FULL_TEXT = "ЧЕКПОИНТ ОДИН\nOK\nКОМАНДА ДВА\nCHANGED\n";

  beforeEach(() => {
    // Байтовые офсеты в UTF-8: латиница/цифры — 1 байт/символ, кириллица — 2.
    // Тут все символы кириллицы, поэтому офсет считаем через TextEncoder,
    // как это делает сам компонент.
    const bytes = new TextEncoder().encode(FULL_TEXT);
    const splitAt = new TextEncoder().encode("ЧЕКПОИНТ ОДИН\nOK\n").length;
    listLogSegmentsMock.mockResolvedValue({
      items: [
        makeSegment({ id: "seg_1", position: 0, label: "prepare-stand", status: "OK", byte_offset_start: 0, byte_offset_end: splitAt }),
        makeSegment({ id: "seg_2", position: 1, kind: "command", label: "run-test", status: "CHANGED", byte_offset_start: splitAt, byte_offset_end: bytes.length }),
      ],
      total: 2,
      limit: 500,
      offset: 0,
    });
    getTestLogTextMock.mockResolvedValue({ text: FULL_TEXT, filename: "adhoc-2026090612.log" });
    downloadTestLogMock.mockResolvedValue({ text: FULL_TEXT, filename: "adhoc-2026090612.log" });
  });

  it("рендерит реальные сегменты с их статусами и текстом, нарезанным по байт-офсетам", async () => {
    renderHarness();
    // adhoc-2026090612 — done, DB-PG-TPCC на vm-stand1
    fireEvent.click(screen.getByText("adhoc-2026090612"));

    await waitFor(() => expect(getTestLogTextMock).toHaveBeenCalledWith("adhoc-2026090612"));
    // "prepare-stand"/"run-test" — метки сегментов, встречаются и в навигации, и в содержимом
    expect((await screen.findAllByText("prepare-stand")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("run-test").length).toBeGreaterThan(0);
    expect(screen.getByText("ЧЕКПОИНТ ОДИН")).toBeInTheDocument();
    expect(screen.getByText("КОМАНДА ДВА")).toBeInTheDocument();
    expect(screen.getAllByText("CHANGED").length).toBeGreaterThan(0);
  });

  it("скачивание лога вызывает downloadTestLog с queue_item_id запуска", async () => {
    renderHarness();
    fireEvent.click(screen.getByText("adhoc-2026090612"));
    const downloadBtn = await screen.findByRole("button", { name: /Скачать лог/ });
    fireEvent.click(downloadBtn);
    await waitFor(() => expect(downloadTestLogMock).toHaveBeenCalledWith("adhoc-2026090612"));
  });

  it("фильтр «только не-OK» убирает OK-сегмент из навигации, но не из содержимого", async () => {
    renderHarness();
    fireEvent.click(screen.getByText("adhoc-2026090612"));
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
