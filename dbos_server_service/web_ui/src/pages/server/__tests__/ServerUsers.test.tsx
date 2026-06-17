import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

// Сетевые вызовы страницы мокаем — нужен только smoke-рендер (заголовок,
// плейсхолдер поиска, loading state). Список серверов держим в pending.
const listServersMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/servers", () => ({
  get listServers() {
    return listServersMock;
  },
}));

const listAccountsMock = vi.fn(() => new Promise(() => {}));
const getAccountMock = vi.fn(() => new Promise(() => {}));
const adoptFromHostMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/accounts", () => ({
  get listAccounts() {
    return listAccountsMock;
  },
  get getAccount() {
    return getAccountMock;
  },
  get adoptFromHost() {
    return adoptFromHostMock;
  },
}));

// Ревизия дёргает users/inventory (misc) и поллит задачу через getTask.
const usersInventoryMock = vi.fn(() => new Promise(() => {}));
const getTaskMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/misc", () => ({
  get usersInventory() {
    return usersInventoryMock;
  },
  get getTask() {
    return getTaskMock;
  },
}));

// LabelsProvider тащит сетевые загрузки имён — подменяем хук карты серверов и
// label пользователей, остальные экспорты берём из оригинала.
vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  return {
    ...actual,
    useServerMap: () => new Map<string, string>([["srv1", "alpha"]]),
    useUserLabel: () => "—",
  };
});

import { ServerUsers } from "@/pages/server/ServerUsers";

function renderPage() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={["/server/users"]}>
              <ServerUsers />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

const FAKE_ACCOUNT = {
  id: "acc1",
  server_ids: ["srv1"],
  department_id: "dep1",
  login: "dbos-svc",
  source: "managed" as const,
  has_sudo: true,
  unix_groups: ["docker"],
  linked_user_id: null,
  shell: "/bin/bash",
  home_dir: "/home/dbos-svc",
  is_active: true,
  password_rotated_at: null,
  password_b64: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: null,
};

describe("ServerUsers (fleet account list)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listServersMock.mockReset();
    listAccountsMock.mockReset();
    getAccountMock.mockReset();
    adoptFromHostMock.mockReset();
    usersInventoryMock.mockReset();
    getTaskMock.mockReset();
    listServersMock.mockReturnValue(new Promise(() => {}));
    listAccountsMock.mockReturnValue(new Promise(() => {}));
    getAccountMock.mockReturnValue(new Promise(() => {}));
    adoptFromHostMock.mockReturnValue(new Promise(() => {}));
    usersInventoryMock.mockReturnValue(new Promise(() => {}));
    getTaskMock.mockReturnValue(new Promise(() => {}));
  });

  function selectAccount() {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [FAKE_ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });
  }

  it("рендерится и показывает loading state, пока грузятся серверы", () => {
    renderPage();
    expect(screen.getByText(/server_service \/ server users/)).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/Поиск по 0 аккаунтам/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
  });

  it("показывает рабочую зону аккаунта в правой панели по клику на строку", async () => {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [FAKE_ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });

    renderPage();

    const loginCell = await screen.findByText("dbos-svc");
    fireEvent.click(loginCell);

    // Правая рабочая зона показывает управляющие кнопки наверху.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Редактировать/ }),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Удалить/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Ротировать \(БД\)/ }),
    ).toBeInTheDocument();

    // Секция «Серверы аккаунта» с per-server provision/deprovision.
    expect(screen.getByText(/Серверы аккаунта/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Provision/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Deprovision/ }),
    ).toBeInTheDocument();
    // Кнопка привязки сервера тоже на месте.
    expect(
      screen.getByRole("button", { name: /Привязать/ }),
    ).toBeInTheDocument();
  });

  it("показывает кнопку «Ревизия» у сервера в секции", async () => {
    selectAccount();
    renderPage();

    fireEvent.click(await screen.findByText("dbos-svc"));
    expect(
      await screen.findByRole("button", { name: /Ревизия/ }),
    ).toBeInTheDocument();
  });

  it("открывает модалку diff с было/стало и чекбоксами по завершении ревизии", async () => {
    selectAccount();
    usersInventoryMock.mockResolvedValue({ task_id: "tsk1", status: "queued" });
    // Первый же поллинг возвращает succeeded с расхождениями — модалка
    // открывается автоматически (быстрый таск).
    getTaskMock.mockResolvedValue({
      id: "tsk1",
      kind: "users.inventory",
      status: "succeeded",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        diffs: [
          {
            account_id: "acc1",
            login: "dbos-svc",
            fields: {
              has_sudo: { expected: false, found: true },
              unix_groups: { expected: ["postgres"], found: ["wheel"] },
              shell: { expected: "/bin/bash", found: "/bin/sh" },
            },
          },
        ],
      },
    });

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));
    fireEvent.click(await screen.findByRole("button", { name: /Ревизия/ }));

    // Модалка с заголовком и кнопкой применения.
    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    expect(
      d.getByRole("button", { name: /Применить к БД/ }),
    ).toBeInTheDocument();

    // Было → стало для shell (внутри модалки).
    expect(d.getByText("/bin/bash")).toBeInTheDocument();
    expect(d.getByText("/bin/sh")).toBeInTheDocument();

    // Чекбоксы по полям присутствуют и по умолчанию отмечены.
    const sudoCheck = d.getByLabelText(/Применить has_sudo/);
    expect((sudoCheck as HTMLInputElement).checked).toBe(true);
    expect(d.getByLabelText(/Применить unix_groups/)).toBeInTheDocument();
    expect(d.getByLabelText(/Применить shell/)).toBeInTheDocument();
  });
});
