import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import { toBase64 } from "@/lib/base64";
import { closeAllConsoleSockets } from "@/lib/consoleSocketRegistry";
import type { Persona } from "@/types/persona";

// xterm рисует в DOM и тащит canvas — для jsdom подменяем терминал заглушкой,
// которая отдаёт onData-подписку и пустые методы. Нам важен только поток
// данных в WebSocket, а не реальный рендер.
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

// Консольный WS строим из этих хелперов — в тесте достаточно стабильных
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

const listAccountsMock = vi.fn();
const getAccountMock = vi.fn();
vi.mock("@/api/server/accounts", () => ({
  get listAccounts() {
    return listAccountsMock;
  },
  get getAccount() {
    return getAccountMock;
  },
}));

// Persona задаём прямо — управляем гейтом view_password (dep_admin/operator →
// есть; reader → нет).
let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

import { ConsoleTab } from "@/pages/server/tabs/console";

// Управляемый фейк WebSocket: ловим send и эмулируем мгновенный open.
let lastWs: FakeWebSocket | null = null;
class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  constructor() {
    FakeWebSocket.register(this);
    setTimeout(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.();
    }, 0);
  }
  static register(ws: FakeWebSocket) {
    lastWs = ws;
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
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

const ACCOUNT = {
  id: "acc1",
  server_ids: ["srv1"],
  department_id: "core",
  login: "dbos-svc",
  source: "managed" as const,
  has_sudo: true,
  unix_groups: [],
  linked_user_id: null,
  shell: "/bin/bash",
  home_dir: "/home/dbos-svc",
  is_active: true,
  password_rotated_at: "2026-01-01T00:00:00Z",
  password_b64: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: null,
};

function renderConsole() {
  return render(
    <ToastProvider>
      <ConsoleTab serverId="srv1" />
    </ToastProvider>,
  );
}

async function connect() {
  // Кнопка-триггер дропдауна обёрнута в <label>, но собственный aria-label
  // кнопки (плейсхолдер/значение) приоритетнее native-label ассоциации.
  const trigger = await screen.findByRole("button", {
    name: "— выберите учётку —",
  });
  fireEvent.click(trigger);
  const option = await screen.findByRole("option", { name: /dbos-svc/ });
  fireEvent.click(option);
  fireEvent.click(screen.getByRole("button", { name: /Подключить/ }));
  await waitFor(() => lastWs?.readyState === FakeWebSocket.OPEN);
}

describe("ConsoleTab password injection", () => {
  beforeEach(() => {
    // Живая WS-сессия консоли теперь держится в persistent-реестре
    // (`@/lib/consoleSocketRegistry`) — module-уровневый singleton, который
    // осознанно переживает unmount компонента (SPA-навигация), но из-за
    // этого точно так же переживает и unmount между тестами в одном файле.
    // Без явного сброса второй тест находит "живую" сессию, оставшуюся от
    // первого (WS там ни разу не закрывался), и монтируется сразу в
    // состоянии "Подключено" вместо "Подключить". Закрываем всё перед каждым
    // тестом — ровно то же самое, что происходит в реальном приложении на
    // logout.
    closeAllConsoleSockets();
    sessionStorage.clear();
    lastWs = null;
    listAccountsMock.mockReset();
    getAccountMock.mockReset();
    listAccountsMock.mockResolvedValue({
      items: [ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  });

  it("dep_admin: клик «Пароль» шлёт пароль в ws без перевода строки", async () => {
    currentPersona = persona({ platform_role: "dep_admin" });
    getAccountMock.mockResolvedValue({
      ...ACCOUNT,
      password_b64: toBase64("s3cr3t"),
    });

    renderConsole();
    await connect();

    fireEvent.click(
      await screen.findByRole("button", { name: /Пароль/ }),
    );

    await waitFor(() => expect(getAccountMock).toHaveBeenCalledWith("acc1"));
    await waitFor(() => expect(lastWs?.sent).toContain("s3cr3t"));
    // Без автоматического Enter — никаких \n / \r в отправленном.
    expect(lastWs?.sent.some((s) => /[\r\n]/.test(s))).toBe(false);
  });

  it("reader без view_password: кнопка «Пароль» задизейблена", async () => {
    currentPersona = persona({ service_roles: { server: "reader" } });

    renderConsole();
    await connect();

    const btn = await screen.findByRole("button", { name: /Пароль/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Нет доступа к паролю");

    fireEvent.click(btn);
    expect(getAccountMock).not.toHaveBeenCalled();
  });
});
