import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const listMock = vi.fn();
const createMock = vi.fn();
const updateMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/api/server/serverCategories", () => ({
  listServerCategories: (...a: unknown[]) => listMock(...a),
  createServerCategory: (...a: unknown[]) => createMock(...a),
  updateServerCategory: (...a: unknown[]) => updateMock(...a),
  deleteServerCategory: (...a: unknown[]) => deleteMock(...a),
  getServerCategoryByCode: vi.fn(),
}));

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: {
      id: "u1",
      dept_id: "core",
      platform_role: "dep_admin",
      service_roles: {},
    },
  }),
}));

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: async () => true }),
}));

import { ServicesServerCategories } from "@/pages/admin/services/ServicesServerCategories";

function category(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "cat_1",
    code: "low_server",
    label: "LowServer",
    description: "Слабые тестовые сервера",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: "usr_admin",
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <ServicesServerCategories />
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("ServicesServerCategories", () => {
  beforeEach(() => {
    // Страница ветвится на mockMode — тесту нужен реальный API-путь, чтобы
    // проверить вызовы моков listServerCategories/create/update/delete.
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listMock.mockReset();
    createMock.mockReset();
    updateMock.mockReset();
    deleteMock.mockReset();
    listMock.mockResolvedValue({
      items: [category()],
      total: 1,
      limit: 500,
      offset: 0,
    });
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("грузит и показывает список категорий", async () => {
    renderPage();
    expect(await screen.findByText("LowServer")).toBeInTheDocument();
    expect(screen.getByText("low_server")).toBeInTheDocument();
  });

  it("создание категории зовёт createServerCategory с введёнными полями", async () => {
    createMock.mockResolvedValue(category({ id: "cat_2", code: "high_server", label: "HighServer" }));
    renderPage();
    await screen.findByText("LowServer");

    fireEvent.click(screen.getAllByRole("button", { name: /Создать/ })[0]);

    const codeInput = await screen.findByPlaceholderText("low_server");
    fireEvent.change(codeInput, { target: { value: "high_server" } });
    const labelInput = screen.getByPlaceholderText("LowServer");
    fireEvent.change(labelInput, { target: { value: "HighServer" } });

    // Заголовок держит собственную кнопку «Создать» рядом с формой — берём
    // последнюю (submit формы).
    const submitButtons = screen.getAllByRole("button", { name: "Создать" });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock).toHaveBeenCalledWith({
      code: "high_server",
      label: "HighServer",
      description: undefined,
    });
  });

  it("удаление категории с подтверждением зовёт deleteServerCategory", async () => {
    deleteMock.mockResolvedValue(undefined);
    renderPage();
    fireEvent.click(await screen.findByText("LowServer"));

    fireEvent.click(await screen.findByRole("button", { name: /Удалить/ }));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("cat_1"));
  });
});
