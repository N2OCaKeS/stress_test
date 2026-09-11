import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getAcsSettingsMock = vi.fn();
const updateAcsSettingsMock = vi.fn();
const getAcsDepartmentAccessMock = vi.fn();
const updateAcsDepartmentAccessMock = vi.fn();
vi.mock("@/api/server/acsSettings", () => ({
  get getAcsSettings() {
    return getAcsSettingsMock;
  },
  get updateAcsSettings() {
    return updateAcsSettingsMock;
  },
  get getAcsDepartmentAccess() {
    return getAcsDepartmentAccessMock;
  },
  get updateAcsDepartmentAccess() {
    return updateAcsDepartmentAccessMock;
  },
}));

const listDepartmentsMock = vi.fn();
vi.mock("@/api/auth/departments", () => ({
  get listDepartments() {
    return listDepartmentsMock;
  },
}));

import { ServicesAcsSettings } from "@/pages/admin/services/ServicesAcsSettings";

function makeSettings(over: Record<string, unknown> = {}) {
  return {
    enabled: false,
    acs_url: null,
    password_is_set: false,
    ...over,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesAcsSettings />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesAcsSettings", () => {
  beforeEach(() => {
    getAcsSettingsMock.mockReset();
    updateAcsSettingsMock.mockReset();
    getAcsDepartmentAccessMock.mockReset();
    updateAcsDepartmentAccessMock.mockReset();
    listDepartmentsMock.mockReset();
    getAcsDepartmentAccessMock.mockResolvedValue([]);
    listDepartmentsMock.mockResolvedValue([
      { id: "dep_a", name: "Alpha", user_count: 0, created_at: "" },
      { id: "dep_b", name: "Beta", user_count: 0, created_at: "" },
    ]);
  });

  it("грузит настройки и показывает URL / статус пароля", async () => {
    getAcsSettingsMock.mockResolvedValue(
      makeSettings({
        enabled: true,
        acs_url: "https://acs.example.ru/",
        password_is_set: true,
      }),
    );
    renderPage();
    expect(
      await screen.findByDisplayValue("https://acs.example.ru/"),
    ).toBeInTheDocument();
    expect(screen.getByText(/задан/)).toBeInTheDocument();
  });

  it("сохранение настроек зовёт updateAcsSettings с текущими полями", async () => {
    getAcsSettingsMock.mockResolvedValue(makeSettings());
    updateAcsSettingsMock.mockResolvedValue(makeSettings());
    renderPage();

    const urlInput = (await screen.findByPlaceholderText(
      "https://acs.example.ru/",
    )) as HTMLInputElement;
    fireEvent.change(urlInput, {
      target: { value: "https://acs.example.ru/" },
    });
    // Ждём, пока React реально осядет с новым значением, прежде чем кликать
    // дальше — под нагрузкой полного сьюта несколько synchronous fireEvent
    // подряд без точки синхронизации иногда обгоняли commit предыдущего
    // изменения, и `handleSave` читал устаревший (пустой) `acsUrl`.
    await waitFor(() => expect(urlInput.value).toBe("https://acs.example.ru/"));

    const enabledCheckbox = screen.getByLabelText(
      "Снимки ACS включены",
    ) as HTMLInputElement;
    fireEvent.click(enabledCheckbox);
    await waitFor(() => expect(enabledCheckbox.checked).toBe(true));

    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => expect(updateAcsSettingsMock).toHaveBeenCalledTimes(1));
    const payload = updateAcsSettingsMock.mock.calls[0][0];
    expect(payload.enabled).toBe(true);
    expect(payload.acs_url).toBe("https://acs.example.ru/");
    expect(payload.acs_password).toBeUndefined();
  });

  it("таблица отделов сохраняет только изменённые флаги", async () => {
    getAcsSettingsMock.mockResolvedValue(makeSettings());
    getAcsDepartmentAccessMock.mockResolvedValue([
      { department_id: "dep_a", is_enabled: true, updated_at: null, created_by: null },
    ]);
    updateAcsDepartmentAccessMock.mockResolvedValue([]);
    renderPage();

    // Дождаться применения загруженных флагов к состоянию формы.
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Alpha" })).toBeChecked());
    const betaRow = await screen.findByText("Beta");
    const betaCheckbox = betaRow.closest("label")!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    fireEvent.click(betaCheckbox);
    await waitFor(() => expect(betaCheckbox.checked).toBe(true));

    // Настройки (cfgQ) и таблица отделов (deptsQ/accessQ) — независимые
    // запросы; "Beta" появляется, как только загрузилась таблица, но кнопка
    // «Сохранить» настроек рендерится отдельно от своего запроса — ждём,
    // пока обе появятся, прежде чем полагаться на индекс [1].
    const saveButtons = await waitFor(() => {
      const buttons = screen.getAllByRole("button", { name: /Сохранить/ });
      expect(buttons.length).toBe(2);
      return buttons;
    });
    fireEvent.click(saveButtons[1]);

    await waitFor(() =>
      expect(updateAcsDepartmentAccessMock).toHaveBeenCalledTimes(1),
    );
    expect(updateAcsDepartmentAccessMock.mock.calls[0][0]).toEqual([
      { department_id: "dep_b", is_enabled: true },
    ]);
  });
});
