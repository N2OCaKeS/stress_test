import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
vi.mock("@/api/server/accounts", () => ({
  get listAccounts() {
    return listAccountsMock;
  },
  get getAccount() {
    return getAccountMock;
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
    listServersMock.mockReturnValue(new Promise(() => {}));
    listAccountsMock.mockReturnValue(new Promise(() => {}));
    getAccountMock.mockReturnValue(new Promise(() => {}));
  });

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

    // Правая рабочая зона показывает профиль и инлайн-действия.
    await waitFor(() => {
      expect(screen.getByText(/Ротация пароля/)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /Редактировать/ }),
    ).toBeInTheDocument();
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
});
