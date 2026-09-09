import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Persona } from "@/types/persona";
import type { QueueItemSummary } from "@/api/testing/types";

// xterm рисует в DOM и тащит canvas — заглушка как в ConsoleTab.test.tsx.
vi.mock("@xterm/xterm", () => {
  class FakeTerminal {
    open() {}
    loadAddon() {}
    clear() {}
    write() {}
    writeln() {}
    focus() {}
    dispose() {}
    onData() {
      return { dispose() {} };
    }
  }
  return { Terminal: FakeTerminal };
});
vi.mock("@xterm/addon-fit", () => {
  class FakeFitAddon {
    fit() {}
  }
  return { FitAddon: FakeFitAddon };
});
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

// Интерактивная консоль не участвует в этих тестах — достаточно стабильных
// заглушек, реальный URL/subprotocol не проверяем.
vi.mock("@/api/server/console", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/api/server/console")>();
  return {
    ...actual,
    consoleWsUrl: () => "ws://test/console",
    consoleWsProtocols: () => ["console.v1"],
  };
});

const findActiveQueueItemMock = vi.fn<
  (serverId: string) => Promise<QueueItemSummary | null>
>();
vi.mock("@/api/testing/testStands", () => ({
  get findActiveQueueItemForServer() {
    return findActiveQueueItemMock;
  },
}));

const testLogStreamUrlMock = vi.fn(() => "ws://test/testing-log");
const testLogStreamProtocolsMock = vi.fn(() => [
  "testing-log.v1",
  "bearer.tok",
]);
vi.mock("@/api/testing/logStream", () => ({
  get testLogStreamUrl() {
    return testLogStreamUrlMock;
  },
  get testLogStreamProtocols() {
    return testLogStreamProtocolsMock;
  },
}));

// Аккаунтов на сервере нет — интерактивная консоль рендерит пустой стейт,
// нам это не мешает: проверяем только независимую live-log панель.
const listAccountsMock = vi.fn();
vi.mock("@/api/server/accounts", () => ({
  get listAccounts() {
    return listAccountsMock;
  },
  get getAccount() {
    return vi.fn();
  },
}));

let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

import { ConsoleTab } from "@/pages/server/tabs/console";

let sockets: FakeWebSocket[] = [];
class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  url: string;
  protocols: string[];
  constructor(url: string, protocols: string[]) {
    this.url = url;
    this.protocols = protocols;
    sockets.push(this);
    setTimeout(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.();
    }, 0);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close(code = 1000, reason = "") {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

function persona(overrides: Partial<Persona>): Persona {
  return {
    id: "p",
    username: "p",
    email: "p@dbos.local",
    initials: "P",
    display_name: "p",
    dept_id: "core",
    platform_role: null,
    service_roles: {},
    accessible_services: ["server"],
    has_admin: false,
    tagline: "",
    ...overrides,
  } as Persona;
}

function renderConsole() {
  return render(
    <ToastProvider>
      <ConsoleTab serverId="srv1" />
    </ToastProvider>,
  );
}

const QUEUE_ITEM: QueueItemSummary = {
  queue_item_id: "qi_live1",
  state: "running",
  test_id: "tdef_1",
  started_at: "2026-01-01T00:00:00Z",
};

describe("Live test log button in server console", () => {
  beforeEach(() => {
    sockets = [];
    findActiveQueueItemMock.mockReset();
    listAccountsMock.mockReset();
    listAccountsMock.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    currentPersona = persona({ platform_role: "dep_admin" });
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  });

  it("не показывает кнопку, если у стенда нет активного прогона", async () => {
    findActiveQueueItemMock.mockResolvedValue(null);
    renderConsole();

    await waitFor(() => expect(findActiveQueueItemMock).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: /Живой лог теста/ }),
    ).not.toBeInTheDocument();
  });

  it("показывает кнопку при активном прогоне и открывает второй независимый терминал по клику", async () => {
    findActiveQueueItemMock.mockResolvedValue(QUEUE_ITEM);
    renderConsole();

    const btn = await screen.findByRole("button", {
      name: /Живой лог теста/,
    });
    fireEvent.click(btn);

    // Второй терминал открыл собственный WS, независимый от интерактивной
    // консоли (та вообще не подключалась в этом тесте).
    await waitFor(() => expect(sockets).toHaveLength(1));
    expect(sockets[0].url).toBe("ws://test/testing-log");
    expect(sockets[0].protocols).toEqual(["testing-log.v1", "bearer.tok"]);

    // Кнопка переключилась на «Скрыть» — панель открыта.
    expect(
      await screen.findByRole("button", { name: /Скрыть живой лог теста/ }),
    ).toBeInTheDocument();

    // Интерактивная консоль по-прежнему на месте и не задета этим потоком.
    expect(
      await screen.findByText(/На этом сервере нет аккаунтов/),
    ).toBeInTheDocument();
  });

  it("сообщение сервера о завершении теста не переподключается автоматически", async () => {
    findActiveQueueItemMock.mockResolvedValue(QUEUE_ITEM);
    renderConsole();

    const btn = await screen.findByRole("button", {
      name: /Живой лог теста/,
    });
    fireEvent.click(btn);
    await waitFor(() => expect(sockets).toHaveLength(1));

    const ws = sockets[0];
    await waitFor(() => expect(ws.readyState).toBe(FakeWebSocket.OPEN));
    act(() => {
      ws.close(1000, "TEST_FINISHED");
    });

    // Закрытие сервером не создаёт нового сокета (нет авто-реконнекта).
    await new Promise((r) => setTimeout(r, 10));
    expect(sockets).toHaveLength(1);
  });
});
