import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

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

function emptySettings() {
  return {
    id: null,
    department_id: "dep_1",
    credential_id: null,
    jira_base_url: "https://jira.dev.internal",
    confluence_base_url: "https://confluence.dev.internal",
    confluence_credential_id: null,
    bitbucket_base_url: "https://bitbucket.dev.internal",
    bitbucket_project_key: "NTDEV",
    bitbucket_repo_slug: "allta-app",
    bitbucket_credential_id: null,
    jira_board_id: "340",
    tempo_team_id: "7",
    confluence_report_page_space: "NTDEV",
    confluence_report_parent_page_title: "Отчёты по активности (dev)",
    stp_matrix_confluence_space: "DEVQA",
    stp_matrix_confluence_root_page_title: "Состав тестового прогона",
    created_at: null,
    updated_at: null,
  };
}

const getDepartmentIntegrationSettingsMock = vi.fn();
const upsertDepartmentIntegrationSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentIntegrationSettings", () => ({
  getDepartmentIntegrationSettings: (...a: unknown[]) => getDepartmentIntegrationSettingsMock(...a),
  upsertDepartmentIntegrationSettings: (...a: unknown[]) => upsertDepartmentIntegrationSettingsMock(...a),
}));

const createCredentialMock = vi.fn();
const updateCredentialMock = vi.fn();
const getCredentialMock = vi.fn();
vi.mock("@/api/secret/credentials", () => ({
  createCredential: (...a: unknown[]) => createCredentialMock(...a),
  updateCredential: (...a: unknown[]) => updateCredentialMock(...a),
  getCredential: (...a: unknown[]) => getCredentialMock(...a),
}));

import { IntegrationOnboarding } from "@/pages/home/IntegrationOnboarding";

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <IntegrationOnboarding />
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("IntegrationOnboarding — ввод реальных Jira/Git/Confluence токенов", () => {
  beforeEach(() => {
    getDepartmentIntegrationSettingsMock.mockReset().mockResolvedValue(emptySettings());
    upsertDepartmentIntegrationSettingsMock.mockReset();
    createCredentialMock.mockReset();
    updateCredentialMock.mockReset();
    getCredentialMock.mockReset();
  });

  it("показывает все три слота как не настроенные на свежем dev-отделе", async () => {
    renderPage();
    expect(await screen.findByText("Jira / Zephyr / Tempo")).toBeInTheDocument();
    expect(screen.getAllByText("не настроено")).toHaveLength(3);
  });

  it("первый ввод создаёт scope=service credential и привязывает его ссылкой", async () => {
    createCredentialMock.mockResolvedValue({ id: "cred_new_jira", name: "jira-integration-token" });
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderPage();
    await screen.findByText("Jira / Zephyr / Tempo");

    const [jiraInput] = screen.getAllByPlaceholderText("реальный токен / пароль");
    fireEvent.change(jiraInput, { target: { value: "real-jira-token" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);

    await waitFor(() =>
      expect(createCredentialMock).toHaveBeenCalledWith(
        expect.objectContaining({
          service: "jira",
          scope: "service",
          secret: "real-jira-token",
          owner_dept_id: "dep_1",
        }),
      ),
    );
    await waitFor(() =>
      expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith("dep_1", { credential_id: "cred_new_jira" }),
    );
  });

  it("уже настроенный слот предлагает ротацию, а не повторное создание", async () => {
    getDepartmentIntegrationSettingsMock.mockResolvedValue({
      ...emptySettings(),
      credential_id: "cred_existing_jira",
    });
    getCredentialMock.mockResolvedValue({
      id: "cred_existing_jira",
      name: "jira-integration-token",
      service: "jira",
      updated_at: "2026-09-01T00:00:00Z",
    });
    updateCredentialMock.mockResolvedValue({ id: "cred_existing_jira" });
    renderPage();

    await screen.findByText("настроено");
    fireEvent.click(screen.getByRole("button", { name: "Заменить значение (ротация)" }));
    const input = screen.getByPlaceholderText("новое значение токена");
    fireEvent.change(input, { target: { value: "rotated-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    await waitFor(() =>
      expect(updateCredentialMock).toHaveBeenCalledWith("cred_existing_jira", { secret: "rotated-token" }),
    );
    expect(createCredentialMock).not.toHaveBeenCalled();
  });
});
