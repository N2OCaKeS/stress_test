import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "@/contexts/ToastContext";
import type { PublicQueueItem } from "@/api/testing/queueItems";

const ME = "user-me";
const DEPT = "dep_a";

// Пользователь отдела — без этого useDepartmentRunNotifications вообще не
// опрашивает (см. guard на userId/departmentId в самом хуке).
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { user_id: ME, department_id: DEPT } }),
  useAuthOptional: () => ({ user: { user_id: ME, department_id: DEPT } }),
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

const listQueueItemsMock = vi.fn();
vi.mock("@/api/testing/queueItems", () => ({
  listQueueItems: (...args: unknown[]) => listQueueItemsMock(...args),
}));

import { NotificationBell } from "@/components/notifications/NotificationBell";

function makeItem(over: Partial<PublicQueueItem> & Pick<PublicQueueItem, "id">): PublicQueueItem {
  return {
    test_id: "test_1",
    test_code: "TC-1",
    stand_id: "stand_1",
    test_run_id: null,
    retry_of_id: null,
    debug_mode: false,
    state: "running",
    rc: "1.8",
    kernel: "6.1",
    mode: "orel",
    created_at: "2026-09-18T10:00:00Z",
    started_at: "2026-09-18T10:00:00Z",
    finished_at: null,
    error: null,
    ...over,
  } as PublicQueueItem;
}

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
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

describe("NotificationBell — объединение worker-задач и прогонов отдела", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    navigateMock.mockReset();
    listTasksMock.mockReset();
    listQueueItemsMock.mockReset();
    listTasksMock.mockResolvedValue(page([]));
    listQueueItemsMock.mockResolvedValue(page([]));
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("переход теста отдела в failed даёт непрочитанное уведомление и toast", async () => {
    listQueueItemsMock.mockResolvedValue(page([makeItem({ id: "qi1", state: "running" })]));
    renderBell();
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.queryByText(/непрочитанных/)).not.toBeInTheDocument();

    listQueueItemsMock.mockResolvedValue(
      page([makeItem({ id: "qi1", state: "failed", finished_at: "2026-09-18T10:05:00Z" })]),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(
      screen.getByRole("button", { name: /Уведомления: 1/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/завершился с ошибкой/)).toBeInTheDocument();
  });

  it("бейдж суммирует непрочитанные из обоих источников", async () => {
    listTasksMock.mockResolvedValue(
      page([{ id: "t1", kind: "server.prepare", status: "running", created_at: "2026-09-18T09:00:00Z", finished_at: null, retry_count: 0 }]),
    );
    listQueueItemsMock.mockResolvedValue(page([makeItem({ id: "qi1", state: "running" })]));
    renderBell();
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    listTasksMock.mockResolvedValue(
      page([{ id: "t1", kind: "server.prepare", status: "succeeded", created_at: "2026-09-18T09:00:00Z", finished_at: "2026-09-18T09:01:00Z", retry_count: 0 }]),
    );
    listQueueItemsMock.mockResolvedValue(
      page([makeItem({ id: "qi1", state: "skipped", finished_at: "2026-09-18T09:02:00Z" })]),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(
      screen.getByRole("button", { name: /Уведомления: 2/ }),
    ).toBeInTheDocument();
  });

  it("клик по прогону отдела ведёт на /testing/runs", async () => {
    listQueueItemsMock.mockResolvedValue(page([makeItem({ id: "qi1", state: "running" })]));
    renderBell();
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    listQueueItemsMock.mockResolvedValue(
      page([makeItem({ id: "qi1", state: "succeeded", finished_at: "2026-09-18T10:05:00Z" })]),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    fireEvent.click(screen.getByRole("button", { name: /Уведомления: 1/ }));
    fireEvent.click(screen.getByText("TC-1"));

    expect(navigateMock).toHaveBeenCalledWith("/testing/runs");
  });
});
