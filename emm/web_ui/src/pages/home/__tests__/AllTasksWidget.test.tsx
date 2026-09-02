import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// listTasks мокаем готовым dept-scope ответом (без created_by-фильтра — панель
// его и не передаёт).
const listTasks = vi.fn();
vi.mock("@/api/server/misc", () => ({
  listTasks: (...args: unknown[]) => listTasks(...args),
}));

// Перехватываем navigate, чтобы проверить роутинг по клику.
const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigate };
});

import { AllTasksWidget } from "@/pages/home/widgets/AllTasksWidget";

function renderWidget() {
  return render(
    <MemoryRouter>
      <AllTasksWidget />
    </MemoryRouter>,
  );
}

const SAMPLE = {
  items: [
    {
      id: "task-1",
      kind: "power.cycle",
      status: "running",
      created_by: "alice",
      department_id: "dep-1",
      created_at: "2026-06-23T10:00:00Z",
      retry_count: 0,
    },
    {
      id: "task-2",
      kind: "ssh.exec",
      status: "succeeded",
      created_by: "bob",
      department_id: "dep-1",
      created_at: "2026-06-23T09:30:00Z",
      retry_count: 0,
    },
  ],
  total: 2,
};

describe("AllTasksWidget", () => {
  beforeEach(() => {
    listTasks.mockReset();
    navigate.mockReset();
  });

  it("рендерит задачи отдела из listTasks без фильтра created_by", async () => {
    listTasks.mockResolvedValue(SAMPLE);
    renderWidget();

    await screen.findByText("power.cycle");
    expect(screen.getByText("ssh.exec")).toBeInTheDocument();
    expect(screen.getByText(/alice/)).toBeInTheDocument();
    expect(screen.getByText(/bob/)).toBeInTheDocument();

    // created_by в query не уходит — backend сам режет по dept-scope.
    expect(listTasks).toHaveBeenCalled();
    const arg = listTasks.mock.calls[0][0] ?? {};
    expect(arg).not.toHaveProperty("created_by");
  });

  it("клик по строке зовёт navigate('/tasks/<id>')", async () => {
    listTasks.mockResolvedValue(SAMPLE);
    renderWidget();

    const row = await screen.findByText("power.cycle");
    fireEvent.click(row);

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/tasks/task-1"),
    );
  });
});
