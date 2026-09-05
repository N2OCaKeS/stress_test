import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getConfigMock = vi.fn();
const updateConfigMock = vi.fn();
vi.mock("@/api/auth/navLinks", () => ({
  get getNavLinksConfig() {
    return getConfigMock;
  },
  get updateNavLinksConfig() {
    return updateConfigMock;
  },
}));

const listDepartmentsMock = vi.fn();
vi.mock("@/api/auth/departments", () => ({
  get listDepartments() {
    return listDepartmentsMock;
  },
}));

import { ServicesNavLink } from "@/pages/admin/services/ServicesNavLink";

function makeConfig(over: Record<string, unknown> = {}) {
  return {
    enabled: false,
    label: "allta",
    url: null,
    all_departments: false,
    department_ids: [],
    ...over,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesNavLink />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesNavLink", () => {
  beforeEach(() => {
    getConfigMock.mockReset();
    updateConfigMock.mockReset();
    listDepartmentsMock.mockReset();
    listDepartmentsMock.mockResolvedValue([
      { id: "dep_a", name: "Alpha", user_count: 0, created_at: "" },
      { id: "dep_b", name: "Beta", user_count: 0, created_at: "" },
    ]);
  });

  it("грузит конфиг и показывает подпись/URL", async () => {
    getConfigMock.mockResolvedValue(
      makeConfig({
        enabled: true,
        label: "Allta App",
        url: "https://allta.example.ru/",
        all_departments: true,
      }),
    );
    updateConfigMock.mockResolvedValue(makeConfig());
    renderPage();
    expect(await screen.findByDisplayValue("Allta App")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("https://allta.example.ru/"),
    ).toBeInTheDocument();
  });

  it("сохранение зовёт updateNavLinksConfig с выбранными отделами", async () => {
    getConfigMock.mockResolvedValue(makeConfig());
    updateConfigMock.mockResolvedValue(makeConfig());
    renderPage();

    // Дожидаемся формы.
    const labelInput = await screen.findByPlaceholderText("allta");
    fireEvent.change(labelInput, { target: { value: "allta" } });

    const urlInput = screen.getByPlaceholderText("https://allta.example.ru/");
    fireEvent.change(urlInput, {
      target: { value: "https://allta.example.ru/" },
    });

    fireEvent.click(screen.getByLabelText("Кнопка включена"));

    // Отдел Beta из списка listDepartments.
    fireEvent.click(await screen.findByLabelText("Beta"));

    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(updateConfigMock).toHaveBeenCalledTimes(1));
    const payload = updateConfigMock.mock.calls[0][0];
    expect(payload.enabled).toBe(true);
    expect(payload.url).toBe("https://allta.example.ru/");
    expect(payload.all_departments).toBe(false);
    expect(payload.department_ids).toEqual(["dep_b"]);
  });
});
