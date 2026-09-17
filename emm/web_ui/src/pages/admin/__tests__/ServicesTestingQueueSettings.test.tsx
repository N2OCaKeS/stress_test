import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_1", platform_role: "dep_admin", service_roles: {} },
  }),
}));

const getDepartmentTestSettingsMock = vi.fn();
const upsertDepartmentTestSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentTestSettings", () => ({
  getDepartmentTestSettings: (...a: unknown[]) => getDepartmentTestSettingsMock(...a),
  upsertDepartmentTestSettings: (...a: unknown[]) => upsertDepartmentTestSettingsMock(...a),
}));

import { ServicesTestingQueueSettings } from "@/pages/admin/services/ServicesTestingQueueSettings";

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesTestingQueueSettings />
      </ToastProvider>
    </ThemeProvider>,
  );
}

function defaults(over: Record<string, unknown> = {}) {
  return {
    id: null,
    department_id: "dep_1",
    retry_enabled: true,
    test_username: "u",
    activity_report_auto_generate: false,
    created_at: null,
    updated_at: null,
    ...over,
  };
}

describe("ServicesTestingQueueSettings — очередь/ретраи тестирования отдела (department_test_settings)", () => {
  beforeEach(() => {
    getDepartmentTestSettingsMock.mockReset().mockResolvedValue(defaults());
    upsertDepartmentTestSettingsMock.mockReset();
  });

  it("показывает дефолты (retry_enabled, test_username)", async () => {
    renderPage();
    expect(await screen.findByText("Очередь и повторы тестирования")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByPlaceholderText("u")).toHaveValue("u"));
    expect(screen.getByRole("switch")).toBeChecked();
    // Расписание отчёта переехало на страницу отчёта — здесь его больше нет.
    expect(screen.queryByText(/Расписание отчёта по активностям/)).not.toBeInTheDocument();
  });

  it("сохранение зовёт upsertDepartmentTestSettings с изменёнными полями", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults({ test_username: "tester" }));
    renderPage();
    await screen.findByText("Очередь и повторы тестирования");
    const usernameInput = (await screen.findByPlaceholderText("u")) as HTMLInputElement;
    await waitFor(() => expect(usernameInput.value).toBe("u"));

    const retrySwitch = screen.getByRole("switch") as HTMLInputElement;
    fireEvent.click(retrySwitch);
    await waitFor(() => expect(retrySwitch.checked).toBe(false));

    fireEvent.change(usernameInput, { target: { value: "tester" } });
    await waitFor(() => expect(usernameInput.value).toBe("tester"));

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      retry_enabled: false,
      test_username: "tester",
    });
  });

  it("не даёт сохранить пустое имя пользователя теста", async () => {
    renderPage();
    await screen.findByText("Очередь и повторы тестирования");

    const usernameInput = (await screen.findByPlaceholderText("u")) as HTMLInputElement;
    await waitFor(() => expect(usernameInput.value).toBe("u"));

    fireEvent.change(usernameInput, { target: { value: "" } });
    await waitFor(() => expect(usernameInput.value).toBe(""));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Укажите имя пользователя исполнения теста.")).toBeInTheDocument();
    expect(upsertDepartmentTestSettingsMock).not.toHaveBeenCalled();
  });
});
