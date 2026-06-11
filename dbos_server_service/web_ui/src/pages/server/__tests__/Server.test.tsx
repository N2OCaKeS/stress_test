import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";

// Сетевые вызовы страницы списка моки́м — нам важен только smoke-рендер
// (заголовок панели, плейсхолдер поиска, loading state списка).
vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => new Promise(() => {})),
  getServer: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
}));

import { Server } from "@/pages/server/Server";

function renderServer() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={["/servers"]}>
            <Server />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Server (list page) smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("рендерится без ошибок и показывает loading state списка", () => {
    renderServer();
    // Aside-плейсхолдер поиска присутствует.
    expect(
      screen.getByPlaceholderText(/Поиск по 0 серверам/),
    ).toBeInTheDocument();
    // listServers ещё в pending — должен отрисоваться индикатор «Загрузка…».
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
    // EmptyPane workzone'ы (id не выбран, action не задан).
    expect(
      screen.getByText(/Выберите сервер слева для просмотра деталей/),
    ).toBeInTheDocument();
  });
});
