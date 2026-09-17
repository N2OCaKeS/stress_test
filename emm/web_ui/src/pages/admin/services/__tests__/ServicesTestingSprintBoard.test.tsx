import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: {
      id: "u1",
      username: "dep_admin1",
      dept_id: "dep_1",
      platform_role: "dep_admin",
      service_roles: {},
      accessible_services: [],
    },
  }),
}));

const getDepartmentSprintBoardMock = vi.fn();
const getDepartmentIntegrationSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentIntegrationSettings", () => ({
  getDepartmentSprintBoard: (...a: unknown[]) => getDepartmentSprintBoardMock(...a),
  getDepartmentIntegrationSettings: (...a: unknown[]) => getDepartmentIntegrationSettingsMock(...a),
}));

import { ServicesTestingSprintBoard } from "@/pages/admin/services/ServicesTestingSprintBoard";

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ServicesTestingSprintBoard />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function emptyIntegrationSettings() {
  return {
    id: null,
    department_id: "dep_1",
    credential_id: null,
    jira_base_url: "https://jira.example",
    confluence_base_url: null,
    confluence_credential_id: null,
    bitbucket_base_url: null,
    bitbucket_project_key: null,
    bitbucket_repo_slug: null,
    bitbucket_credential_id: null,
    jira_board_id: "340",
    tempo_team_id: null,
    confluence_report_page_space: null,
    confluence_report_parent_page_title: null,
    stp_matrix_confluence_space: null,
    stp_matrix_confluence_root_page_title: null,
    created_at: null,
    updated_at: null,
  };
}

describe("ServicesTestingSprintBoard", () => {
  beforeEach(() => {
    getDepartmentSprintBoardMock.mockReset();
    getDepartmentIntegrationSettingsMock.mockReset().mockResolvedValue(emptyIntegrationSettings());
  });

  it("не настроено — показывает подсказку без падения", async () => {
    getDepartmentSprintBoardMock.mockResolvedValue({
      configured: false,
      sprint: null,
      columns: [],
      warning: "Jira integration is not configured for this department",
    });
    renderPage();
    expect(await screen.findByText(/Jira для этого отдела не настроена/)).toBeInTheDocument();
  });

  it("нет активного спринта — чистый пустой результат, не ошибка", async () => {
    getDepartmentSprintBoardMock.mockResolvedValue({
      configured: true,
      sprint: null,
      columns: [],
      warning: "no active sprint on this board",
    });
    renderPage();
    expect(await screen.findByText("no active sprint on this board")).toBeInTheDocument();
  });

  it("рендерит колонки с issue и ссылкой на реальную Jira", async () => {
    getDepartmentSprintBoardMock.mockResolvedValue({
      configured: true,
      sprint: { id: 42, name: "Sprint 42", start_date: "2026-09-01", end_date: "2026-09-14" },
      columns: [
        {
          status: "To Do",
          issues: [
            {
              key: "QA-1",
              summary: "Some task",
              status: "To Do",
              status_category: "new",
              assignee: null,
              issue_type: "Task",
            },
          ],
        },
        {
          status: "In Progress",
          issues: [],
        },
      ],
      warning: null,
    });
    renderPage();

    expect(await screen.findByText("Sprint 42")).toBeInTheDocument();
    expect(screen.getByText("To Do")).toBeInTheDocument();
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("Some task")).toBeInTheDocument();

    const link = await screen.findByRole("link", { name: /QA-1/ });
    expect(link).toHaveAttribute("href", "https://jira.example/browse/QA-1");
  });
});
