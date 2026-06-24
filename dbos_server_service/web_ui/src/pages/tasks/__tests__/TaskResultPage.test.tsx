import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

// Страница тянет задачу через getTask; действия результата — через accounts.
const getTaskMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/misc", () => ({
  get getTask() {
    return getTaskMock;
  },
  // TaskDetail тянет cancelTask из misc — даём заглушку, она тут не зовётся.
  cancelTask: vi.fn(() => new Promise(() => {})),
}));

const importUnknownUserMock = vi.fn((_b?: unknown) => new Promise(() => {}));
const addIgnoredLoginMock = vi.fn((_b?: unknown) => new Promise(() => {}));
const bindAccountServersMock = vi.fn(
  (_id?: unknown, _b?: unknown) => new Promise(() => {}),
);
vi.mock("@/api/server/accounts", () => ({
  get importUnknownUser() {
    return importUnknownUserMock;
  },
  get addIgnoredLogin() {
    return addIgnoredLoginMock;
  },
  get bindAccountServers() {
    return bindAccountServersMock;
  },
}));

// Shell → TopBar монтирует колокол уведомлений, тянущий useAuth; в smoke-тесте
// без AuthProvider это падает — подменяем хук на пустое состояние.
vi.mock("@/api/server/useMyTaskNotifications", () => ({
  useMyTaskNotifications: () => ({
    notifications: [],
    unreadCount: 0,
    markAllRead: vi.fn(),
    markRead: vi.fn(),
  }),
}));

// TaskDetail резолвит имена сервера/отдела — подменяем хуки labels.
vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  return {
    ...actual,
    useServerLabel: () => "alpha",
    useDeptLabel: () => "dep1",
  };
});

import { TaskResultPage } from "@/pages/tasks/TaskResultPage";

function renderAt(id: string) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={[`/tasks/${id}`]}>
              <Routes>
                <Route path="/tasks/:id" element={<TaskResultPage />} />
              </Routes>
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("TaskResultPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getTaskMock.mockReset();
    importUnknownUserMock.mockReset();
    addIgnoredLoginMock.mockReset();
    bindAccountServersMock.mockReset();
    getTaskMock.mockReturnValue(new Promise(() => {}));
    importUnknownUserMock.mockReturnValue(new Promise(() => {}));
    addIgnoredLoginMock.mockReturnValue(new Promise(() => {}));
    bindAccountServersMock.mockReturnValue(new Promise(() => {}));
  });

  it("для inventory-задачи рендерит unlinked_existing и «Связать» зовёт bindAccountServers", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-inv",
      kind: "users.inventory",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        unknown_users: [],
        unlinked_existing: [
          {
            login: "svc-old",
            uid: 1700,
            candidates: [
              { account_id: "acc-existing", department_id: "dep1", source: "managed" },
            ],
          },
        ],
      },
    });
    bindAccountServersMock.mockResolvedValue({ id: "acc-existing" });

    renderAt("tsk-inv");

    expect(
      await screen.findByText(/Существующие аккаунты — связать с сервером/),
    ).toBeInTheDocument();
    expect(screen.getByText("svc-old")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Связать svc-old/ }));
    await waitFor(() => {
      expect(bindAccountServersMock).toHaveBeenCalledTimes(1);
    });
    expect(bindAccountServersMock).toHaveBeenCalledWith("acc-existing", {
      server_ids: ["srv1"],
    });
  });

  it("для inventory-задачи рендерит unknown_users с режимами add/ignore", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-inv2",
      kind: "users.inventory",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        unknown_users: [
          {
            login: "ghost",
            uid: 1500,
            has_sudo: true,
            unix_groups: ["wheel"],
            shell: "/bin/bash",
          },
        ],
        unlinked_existing: [],
      },
    });

    renderAt("tsk-inv2");

    expect(await screen.findByText("ghost")).toBeInTheDocument();
    expect(screen.getByLabelText(/Добавить ghost/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Игнорировать ghost/)).toBeInTheDocument();
  });

  it("при пустом unknown_users, но непустом users — рендерит обнаруженных со статусами и текст пустого состояния", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-inv3",
      kind: "users.inventory",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        users: [
          {
            login: "tester",
            uid: 1001,
            has_sudo: false,
            unix_groups: ["testers"],
            shell: "/bin/bash",
            home_dir: "/home/tester",
          },
          {
            login: "dbos",
            uid: 1000,
            has_sudo: true,
            unix_groups: ["sudo"],
            shell: "/bin/bash",
            home_dir: "/home/dbos",
          },
        ],
        unknown_users: [],
        unlinked_existing: [],
      },
    });

    renderAt("tsk-inv3");

    expect(await screen.findByText(/Обнаруженные пользователи/)).toBeInTheDocument();
    expect(screen.getByText("tester")).toBeInTheDocument();
    expect(screen.getByText("dbos")).toBeInTheDocument();
    // Оба уже учтены → текст пустого состояния, оба помечены «Уже в системе».
    expect(
      screen.getByText(/Новых пользователей для добавления нет/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Уже в системе").length).toBe(2);
  });

  it("обнаруженный незнакомый пользователь в общем списке имеет действие «Добавить»", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-inv4",
      kind: "users.inventory",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        users: [
          {
            login: "ghost",
            uid: 1500,
            has_sudo: true,
            unix_groups: ["wheel"],
            shell: "/bin/bash",
            home_dir: "/home/ghost",
          },
        ],
        unknown_users: [
          {
            login: "ghost",
            uid: 1500,
            has_sudo: true,
            unix_groups: ["wheel"],
            shell: "/bin/bash",
          },
        ],
        unlinked_existing: [],
      },
    });

    renderAt("tsk-inv4");

    expect(await screen.findByText("ghost")).toBeInTheDocument();
    expect(screen.getByText("Не в системе")).toBeInTheDocument();
    expect(screen.getByLabelText(/Добавить ghost/)).toBeInTheDocument();
  });

  it("для прочих задач рендерит TaskDetail со статусом и result", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-pkg",
      kind: "installed_packages.list",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: { packages: [{ name: "htop", version: "3.0" }] },
    });

    renderAt("tsk-pkg");

    // Заголовок TaskDetail с kind'ом + секция result с JSON.
    expect(
      await screen.findByRole("heading", { name: /installed_packages\.list/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/result/)).toBeInTheDocument();
    expect(screen.getByText(/htop/)).toBeInTheDocument();
  });

  it("пока задача не терминальна — показывает статус с поллингом", async () => {
    getTaskMock.mockResolvedValue({
      id: "tsk-run",
      kind: "users.inventory",
      status: "running",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: null,
    });

    renderAt("tsk-run");

    expect(await screen.findByText(/Задача в работе/)).toBeInTheDocument();
    const pending = screen.getByText(/Задача в работе/);
    expect(within(pending).getByText("running")).toBeInTheDocument();
  });
});
