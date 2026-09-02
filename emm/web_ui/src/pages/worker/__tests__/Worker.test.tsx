import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";

// Сетевой вызов списка задач мокаем pending-промисом — нужен только
// smoke-рендер: aside-плейсхолдер поиска и loading state.
vi.mock("@/api/server/misc", () => ({
  listTasks: vi.fn(() => new Promise(() => {})),
  getTask: vi.fn(() => new Promise(() => {})),
  cancelTask: vi.fn(),
}));

import { Worker } from "@/pages/worker/Worker";

function renderWorker() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={["/worker"]}>
            <Worker />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Worker (tasks page) smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("рендерится без ошибок и показывает loading state списка", () => {
    renderWorker();
    // Дефолтная персона (alice / dep_admin) не заблокирована — видит aside.
    expect(
      screen.getByPlaceholderText(/Поиск по 0 задачам/),
    ).toBeInTheDocument();
    // listTasks ещё pending — индикатор «Загрузка…».
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
    // Workzone без выбранной задачи — EmptyDetail.
    expect(
      screen.getByText(/Выберите задачу слева для просмотра деталей/),
    ).toBeInTheDocument();
  });
});
