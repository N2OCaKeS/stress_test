import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "@/contexts/ToastContext";
import type { TaskRead } from "@/api/server/types";

const ME = "user-me";

// useAuth — фиксированный текущий пользователь (mock-режим AuthContext не
// выставляет user, поэтому подменяем хук целиком).
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { user_id: ME } }),
  useAuthOptional: () => ({ user: { user_id: ME } }),
}));

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

const listTasksMock = vi.fn();
vi.mock("@/api/server/misc", () => ({
  listTasks: (...args: unknown[]) => listTasksMock(...args),
}));

import { NotificationBell } from "@/components/notifications/NotificationBell";

function makeTask(over: Partial<TaskRead> & Pick<TaskRead, "id">): TaskRead {
  return {
    kind: "server.prepare",
    status: "succeeded",
    created_at: "2026-06-23T10:00:00Z",
    finished_at: "2026-06-23T10:01:00Z",
    retry_count: 0,
    ...over,
  } as TaskRead;
}

function page(items: TaskRead[]) {
  return { items, total: items.length };
}

function renderBell() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("NotificationBell + NotificationCenter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    navigateMock.mockReset();
    listTasksMock.mockReset();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("бейдж считает только новые терминальные как непрочитанные", async () => {
    listTasksMock.mockResolvedValue(
      page([
        makeTask({ id: "t1", status: "succeeded" }),
        makeTask({ id: "t2", status: "failed" }),
        // нетерминальная — пока не непрочитанная
        makeTask({ id: "t4", status: "running" }),
      ]),
    );
    renderBell();

    // первый tick — t1/t2 приходят уже терминальными, новыми их не считаем,
    // поэтому они read=true и бейджа нет.
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    expect(screen.queryByText(/непрочитанных/)).not.toBeInTheDocument();

    // на следующем опросе t4 переходит в failed — это новый терминал → unread.
    listTasksMock.mockResolvedValue(
      page([
        makeTask({ id: "t1", status: "succeeded" }),
        makeTask({ id: "t2", status: "failed" }),
        makeTask({ id: "t4", status: "failed" }),
      ]),
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    const bell = screen.getByRole("button", { name: /Уведомления: 1/ });
    expect(bell).toBeInTheDocument();
  });

  it("клик по элементу зовёт navigate('/tasks/<id>') и markRead", async () => {
    // t1 приходит queued, затем succeeded — новый терминал, unread.
    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "queued" })]));
    renderBell();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });

    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "succeeded" })]));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    expect(
      screen.getByRole("button", { name: /Уведомления: 1/ }),
    ).toBeInTheDocument();

    // открыть центр и кликнуть по задаче
    fireEvent.click(screen.getByRole("button", { name: /Уведомления: 1/ }));
    fireEvent.click(screen.getByText("server.prepare"));

    expect(navigateMock).toHaveBeenCalledWith("/tasks/t1");
    // markRead отработал — бейдж пропал.
    expect(screen.queryByText(/Уведомления: 1/)).not.toBeInTheDocument();
  });

  it("новый терминальный статус показывает toast", async () => {
    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "running" })]));
    renderBell();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    expect(screen.queryByText(/завершена успешно/)).not.toBeInTheDocument();

    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "succeeded" })]));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    expect(
      screen.getByText(/Задача server\.prepare завершена успешно/),
    ).toBeInTheDocument();
  });

  it("отметить все прочитанными гасит бейдж", async () => {
    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "queued" })]));
    renderBell();
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    listTasksMock.mockResolvedValue(page([makeTask({ id: "t1", status: "failed" })]));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

    fireEvent.click(screen.getByRole("button", { name: /Уведомления: 1/ }));
    fireEvent.click(screen.getByText("Отметить все прочитанными"));

    expect(screen.queryByText(/Уведомления: 1/)).not.toBeInTheDocument();
  });
});
