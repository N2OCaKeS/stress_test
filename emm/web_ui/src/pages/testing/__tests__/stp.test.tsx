import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const listOsVersionsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...a: unknown[]) => listOsVersionsMock(...a),
}));

const listDepartmentsMock = vi.fn();
vi.mock("@/api/auth/departments", () => ({
  listDepartments: (...a: unknown[]) => listDepartmentsMock(...a),
}));

const listTestStandsMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...a: unknown[]) => listTestStandsMock(...a),
}));

const listStpTestCasesMock = vi.fn();
const createStpTestCaseMock = vi.fn();
const getStpTestCaseMock = vi.fn();
const updateStpTestCaseMock = vi.fn();
const deleteStpTestCaseMock = vi.fn();
const generateStpMock = vi.fn();
const getStpCompositionMock = vi.fn();
const listStpTestRunsMock = vi.fn();
const getStpTestRunMock = vi.fn();
const listStpTestRunCellsMock = vi.fn();
const overrideStpCellMock = vi.fn();
const publishStpMatrixMock = vi.fn();
vi.mock("@/api/testing/stp", () => ({
  listStpTestCases: (...a: unknown[]) => listStpTestCasesMock(...a),
  createStpTestCase: (...a: unknown[]) => createStpTestCaseMock(...a),
  getStpTestCase: (...a: unknown[]) => getStpTestCaseMock(...a),
  updateStpTestCase: (...a: unknown[]) => updateStpTestCaseMock(...a),
  deleteStpTestCase: (...a: unknown[]) => deleteStpTestCaseMock(...a),
  generateStp: (...a: unknown[]) => generateStpMock(...a),
  getStpComposition: (...a: unknown[]) => getStpCompositionMock(...a),
  listStpTestRuns: (...a: unknown[]) => listStpTestRunsMock(...a),
  getStpTestRun: (...a: unknown[]) => getStpTestRunMock(...a),
  listStpTestRunCells: (...a: unknown[]) => listStpTestRunCellsMock(...a),
  overrideStpCell: (...a: unknown[]) => overrideStpCellMock(...a),
  publishStpMatrix: (...a: unknown[]) => publishStpMatrixMock(...a),
}));

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: {
      id: "u1",
      dept_id: "dep_1",
      platform_role: "dep_admin",
      service_roles: {},
    },
  }),
}));

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: async () => true }),
}));

import { StpMiddlePanel, StpWorkzone, useStpVersionState } from "@/pages/testing/stp";

const ISO = "2026-09-01T00:00:00Z";

function osVersion(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "osv_1",
    name: "1.8.7.46",
    description: "Astra Linux SE 1.8.7 rc46",
    repositories: [],
    kernels: ["6.12.24-1.el11"],
    is_urgent_update: false,
    discovered_at: ISO,
    updated_at: ISO,
    ...overrides,
  };
}

function department(overrides: Partial<Record<string, unknown>> = {}) {
  return { id: "dep_1", name: "Dept One", user_count: 3, created_at: ISO, ...overrides };
}

function testStand(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "stand_1",
    server_id: "srv_1",
    department_id: "dep_1",
    queue_enabled: true,
    is_active: true,
    created_at: ISO,
    updated_at: ISO,
    created_by: null,
    server: null,
    server_unavailable: false,
    ...overrides,
  };
}

function testCase(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "case_1",
    code: "ASTRA-T101",
    title: "Установка с загрузочного носителя",
    zephyr_id: "BT-T101",
    department_id: "dep_1",
    created_at: ISO,
    updated_at: ISO,
    created_by: "usr_admin",
    ...overrides,
  };
}

function testRun(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "run_1",
    os_version_id: "osv_1",
    mode: "orel",
    kernel: "6.12.24-1.el11",
    stand_id: "stand_1",
    zephyr_test_run_key: "BT-R1",
    zephyr_folder_path: "/1.8.7.46/orel",
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  };
}

function cell(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "cell_1",
    stp_test_case_id: "case_1",
    stp_test_run_id: "run_1",
    status: "pass",
    is_active: true,
    queue_item_id: "qi_1",
    updated_by: null,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  };
}

function composition(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "stpcomp_1",
    department_id: "dep_1",
    os_version_id: "osv_1",
    scope: "changelog",
    revision: 1,
    updated_at: ISO,
    updated_by: "usr_admin",
    ...overrides,
  };
}

