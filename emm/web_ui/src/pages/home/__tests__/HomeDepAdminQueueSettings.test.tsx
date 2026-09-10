import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: {
      id: "u1",
      username: "alice",
      dept_id: "dep_1",
      platform_role: "dep_admin",
      service_roles: {},
      accessible_services: [],
    },
  }),
}));

vi.mock("@/api/auth/users", () => ({
  listUsers: vi.fn(),
  listUsersByDepartment: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("@/api/auth/groups", () => ({
  listGroups: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/api/auth/bots", () => ({
  listBots: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/api/server/misc", () => ({
  getHostDiskUsage: vi.fn().mockResolvedValue({ paths: [] }),
  listTasks: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 }),
}));
vi.mock("@/api/testing/departmentActivityReports", () => ({
  generateDepartmentActivityReport: vi.fn(),
  listDepartmentActivityReports: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 }),
}));
vi.mock("@/api/testing/departmentIntegrationSettings", () => ({
  getDepartmentIntegrationSettings: vi.fn().mockResolvedValue({
    id: null,
    department_id: "dep_1",
    credential_id: null,
    jira_base_url: null,
    confluence_base_url: null,
    bitbucket_base_url: null,
    bitbucket_project_key: null,
    bitbucket_repo_slug: null,
    bitbucket_credential_id: null,
    jira_board_id: null,
    tempo_team_id: null,
    confluence_report_page_space: null,
    confluence_report_parent_page_title: null,
    created_at: null,
    updated_at: null,
  }),
  upsertDepartmentIntegrationSettings: vi.fn(),
}));
vi.mock("@/api/testing/departmentReportMembers", () => ({
  listDepartmentReportMembers: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 }),
  createDepartmentReportMember: vi.fn(),
  updateDepartmentReportMember: vi.fn(),
  deleteDepartmentReportMember: vi.fn(),
}));

const getDepartmentTestSettingsMock = vi.fn();
const upsertDepartmentTestSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentTestSettings", () => ({
  getDepartmentTestSettings: (...a: unknown[]) => getDepartmentTestSettingsMock(...a),
  upsertDepartmentTestSettings: (...a: unknown[]) => upsertDepartmentTestSettingsMock(...a),
}));

import { HomeDepAdmin } from "@/pages/home/HomeDepAdmin";

function renderHome() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <ConfirmProvider>
            <HomeDepAdmin />
          </ConfirmProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function defaults(over: Record<string, unknown> = {}) {
  return {
    id: null,
    department_id: "dep_1",
    retry_enabled: true,
    test_username: "u",
    activity_report_schedule: null,
    created_at: null,
    updated_at: null,
    ...over,
  };
}

describe("HomeDepAdmin — очередь/ретраи тестирования отдела (department_test_settings)", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    getDepartmentTestSettingsMock.mockReset().mockResolvedValue(defaults());
    upsertDepartmentTestSettingsMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("показывает дефолты (retry_enabled, test_username)", async () => {
    renderHome();
    expect(await screen.findByText("Очередь и повторы тестирования")).toBeInTheDocument();
    // GET резолвится асинхронно — ждём, пока sync-эффект перенесёт загруженные
    // значения в форму, а не читаем исходное состояние useState.
    await waitFor(() => expect(screen.getByPlaceholderText("u")).toHaveValue("u"));
    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("сохранение зовёт upsertDepartmentTestSettings с изменёнными полями", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults({ test_username: "tester" }));
    renderHome();
    await screen.findByText("Очередь и повторы тестирования");
    // Дожидаемся, пока GET осядет в форме, прежде чем взаимодействовать —
    // иначе sync-эффект может отработать ПОСЛЕ клика/ввода и затереть их
    // (та же гонка, что и в ServicesAcsSettings).
    const usernameInput = (await screen.findByPlaceholderText("u")) as HTMLInputElement;
    await waitFor(() => expect(usernameInput.value).toBe("u"));

    const retrySwitch = screen.getByRole("switch") as HTMLInputElement;
    fireEvent.click(retrySwitch);
    await waitFor(() => expect(retrySwitch.checked).toBe(false));

    fireEvent.change(usernameInput, { target: { value: "tester" } });
    await waitFor(() => expect(usernameInput.value).toBe("tester"));

    const scheduleInput = screen.getByPlaceholderText("например, 1 числа месяца") as HTMLInputElement;
    fireEvent.change(scheduleInput, { target: { value: "1 числа" } });
    await waitFor(() => expect(scheduleInput.value).toBe("1 числа"));

    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButtons[1]);

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      retry_enabled: false,
      test_username: "tester",
      activity_report_schedule: "1 числа",
    });
  });

  it("не даёт сохранить пустое имя пользователя теста", async () => {
    renderHome();
    await screen.findByText("Очередь и повторы тестирования");

    const usernameInput = (await screen.findByPlaceholderText("u")) as HTMLInputElement;
    await waitFor(() => expect(usernameInput.value).toBe("u"));

    fireEvent.change(usernameInput, { target: { value: "" } });
    await waitFor(() => expect(usernameInput.value).toBe(""));
    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButtons[1]);

    expect(await screen.findByText("Укажите имя пользователя исполнения теста.")).toBeInTheDocument();
    expect(upsertDepartmentTestSettingsMock).not.toHaveBeenCalled();
  });
});
