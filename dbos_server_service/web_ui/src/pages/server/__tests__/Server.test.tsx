import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";

// Диалоговый провайдер монтируется в App.tsx; в smoke-рендере страницы он не
// нужен — мокаем хук no-op'ом, чтобы не тащить Radix-портал в jsdom.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => false),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

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

import { listDepartments } from "@/api/auth/departments";
import { Server } from "@/pages/server/Server";

function renderServer(entry = "/servers") {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[entry]}>
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

  it("создание для dep_admin: отдел read-only и listDepartments не дёргается", () => {
    // Дефолтная persona — alice (dep_admin, dept core). listDepartments ей
    // отдаёт 403, поэтому запрос не должен уходить, а поле отдела — фиксировано.
    renderServer("/servers?action=new");
    expect(screen.getByText("Создание сервера")).toBeInTheDocument();
    // Поле отдела — read-only input с id отдела, а не выпадающий список.
    const deptInput = screen.getByDisplayValue("core");
    expect(deptInput).toHaveAttribute("readonly");
    expect(
      screen.getByText(/сервер создаётся в вашем отделе/),
    ).toBeInTheDocument();
    expect(listDepartments).not.toHaveBeenCalled();
  });
});
