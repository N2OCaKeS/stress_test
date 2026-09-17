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

const listUsersByDepartmentMock = vi.fn();
vi.mock("@/api/auth/users", () => ({
  listUsers: vi.fn(),
  listUsersByDepartment: (...a: unknown[]) => listUsersByDepartmentMock(...a),
}));

const listGroupsMock = vi.fn();
vi.mock("@/api/auth/groups", () => ({
  listGroups: (...a: unknown[]) => listGroupsMock(...a),
}));

const listBotsMock = vi.fn();
vi.mock("@/api/auth/bots", () => ({
  listBots: (...a: unknown[]) => listBotsMock(...a),
}));

const getHostDiskUsageMock = vi.fn();
const listTasksMock = vi.fn();
vi.mock("@/api/server/misc", () => ({
  getHostDiskUsage: (...a: unknown[]) => getHostDiskUsageMock(...a),
  listTasks: (...a: unknown[]) => listTasksMock(...a),
}));

const generateDepartmentActivityReportMock = vi.fn();
const listDepartmentActivityReportsMock = vi.fn();
vi.mock("@/api/testing/departmentActivityReports", () => ({
  generateDepartmentActivityReport: (...a: unknown[]) => generateDepartmentActivityReportMock(...a),
  listDepartmentActivityReports: (...a: unknown[]) => listDepartmentActivityReportsMock(...a),
}));

const getDepartmentIntegrationSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentIntegrationSettings", () => ({
  getDepartmentIntegrationSettings: (...a: unknown[]) => getDepartmentIntegrationSettingsMock(...a),
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

describe("HomeDepAdmin — отчёт по активностям сотрудников отдела", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listUsersByDepartmentMock.mockReset().mockResolvedValue({ items: [], total: 0 });
    listGroupsMock.mockReset().mockResolvedValue([]);
    listBotsMock.mockReset().mockResolvedValue([]);
    getHostDiskUsageMock.mockReset().mockResolvedValue({ paths: [] });
    listTasksMock.mockReset().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 });
    generateDepartmentActivityReportMock.mockReset();
    // "Сегодня" в тестовом окружении — реальный системный час (сентябрь
    // 2026), поэтому диапазон по умолчанию — июнь..сентябрь 2026, а отчёт за
    // август 2026 в него попадает.
    listDepartmentActivityReportsMock.mockReset().mockResolvedValue({
      items: [
        {
          id: "rep_1",
          department_id: "dep_1",
          period: "2026-08",
          generated_at: "2026-08-05T10:00:00Z",
          generated_by: "usr_1",
          confluence_page_id: "12345",
          status: "done",
          error: null,
        },
      ],
      total: 1,
      limit: 500,
      offset: 0,
    });
    getDepartmentIntegrationSettingsMock.mockReset().mockResolvedValue({
      id: "int_1",
      department_id: "dep_1",
      credential_id: null,
      jira_base_url: null,
      confluence_base_url: "https://confluence.astralinux.ru",
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
    });
    getDepartmentTestSettingsMock.mockReset().mockResolvedValue({
      id: null,
      department_id: "dep_1",
      retry_enabled: true,
      test_username: "u",
      activity_report_auto_generate: false,
      created_at: null,
      updated_at: null,
    });
    upsertDepartmentTestSettingsMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("показывает блок «Отчёт по активностям сотрудников отдела» с кнопкой генерации и историей по умолчанию за 3 месяца", async () => {
    renderHome();
    expect(await screen.findByText("Отчёт по активностям сотрудников отдела")).toBeInTheDocument();
    expect(await screen.findByText(/августа 2026/)).toBeInTheDocument();
    expect(screen.getByText("готов")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Открыть в Confluence/ })).toHaveAttribute(
      "href",
      "https://confluence.astralinux.ru/pages/viewpage.action?pageId=12345",
    );
    // Диапазон по умолчанию — 3 месяца назад..текущий месяц (июнь..сентябрь
    // 2026, 4 месяца): один готов (август), три остальных — "не создан".
    expect(screen.getAllByText("не создан").length).toBe(3);
  });

  it("клик по «Сгенерировать отчёт» зовёт generateDepartmentActivityReport с department_id и периодом предыдущего месяца", async () => {
    generateDepartmentActivityReportMock.mockResolvedValue({
      id: "rep_2",
      department_id: "dep_1",
      period: "2026-08",
      generated_at: "2026-09-07T10:00:00Z",
      generated_by: "u1",
      confluence_page_id: null,
      status: "generating",
      error: null,
    });
    renderHome();
    await screen.findByText("Отчёт по активностям сотрудников отдела");

    fireEvent.click(screen.getByRole("button", { name: /Сгенерировать отчёт/ }));

    await waitFor(() => expect(generateDepartmentActivityReportMock).toHaveBeenCalledTimes(1));
    // Дефолт формы генерации — предыдущий месяц относительно текущего (не
    // текущий — он ещё не закончился и не может быть полным отчётом).
    expect(generateDepartmentActivityReportMock).toHaveBeenCalledWith("dep_1", { period: "2026-08" });
    // Успешная генерация перезапрашивает историю.
    await waitFor(() => expect(listDepartmentActivityReportsMock.mock.calls.length).toBeGreaterThan(1));
  });

  it("показывает сообщение об ошибке, если генерация упала", async () => {
    generateDepartmentActivityReportMock.mockRejectedValue(new Error("boom"));
    renderHome();
    await screen.findByText("Отчёт по активностям сотрудников отдела");

    fireEvent.click(screen.getByRole("button", { name: /Сгенерировать отчёт/ }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("переключатель авто-генерации зовёт upsertDepartmentTestSettings с activity_report_auto_generate", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue({
      id: "dts_1",
      department_id: "dep_1",
      retry_enabled: true,
      test_username: "u",
      activity_report_auto_generate: true,
      created_at: null,
      updated_at: null,
    });
    renderHome();
    const toggle = await screen.findByLabelText(/Генерировать автоматически 1 числа месяца/);
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
        activity_report_auto_generate: true,
      }),
    );
  });
});
