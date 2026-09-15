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

const getDepartmentIntegrationSettingsMock = vi.fn();
const listCredentialsMock = vi.fn();
vi.mock("@/api/secret/credentials", () => ({ listCredentials: (...a: unknown[]) => listCredentialsMock(...a) }));
const upsertDepartmentIntegrationSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentIntegrationSettings", () => ({
  getDepartmentIntegrationSettings: (...a: unknown[]) => getDepartmentIntegrationSettingsMock(...a),
  upsertDepartmentIntegrationSettings: (...a: unknown[]) => upsertDepartmentIntegrationSettingsMock(...a),
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

vi.mock("@/api/testing/departmentReportMembers", () => ({
  listDepartmentReportMembers: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 }),
  createDepartmentReportMember: vi.fn(),
  updateDepartmentReportMember: vi.fn(),
  deleteDepartmentReportMember: vi.fn(),
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

function emptySettings() {
  return {
    id: null,
    department_id: "dep_1",
    credential_id: null,
    jira_base_url: null,
    confluence_base_url: null,
    confluence_credential_id: null,
    bitbucket_base_url: null,
    bitbucket_project_key: null,
    bitbucket_repo_slug: null,
    bitbucket_credential_id: null,
    jira_board_id: null,
    tempo_team_id: null,
    confluence_report_page_space: null,
    confluence_report_parent_page_title: null,
    stp_matrix_confluence_space: null,
    stp_matrix_confluence_root_page_title: null,
    created_at: null,
    updated_at: null,
  };
}

describe("HomeDepAdmin — настройки интеграции отдела (department_integration_settings)", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    getDepartmentIntegrationSettingsMock.mockReset().mockResolvedValue(emptySettings());
    upsertDepartmentIntegrationSettingsMock.mockReset();
    listCredentialsMock.mockReset().mockResolvedValue({ items: [], next_cursor: null });
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("показывает блок и все поля пустыми, если строка ещё не создана", async () => {
    renderHome();
    expect(await screen.findByText("Интеграции отдела (Jira / Confluence / Bitbucket)")).toBeInTheDocument();
    // GET резолвится асинхронно — ждём коммита sync-эффекта, иначе поля
    // ещё держат исходное состояние формы, а не то, что реально пришло.
    await waitFor(() => expect(getDepartmentIntegrationSettingsMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByPlaceholderText("https://jira.astralinux.ru")).toHaveValue(""));
    expect(screen.getByPlaceholderText("PROJ")).toHaveValue("");
  });

  it("сохранение зовёт upsertDepartmentIntegrationSettings с department_id и введёнными полями", async () => {
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderHome();
    await screen.findByText("Интеграции отдела (Jira / Confluence / Bitbucket)");
    // Дожидаемся, пока GET осядет в форме, прежде чем печатать — иначе
    // sync-эффект (`useEffect(..., [loaded])`) может отработать ПОСЛЕ ввода
    // и затереть уже напечатанное значение (гонка, как в ServicesAcsSettings).
    await waitFor(() => expect(screen.getByPlaceholderText("PROJ")).toHaveValue(""));

    const jiraInput = screen.getByPlaceholderText("https://jira.astralinux.ru") as HTMLInputElement;
    fireEvent.change(jiraInput, { target: { value: "https://jira.astralinux.ru" } });
    await waitFor(() => expect(jiraInput.value).toBe("https://jira.astralinux.ru"));

    const projectKeyInput = screen.getByPlaceholderText("PROJ") as HTMLInputElement;
    fireEvent.change(projectKeyInput, { target: { value: "TST" } });
    await waitFor(() => expect(projectKeyInput.value).toBe("TST"));

    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith(
      "dep_1",
      expect.objectContaining({
        jira_base_url: "https://jira.astralinux.ru",
        bitbucket_project_key: "TST",
        confluence_base_url: null,
      }),
    );
  });

  it("сохраняет настройки СТП-матрицы (space + корневая страница)", async () => {
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderHome();
    await screen.findByText("Интеграции отдела (Jira / Confluence / Bitbucket)");
    await waitFor(() => expect(screen.getByPlaceholderText("DEPTQA")).toHaveValue(""));

    const spaceInput = screen.getByPlaceholderText("DEPTQA") as HTMLInputElement;
    fireEvent.change(spaceInput, { target: { value: "DEPTQA" } });
    await waitFor(() => expect(spaceInput.value).toBe("DEPTQA"));

    const rootTitleInput = screen.getByPlaceholderText("Состав тестового прогона") as HTMLInputElement;
    fireEvent.change(rootTitleInput, { target: { value: "Состав тестового прогона" } });
    await waitFor(() => expect(rootTitleInput.value).toBe("Состав тестового прогона"));

    fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);

    await waitFor(() => expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith(
      "dep_1",
      expect.objectContaining({
        stp_matrix_confluence_space: "DEPTQA",
        stp_matrix_confluence_root_page_title: "Состав тестового прогона",
      }),
    );
  });

  it("кнопка «Сохранить» недоступна, пока форма не изменена", async () => {
    renderHome();
    await screen.findByText("Интеграции отдела (Jira / Confluence / Bitbucket)");
    const saveButtons = screen.getAllByRole("button", { name: "Сохранить" });
    expect(saveButtons[0]).toBeDisabled();
  });

  it("выбирает сервисные записи по именам, учитывает пагинацию и срок действия", async () => {
    const cred = { id: "cred_jira", name: "Jira испытаний", service: "jira", scope: "service", owner_dept_id: "dep_1", status: "active", valid_from: null, valid_to: null };
    listCredentialsMock.mockResolvedValueOnce({ items: [cred, { ...cred, id: "old", name: "Истёкший", valid_to: "2000-01-01T00:00:00Z" }], next_cursor: "page2" })
      .mockResolvedValueOnce({ items: [{ ...cred, id: "cred_git", name: "Git испытаний" }, { ...cred, id: "foreign", name: "Другой отдел", owner_dept_id: "dep_other" }], next_cursor: null });
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderHome();
    await waitFor(() => expect(screen.getByRole("button", { name: "Учётные данные Jira / Zephyr / Tempo: Не выбраны" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Учётные данные Jira / Zephyr / Tempo: Не выбраны" }));
    expect(await screen.findByRole("option", { name: "Git испытаний · jira" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Другой отдел/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Истёкший/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("option", { name: "Jira испытаний · jira" }));
    fireEvent.click(screen.getByRole("button", { name: "Учётные данные Git / Bitbucket: Не выбраны" }));
    fireEvent.click(screen.getByRole("option", { name: "Git испытаний · jira" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);
    await waitFor(() => expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith("dep_1", expect.objectContaining({ credential_id: "cred_jira", bitbucket_credential_id: "cred_git" })));
  });

  it("выбирает отдельную сервисную запись для Confluence (C4)", async () => {
    const cred = { id: "cred_jira", name: "Jira испытаний", service: "jira", scope: "service", owner_dept_id: "dep_1", status: "active", valid_from: null, valid_to: null };
    const confluenceCred = { ...cred, id: "cred_confluence", name: "Confluence испытаний", service: "confluence" };
    listCredentialsMock.mockResolvedValue({ items: [cred, confluenceCred], next_cursor: null });
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderHome();
    await waitFor(() => expect(screen.getByRole("button", { name: "Учётные данные Confluence / life: Не выбраны" })).toBeEnabled());
    expect(screen.getByText("Не выбрано — публикация в Confluence использует запись Jira / Zephyr / Tempo.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Учётные данные Confluence / life: Не выбраны" }));
    fireEvent.click(await screen.findByRole("option", { name: "Confluence испытаний · confluence" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);
    await waitFor(() => expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith("dep_1", expect.objectContaining({ confluence_credential_id: "cred_confluence" })));
  });

  it("ошибка списка секретов не стирает сохранённую привязку", async () => {
    getDepartmentIntegrationSettingsMock.mockResolvedValue({ ...emptySettings(), credential_id: "cred_existing" });
    listCredentialsMock.mockRejectedValue(new Error("Сервис секретов недоступен"));
    upsertDepartmentIntegrationSettingsMock.mockResolvedValue(emptySettings());
    renderHome();
    await screen.findByText("Сервис секретов недоступен");
    await waitFor(() => expect(screen.getByRole("button", { name: "Учётные данные Jira / Zephyr / Tempo: Текущая запись недоступна в списке" })).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText("PROJ"), { target: { value: "TST" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);
    await waitFor(() => expect(upsertDepartmentIntegrationSettingsMock).toHaveBeenCalledWith("dep_1", expect.objectContaining({ credential_id: "cred_existing" })));
  });
});
