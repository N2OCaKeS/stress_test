import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_1", platform_role: "dep_admin", service_roles: {} },
  }),
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

import { ServicesTestingReportMembers } from "@/pages/admin/services/ServicesTestingReportMembers";

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesTestingReportMembers />
      </ToastProvider>
    </ThemeProvider>,
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

describe("ServicesTestingReportMembers — сотрудники отдела для отчёта по активностям (department_report_members)", () => {
  beforeEach(() => {
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

  it("грузит и показывает список сотрудников", async () => {
    renderPage();
    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.getByText(/ivanov/)).toBeInTheDocument();
  });

  it("список сотрудников зовётся с department_id из persona", async () => {
    renderPage();
    await screen.findByText("Иванов Иван");
    expect(listDepartmentReportMembersMock).toHaveBeenCalledWith("dep_1", { limit: 200 });
  });

  it("добавление сотрудника зовёт createDepartmentReportMember с введёнными полями", async () => {
    createDepartmentReportMemberMock.mockResolvedValue(member({ id: "drm_2", display_name: "Петров Пётр" }));
    renderPage();
    await screen.findByText("Иванов Иван");

    fireEvent.click(screen.getByRole("button", { name: /Добавить/ }));

    const nameInput = await screen.findByPlaceholderText("Иванов Иван");
    fireEvent.change(nameInput, { target: { value: "Петров Пётр" } });
    fireEvent.change(screen.getByPlaceholderText("ivanov"), { target: { value: "petrov" } });

    // Модалка рендерится порталом последней в document.body — «Сохранить»
    // модалки идёт после «Сохранить» списка (которого тут нет), берём последнюю.
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
    renderPage();
    await screen.findByText("Иванов Иван");

    fireEvent.click(screen.getByRole("button", { name: /Удалить/ }));

    await waitFor(() =>
      expect(deleteDepartmentReportMemberMock).toHaveBeenCalledWith("dep_1", "drm_1"),
    );
  });

  it("редактирование сотрудника зовёт updateDepartmentReportMember", async () => {
    updateDepartmentReportMemberMock.mockResolvedValue(member({ display_name: "Иванов И.И." }));
    renderPage();
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
