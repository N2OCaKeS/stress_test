import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";

// Сетевые вызовы страницы мокаем — нужен только smoke-рендер (заголовок,
// плейсхолдер поиска, loading state). Список серверов держим в pending.
vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => new Promise(() => {})),
}));

vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() => new Promise(() => {})),
  getAccount: vi.fn(() => new Promise(() => {})),
}));

// LabelsProvider тащит сетевые загрузки имён — подменяем только хук карты
// серверов, остальные экспорты (useDeptLabelOpt для LeftPanel и т.д.) берём из
// оригинала.
vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  return {
    ...actual,
    useServerMap: () => new Map<string, string>(),
  };
});

import { ServerUsers } from "@/pages/server/ServerUsers";

function renderPage() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={["/server/users"]}>
            <ServerUsers />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("ServerUsers (fleet account list) smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("рендерится и показывает loading state, пока грузятся серверы", () => {
    renderPage();
    expect(screen.getByText(/Пользователи серверов/)).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/Поиск по 0 аккаунтам/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
  });
});
