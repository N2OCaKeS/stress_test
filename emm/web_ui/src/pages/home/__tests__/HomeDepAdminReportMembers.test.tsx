import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

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
vi.mock("@/api/testing/departmentTestSettings", () => ({
  getDepartmentTestSettings: vi.fn().mockResolvedValue({
    id: null,
    department_id: "dep_1",
    retry_enabled: true,
    test_username: "u",
    activity_report_schedule: null,
    created_at: null,
    updated_at: null,
  }),
  upsertDepartmentTestSettings: vi.fn(),
}));

const listDepartmentReportMembersMock = vi.fn();
const createDepartmentReportMemberMock = vi.fn();
const updateDepartmentReportMemberMock = vi.fn();
const deleteDepartmentReportMemberMock = vi.fn();
vi.mock("@/api/testing/departmentReportMembers", () => ({
  listDepartmentReportMembers: (...a: unknown[]) => listDepartmentReportMembersMock(...a),
  createDepartmentReportMember: (...a: unknown[]) => createDepartmentReportMemberMock(...a),
  updateDepartmentReportMember: (...a: unknown[]) => updateDepartmentReportMemberMock(...a),
  deleteDepartmentReportMember: (...a: unknown[]) => deleteDepartmentReportMemberMock(...a),
}));

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: async () => true }),
}));

import { HomeDepAdmin } from "@/pages/home/HomeDepAdmin";

function renderHome() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <HomeDepAdmin />
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function member(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "drm_1",
    department_id: "dep_1",
    display_name: "Иванов Иван",
    bitbucket_username: "ivanov",
    jira_author_name: "Ivan Ivanov",
    jira_tempo_worker_key: "JIRAUSER1",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: "usr_1",
    ...overrides,
  };
}

describe("HomeDepAdmin — сотрудники отдела для HR-отчёта (department_report_members)", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listDepartmentReportMembersMock.mockReset().mockResolvedValue({
      items: [member()],
      total: 1,
      limit: 200,
      offset: 0,
    });
    createDepartmentReportMemberMock.mockReset();
    updateDepartmentReportMemberMock.mockReset();
    deleteDepartmentReportMemberMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("грузит и показывает список сотрудников", async () => {
    renderHome();
    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.getByText(/ivanov/)).toBeInTheDocument();
  });

  it("список сотрудников зовётся с department_id из persona", async () => {
    renderHome();
    await screen.findByText("Иванов Иван");
    expect(listDepartmentReportMembersMock).toHaveBeenCalledWith("dep_1", { limit: 200 });
  });

  it("добавление сотрудника зовёт createDepartmentReportMember с введёнными полями", async () => {
    createDepartmentReportMemberMock.mockResolvedValue(member({ id: "drm_2", display_name: "Петров Пётр" }));
    renderHome();
    await screen.findByText("Иванов Иван");

    fireEvent.click(screen.getByRole("button", { name: /Добавить/ }));

    const nameInput = await screen.findByPlaceholderText("Иванов Иван");
    fireEvent.change(nameInput, { target: { value: "Петров Пётр" } });
    fireEvent.change(screen.getByPlaceholderText("ivanov"), { target: { value: "petrov" } });

    // «Сохранить» также подписаны disabled-кнопки настроек интеграции/очереди
    // на этой же странице — берём последнюю (модалка рендерится порталом
    // последней в document.body).
    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() => expect(createDepartmentReportMemberMock).toHaveBeenCalledTimes(1));
    expect(createDepartmentReportMemberMock).toHaveBeenCalledWith("dep_1", {
      display_name: "Петров Пётр",
      bitbucket_username: "petrov",
      jira_author_name: null,
      jira_tempo_worker_key: null,
      is_active: true,
    });
  });

  it("удаление сотрудника с подтверждением зовёт deleteDepartmentReportMember", async () => {
    deleteDepartmentReportMemberMock.mockResolvedValue({ ok: true });
    renderHome();
    await screen.findByText("Иванов Иван");

    fireEvent.click(screen.getByRole("button", { name: /Удалить/ }));

    await waitFor(() =>
      expect(deleteDepartmentReportMemberMock).toHaveBeenCalledWith("dep_1", "drm_1"),
    );
  });

  it("редактирование сотрудника зовёт updateDepartmentReportMember", async () => {
    updateDepartmentReportMemberMock.mockResolvedValue(member({ display_name: "Иванов И.И." }));
    renderHome();
    await screen.findByText("Иванов Иван");

    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));

    const nameInput = await screen.findByDisplayValue("Иванов Иван");
    fireEvent.change(nameInput, { target: { value: "Иванов И.И." } });
    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() =>
      expect(updateDepartmentReportMemberMock).toHaveBeenCalledWith("dep_1", "drm_1", {
        display_name: "Иванов И.И.",
        bitbucket_username: "ivanov",
        jira_author_name: "Ivan Ivanov",
        jira_tempo_worker_key: "JIRAUSER1",
        is_active: true,
      }),
    );
  });
});