function Harness() {
  const state = useStpVersionState();
  return (
    <div style={{ display: "flex" }}>
      <StpMiddlePanel state={state} />
      <StpWorkzone state={state} />
    </div>
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <Harness />
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** Кликает по триггеру `Dropdown`, находя его как ближайшую кнопку рядом с подписью. */
function clickDropdownNear(labelText: string) {
  const label = screen.getByText(labelText);
  const button = label.parentElement!.querySelector("button");
  if (!button) throw new Error(`dropdown button not found near "${labelText}"`);
  fireEvent.click(button);
}

function pickOption(optionText: string | RegExp) {
  const popup = document.querySelector('[data-app-portal]') as HTMLElement;
  const option = within(popup).getByText(optionText);
  fireEvent.click(option);
}

describe("StpMiddlePanel + StpWorkzone", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listOsVersionsMock.mockReset();
    listDepartmentsMock.mockReset();
    listTestStandsMock.mockReset();
    listStpTestCasesMock.mockReset();
    createStpTestCaseMock.mockReset();
    getStpTestCaseMock.mockReset();
    updateStpTestCaseMock.mockReset();
    deleteStpTestCaseMock.mockReset();
    generateStpMock.mockReset();
    getStpCompositionMock.mockReset();
    listStpTestRunsMock.mockReset();
    getStpTestRunMock.mockReset();
    listStpTestRunCellsMock.mockReset();
    overrideStpCellMock.mockReset();
    publishStpMatrixMock.mockReset();

    listOsVersionsMock.mockResolvedValue({ items: [osVersion()], total: 1, limit: 500, offset: 0 });
    listDepartmentsMock.mockResolvedValue([department()]);
    listTestStandsMock.mockResolvedValue({ items: [testStand()], total: 1, limit: 500, offset: 0 });
    listStpTestCasesMock.mockResolvedValue({ items: [testCase()], total: 1, limit: 500, offset: 0 });
    listStpTestRunsMock.mockResolvedValue({ items: [testRun()], total: 1, limit: 500, offset: 0 });
    listStpTestRunCellsMock.mockResolvedValue([cell()]);
    getStpCompositionMock.mockResolvedValue(composition({ scope: null, revision: 0, id: null, updated_at: null, updated_by: null }));
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("грузит версию, каталог и матрицу из API", async () => {
    renderPage();

    expect(await screen.findAllByText("1.8.7.46")).not.toHaveLength(0);
    expect(await screen.findAllByText("ASTRA-T101")).not.toHaveLength(0);

    await waitFor(() => expect(listStpTestRunCellsMock).toHaveBeenCalledWith("run_1"));
    expect(await screen.findByText("Пройден")).toBeInTheDocument();
  });

  it("состав СТП — «По changelog» зовёт generateStp с явным scope и рефетчит список/состав", async () => {
    generateStpMock.mockResolvedValue({ test_runs: [testRun()], errors: [] });
    renderPage();

    await screen.findAllByText("1.8.7.46");
    const generateButton = await screen.findByRole("button", { name: /Состав СТП/ });
    await waitFor(() => expect(generateButton).toBeEnabled());
    fireEvent.click(generateButton);

    fireEvent.click(await screen.findByRole("button", { name: "По changelog" }));

    await waitFor(() => expect(generateStpMock).toHaveBeenCalledTimes(1));
    expect(generateStpMock).toHaveBeenCalledWith({
      os_version_id: "osv_1",
      scope: "changelog",
      department_id: "dep_1",
    });
    expect(await screen.findByText(/прогонов в составе:/)).toBeInTheDocument();
    await waitFor(() => expect(listStpTestRunsMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getStpCompositionMock).toHaveBeenCalledTimes(2));
  });

  it("состав СТП — «Полный набор» зовёт generateStp со scope=full", async () => {
    generateStpMock.mockResolvedValue({ test_runs: [testRun()], errors: [] });
    renderPage();

    await screen.findAllByText("1.8.7.46");
    fireEvent.click(await screen.findByRole("button", { name: /Состав СТП/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Полный набор" }));

    await waitFor(() => expect(generateStpMock).toHaveBeenCalledTimes(1));
    expect(generateStpMock).toHaveBeenCalledWith({
      os_version_id: "osv_1",
      scope: "full",
      department_id: "dep_1",
    });
  });

  it("состав СТП — показывает текущий scope/revision из GET /stp/composition", async () => {
    getStpCompositionMock.mockResolvedValue(composition({ scope: "full", revision: 3 }));
    renderPage();

    await screen.findAllByText("1.8.7.46");
    expect(await screen.findByText("Полный набор")).toBeInTheDocument();
    expect(await screen.findByText(/ревизия 3/)).toBeInTheDocument();
  });

  it("состав СТП — частичные ошибки отображаются как предупреждение", async () => {
    generateStpMock.mockResolvedValue({
      test_runs: [testRun()],
      errors: [{ stand_id: "stand_9", error_code: "SSH_TIMEOUT", message: "не удалось подключиться" }],
    });
    renderPage();

    await screen.findAllByText("1.8.7.46");
    fireEvent.click(await screen.findByRole("button", { name: /Состав СТП/ }));
    fireEvent.click(await screen.findByRole("button", { name: "По changelog" }));

    await waitFor(() => expect(generateStpMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Часть стендов провалилась/)).toBeInTheDocument();
    expect(screen.getByText(/SSH_TIMEOUT: не удалось подключиться/)).toBeInTheDocument();
  });

  it("публикация СТП-матрицы — успех зовёт publishStpMatrix с os_version_id", async () => {
    publishStpMatrixMock.mockResolvedValue({
      id: "stpmx_1", department_id: "dep_1", os_version_id: "osv_1", status: "posted",
      confluence_page_id: "pg_1", confluence_parent_page_id: "pg_0", error: null,
      published_at: ISO, updated_at: ISO,
    });
    renderPage();

    await screen.findAllByText("1.8.7.46");
    const publishButton = await screen.findByRole("button", { name: /Опубликовать в Confluence/ });
    await waitFor(() => expect(publishButton).toBeEnabled());
    fireEvent.click(publishButton);

    fireEvent.click(await screen.findByRole("button", { name: "Опубликовать" }));

    await waitFor(() => expect(publishStpMatrixMock).toHaveBeenCalledTimes(1));
    expect(publishStpMatrixMock).toHaveBeenCalledWith({
      os_version_id: "osv_1",
      department_id: "dep_1",
    });
    expect(await screen.findByText("Опубликовано")).toBeInTheDocument();
  });

  it("публикация СТП-матрицы — не настроено отображается как предупреждение, не ошибка", async () => {
    publishStpMatrixMock.mockResolvedValue({
      id: "stpmx_1", department_id: "dep_1", os_version_id: "osv_1", status: "skipped_not_configured",
      confluence_page_id: null, confluence_parent_page_id: null, error: null,
      published_at: null, updated_at: ISO,
    });
    renderPage();

    await screen.findAllByText("1.8.7.46");
    fireEvent.click(await screen.findByRole("button", { name: /Опубликовать в Confluence/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Опубликовать" }));

    await waitFor(() => expect(publishStpMatrixMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getAllByText(/Не настроено/).length).toBeGreaterThan(0));
  });

  it("ручной override ячейки зовёт overrideStpCell и обновляет ячейку", async () => {
    overrideStpCellMock.mockResolvedValue(cell({ status: "fail", updated_by: "usr_qa" }));
    renderPage();

    fireEvent.click(await screen.findByText("Пройден"));
    clickDropdownNear("Ручной override статуса");
    pickOption("Провален");

    fireEvent.click(await screen.findByRole("button", { name: /Сохранить override/ }));

    await waitFor(() =>
      expect(overrideStpCellMock).toHaveBeenCalledWith("cell_1", { status: "fail" }),
    );
    await waitFor(() => expect(listStpTestRunCellsMock).toHaveBeenCalledTimes(2));
  });

  it("создание тест-кейса зовёт createStpTestCase", async () => {
    createStpTestCaseMock.mockResolvedValue(testCase({ id: "case_2", code: "ASTRA-T102" }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Добавить тест-кейс/ }));

    const codeInput = await screen.findByPlaceholderText("ASTRA-T101");
    fireEvent.change(codeInput, { target: { value: "ASTRA-T102" } });
    const titleInput = screen.getByPlaceholderText("Установка с загрузочного носителя");
    fireEvent.change(titleInput, { target: { value: "Новый тест-кейс" } });

    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createStpTestCaseMock).toHaveBeenCalledWith({
        code: "ASTRA-T102",
        title: "Новый тест-кейс",
        zephyr_id: undefined,
        department_id: "dep_1",
      }),
    );
  });

  it("редактирование тест-кейса подтягивает свежие данные и зовёт updateStpTestCase", async () => {
    getStpTestCaseMock.mockResolvedValue(testCase({ title: "Свежий заголовок" }));
    updateStpTestCaseMock.mockResolvedValue(testCase({ title: "Свежий заголовок 2" }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Изменить" }));

    await waitFor(() => expect(getStpTestCaseMock).toHaveBeenCalledWith("case_1"));
    const titleInput = await screen.findByDisplayValue("Свежий заголовок");
    fireEvent.change(titleInput, { target: { value: "Свежий заголовок 2" } });

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateStpTestCaseMock).toHaveBeenCalledWith("case_1", {
        title: "Свежий заголовок 2",
        zephyr_id: "BT-T101",
        department_id: "dep_1",
      }),
    );
  });

  it("удаление тест-кейса с подтверждением зовёт deleteStpTestCase", async () => {
    deleteStpTestCaseMock.mockResolvedValue({ ok: true });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(deleteStpTestCaseMock).toHaveBeenCalledWith("case_1"));
  });
});
