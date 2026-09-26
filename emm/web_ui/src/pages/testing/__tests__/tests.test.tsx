import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import { ApiError } from "@/api/client";
import type {
  GlobalVariable,
  GlobalVariableSourceOptions,
  LaunchPreview,
  TestCommandArg,
  TestDefinition,
} from "@/api/testing/types";

const confirmMock = vi.fn(async () => true);
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

const listTestDefinitionsMock = vi.fn();
const createTestDefinitionMock = vi.fn();
const updateTestDefinitionMock = vi.fn();
const deleteTestDefinitionMock = vi.fn();
const previewTestLaunchMock = vi.fn();
vi.mock("@/api/testing/testDefinitions", () => ({
  previewTestLaunch: (...args: unknown[]) => previewTestLaunchMock(...args),
  listTestDefinitions: (...args: unknown[]) => listTestDefinitionsMock(...args),
  createTestDefinition: (...args: unknown[]) => createTestDefinitionMock(...args),
  updateTestDefinition: (...args: unknown[]) => updateTestDefinitionMock(...args),
  deleteTestDefinition: (...args: unknown[]) => deleteTestDefinitionMock(...args),
}));

const listTestCommandArgsMock = vi.fn();
const copyTestCommandArgsMock = vi.fn();
const createTestCommandArgMock = vi.fn();
const updateTestCommandArgMock = vi.fn();
const deleteTestCommandArgMock = vi.fn();
vi.mock("@/api/testing/testCommandArgs", () => ({
  copyTestCommandArgs: (...args: unknown[]) => copyTestCommandArgsMock(...args),
  listTestCommandArgs: (...args: unknown[]) => listTestCommandArgsMock(...args),
  createTestCommandArg: (...args: unknown[]) => createTestCommandArgMock(...args),
  updateTestCommandArg: (...args: unknown[]) => updateTestCommandArgMock(...args),
  deleteTestCommandArg: (...args: unknown[]) => deleteTestCommandArgMock(...args),
}));

// Шаги теста: у каталожных тестов здесь один шаг — конструктор
// работает как с одношаговым тестом. Сами шаги — `TestStepsPanel.test.tsx`.
vi.mock("@/api/testing/testSteps", () => ({
  listTestSteps: vi.fn(async (testId: string) => [{
    id: `step_${testId}`, test_id: testId, position: 0, name: "", starter_suffix: null,
    run_mode: "full", stand_setup: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  }]),
  createTestStep: vi.fn(),
  updateTestStep: vi.fn(),
  deleteTestStep: vi.fn(),
  reorderTestSteps: vi.fn(),
}));

const listGlobalVariablesMock = vi.fn();
const getGlobalVariableChoicesMock = vi.fn();
const createGlobalVariableMock = vi.fn();
const updateGlobalVariableMock = vi.fn();
const deleteGlobalVariableMock = vi.fn();
const getGlobalVariableSourceOptionsMock = vi.fn();
vi.mock("@/api/testing/global_variables", () => ({
  getGlobalVariableSourceOptions: (...args: unknown[]) => getGlobalVariableSourceOptionsMock(...args),
  listGlobalVariables: (...args: unknown[]) => listGlobalVariablesMock(...args),
  getGlobalVariableChoices: (...args: unknown[]) => getGlobalVariableChoicesMock(...args),
  createGlobalVariable: (...args: unknown[]) => createGlobalVariableMock(...args),
  updateGlobalVariable: (...args: unknown[]) => updateGlobalVariableMock(...args),
  deleteGlobalVariable: (...args: unknown[]) => deleteGlobalVariableMock(...args),
}));

const listTestStandsMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...args: unknown[]) => listTestStandsMock(...args),
}));

const listStpTestCasesMock = vi.fn();
const createStpTestCaseMock = vi.fn();
const updateStpTestCaseMock = vi.fn();
vi.mock("@/api/testing/stp", () => ({
  listStpTestCases: (...args: unknown[]) => listStpTestCasesMock(...args),
  createStpTestCase: (...args: unknown[]) => createStpTestCaseMock(...args),
  updateStpTestCase: (...args: unknown[]) => updateStpTestCaseMock(...args),
}));

const listOsVersionsMock = vi.fn();
const resolveOsKernelsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...args: unknown[]) => listOsVersionsMock(...args),
  resolveOsKernels: (...args: unknown[]) => resolveOsKernelsMock(...args),
}));

const listDepartmentsMock = vi.fn();
vi.mock("@/api/auth/departments", () => ({
  listDepartments: (...args: unknown[]) => listDepartmentsMock(...args),
}));

import { TestsWorkzone } from "@/pages/testing/tests";

const TESTS: TestDefinition[] = [
  {
    id: "td_1",
    code: "FS-EXT4-FILL",
    full_name: "filesystem / ext4 fill+remove cycle",
    category: "filesystem",
    owner: "QA Infra",
    readiness: "ready",
    mode: "orel",
    department_id: null,
    pinned_stand_id: null,
    changelog_component: null,
    timeout_seconds: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
  },
  {
    id: "td_2",
    code: "DB-PG-TPCC",
    full_name: "database / PostgreSQL TPC-C",
    category: "database",
    owner: "Backend QA",
    readiness: "development",
    mode: "orel",
    department_id: null,
    pinned_stand_id: null,
    changelog_component: null,
    timeout_seconds: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
  },
];

const VARIABLES: GlobalVariable[] = [
  {
    id: "gv_1",
    code: "RC",
    label: "Release candidate",
    source: "launch_context",
    value_type: "string",
    choices_source: null,
    is_sensitive: false,
    description: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
  },
  {
    id: "gv_2",
    code: "TEST_PASSWORD",
    label: "Test password",
    source: "launch_context",
    value_type: "string",
    choices_source: null,
    is_sensitive: true,
    description: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    created_by: null,
  },
];

const SLOTS: TestCommandArg[] = [
  {
    id: "arg_1",
    test_id: "td_1",
    position: 0,
    kind: "literal",
    literal_value: "backup_image.py",
    variable_id: null,
    override_value: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  },
  {
    id: "arg_2",
    test_id: "td_1",
    position: 1,
    kind: "variable",
    literal_value: null,
    variable_id: "gv_1",
    override_value: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  },
];

const SOURCE_OPTIONS: GlobalVariableSourceOptions = {
  sources: ["launch_context", "static", "per_test_override", "secret_service", "template", "test_field", "stand",
    "department_integration", "os_version", "test_account", "zephyr_folder", "stand_ref"],
  test_fields: ["category", "changelog_component", "code", "full_name", "short_name"],
  stand_fields: ["host", "id", "legacy_token", "number"],
  stand_ref_fields: ["host", "legacy_token", "number"],
  department_integration_fields: [
    { field: "confluence_credential_id", is_credential: true },
    { field: "credential_id", is_credential: true },
    { field: "stp_matrix_confluence_space", is_credential: false },
  ],
  credential_parts: ["login", "secret"],
  os_version_fields: ["is_urgent_update", "name", "rc_number"],
  test_account_fields: ["home", "login", "password"],
  zephyr_folder_fields: ["folder_path", "folder_tree_id"],
  template_conditions: ["debug", "not_debug"],
};

const PREVIEW: LaunchPreview = {
  test_id: "td_1",
  stand_id: "st_1",
  launch_context: { RC: "osv_1", KERNEL: "6.1.90-1-generic", MODE: "orel" },
  debug: false,
  testenv: false,
  launch_profile: { profile_id: "lp_default", name: "Легаси starter.sh", version_id: "lpv_1", version: 1 },
  variables: [
    { code: "CONFLUENCE_NEW_PAGE", label: "Страница", source: "template", value: "XFS_1.8.1.6_orel_6.1.90-1-generic_stand3", sensitive: false, slot_position: null },
    { code: "CONFLUENCE_TOKEN", label: "Токен", source: "department_integration", value: "***", sensitive: true, slot_position: null },
    { code: "QUEUE_ITEM_ID", label: null, source: "claim", value: "qi_preview", sensitive: false, slot_position: null },
  ],
  dates_content_masked: "--token '***' --confluence-new-page XFS_1.8.1.6_orel_6.1.90-1-generic_stand3",
  files: [
    { role: "script", path: "/home/u/starter.sh", mode: "0755", sensitive: false, content: "#!/bin/bash\necho start" },
    { role: "token", path: "/home/u/git_token_qi_preview.conf", mode: "0600", sensitive: true, content: "***" },
    { role: "dates", path: "/home/u/dates_qi_preview.conf", mode: "0644", sensitive: true, content: "--token '***'" },
    { role: "testenv_marker", path: "/home/u/testenv_marker.conf", mode: "0644", sensitive: false, content: "off" },
  ],
  launch_command_masked: "sudo bash /home/u/starter.sh file_systems git_token_qi_preview.conf dates_qi_preview.conf 1.8.1.6 ''",
  stop_command: "sudo pkill -f '[/]home/u/starter\\.sh'",
  use_pty: true,
  cleanup_globs: [],
  errors: [],
};

function renderWorkzone() {
  return render(
    <ToastProvider>
      <TestsWorkzone />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  confirmMock.mockResolvedValue(true);
  listTestDefinitionsMock.mockResolvedValue({ items: TESTS, total: TESTS.length, limit: 500, offset: 0 });
  listDepartmentsMock.mockResolvedValue([]);
  listTestStandsMock.mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
  listStpTestCasesMock.mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
  createStpTestCaseMock.mockResolvedValue({ id: "case_1", code: "FS-EXT4-FILL", title: "t", zephyr_id: "BT-T1", department_id: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", created_by: null });
  updateStpTestCaseMock.mockResolvedValue({ id: "case_1", code: "FS-EXT4-FILL", title: "t", zephyr_id: "BT-T2", department_id: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", created_by: null });
  listTestCommandArgsMock.mockResolvedValue([...SLOTS]);
  copyTestCommandArgsMock.mockReset();
  copyTestCommandArgsMock.mockResolvedValue([...SLOTS]);
  listGlobalVariablesMock.mockResolvedValue({ items: VARIABLES, total: VARIABLES.length, limit: 200, offset: 0 });
  createTestDefinitionMock.mockResolvedValue(TESTS[0]);
  updateTestDefinitionMock.mockResolvedValue(TESTS[0]);
  deleteTestDefinitionMock.mockResolvedValue({ ok: true });
  createTestCommandArgMock.mockResolvedValue(SLOTS[0]);
  updateTestCommandArgMock.mockResolvedValue(SLOTS[0]);
  deleteTestCommandArgMock.mockResolvedValue({ ok: true });
  createGlobalVariableMock.mockResolvedValue(VARIABLES[0]);
  updateGlobalVariableMock.mockResolvedValue(VARIABLES[0]);
  deleteGlobalVariableMock.mockResolvedValue({ ok: true });
  getGlobalVariableSourceOptionsMock.mockResolvedValue(SOURCE_OPTIONS);
  previewTestLaunchMock.mockResolvedValue(PREVIEW);
  listOsVersionsMock.mockResolvedValue({
    items: [{ id: "osv_1", name: "1.8.1.6", kernels: ["6.1.90-1-generic", "5.15.0-1"] }],
    total: 1, limit: 500, offset: 0,
  });
  resolveOsKernelsMock.mockResolvedValue({ kernels: [] });
});

describe("TestsWorkzone — каталог тестов из API", () => {
  it("рендерит список тестов, полученный из listTestDefinitions", async () => {
    renderWorkzone();
    expect(await screen.findByText("FS-EXT4-FILL")).toBeInTheDocument();
    expect(screen.getByText("DB-PG-TPCC")).toBeInTheDocument();
    expect(listTestDefinitionsMock).toHaveBeenCalledWith({ limit: 500 });
    // счётчик "Всего наборов" учитывает оба теста
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("каталог не загрузился — показывает ошибку и кнопку повтора", async () => {
    listTestDefinitionsMock.mockReset();
    listTestDefinitionsMock.mockRejectedValue(new Error("network down"));
    renderWorkzone();
    expect(await screen.findByText(/network down/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("создание теста вызывает createTestDefinition и обновляет список", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), {
      target: { value: "NET-IPERF3" },
    });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "network / iperf3 throughput" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createTestDefinitionMock).toHaveBeenCalledWith(
        expect.objectContaining({ code: "NET-IPERF3", full_name: "network / iperf3 throughput", readiness: "development" }),
      ),
    );
    // модалка закрылась
    expect(screen.queryByText("Новый тест каталога")).not.toBeInTheDocument();
  });

  it("номер BT при создании теста заводит stp_test_case через createStpTestCase", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), {
      target: { value: "NET-IPERF3" },
    });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "network / iperf3 throughput" },
    });
    fireEvent.change(screen.getByPlaceholderText("BT-T1234"), { target: { value: "BT-T999" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createStpTestCaseMock).toHaveBeenCalledWith({
        code: "NET-IPERF3", title: "network / iperf3 throughput", zephyr_id: "BT-T999",
      }),
    );
  });

  it("пустой номер BT при создании не заводит stp_test_case", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), {
      target: { value: "NET-IPERF3" },
    });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "network / iperf3 throughput" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(createTestDefinitionMock).toHaveBeenCalled());
    expect(createStpTestCaseMock).not.toHaveBeenCalled();
  });

  it("редактирование теста с уже заведённым номером BT предзаполняет поле и обновляет его через updateStpTestCase", async () => {
    listStpTestCasesMock.mockResolvedValue({
      items: [{ id: "case_1", code: "FS-EXT4-FILL", title: "t", zephyr_id: "BT-T1", department_id: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", created_by: null }],
      total: 1, limit: 500, offset: 0,
    });
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: "Изменить тест" }));
    const btInput = await screen.findByDisplayValue("BT-T1");
    fireEvent.change(btInput, { target: { value: "BT-T2" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(updateStpTestCaseMock).toHaveBeenCalledWith("case_1", { zephyr_id: "BT-T2" }));
  });

  it("свой таймаут теста уходит в createTestDefinition числом, пустое поле — null", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), {
      target: { value: "NET-IPERF3" },
    });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "network / iperf3 throughput" },
    });
    fireEvent.change(screen.getByPlaceholderText("по умолчанию (12 часов)"), {
      target: { value: "120" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createTestDefinitionMock).toHaveBeenCalledWith(
        expect.objectContaining({ timeout_seconds: 120 }),
      ),
    );
  });

  it("приоритет теста для порядка прогона РЦ уходит в createTestDefinition числом (по умолчанию 0)", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), {
      target: { value: "NET-IPERF3" },
    });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "network / iperf3 throughput" },
    });
    const priority = screen.getByLabelText(/Приоритет в прогоне РЦ/) as HTMLInputElement;
    expect(priority.value).toBe("0");
    fireEvent.change(priority, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createTestDefinitionMock).toHaveBeenCalledWith(expect.objectContaining({ priority: 7 })),
    );
  });

  it("редактирование теста вызывает updateTestDefinition с id теста", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: "Изменить тест" }));
    const fullNameInput = await screen.findByDisplayValue("filesystem / ext4 fill+remove cycle");
    fireEvent.change(fullNameInput, { target: { value: "filesystem / ext4 fill+remove cycle v2" } });
    fireEvent.click(screen.getByRole("button", { name: "Рабочий" }));
    fireEvent.click(screen.getByRole("option", { name: "На проверке" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateTestDefinitionMock).toHaveBeenCalledWith(
        "td_1",
        expect.objectContaining({ full_name: "filesystem / ext4 fill+remove cycle v2", readiness: "review" }),
      ),
    );
  });

  it("удаление теста запрашивает подтверждение и вызывает deleteTestDefinition", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: "Удалить тест" }));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(deleteTestDefinitionMock).toHaveBeenCalledWith("td_1"));
  });

  it("клонирование теста предзаполняет форму и копирует слоты команды исходного теста", async () => {
    createTestDefinitionMock.mockResolvedValue({ ...TESTS[0], id: "td_3", code: "FS-EXT4-FILL.copy" });
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: "Клонировать тест" }));

    // Копия рабочего теста требует собственной проверки.
    expect(screen.getByRole("button", { name: "В разработке" })).toBeInTheDocument();
    const codeInput = await screen.findByDisplayValue("FS-EXT4-FILL.copy");
    expect(screen.getByDisplayValue("filesystem / ext4 fill+remove cycle")).toBeInTheDocument();
    fireEvent.change(codeInput, { target: { value: "FS-EXT4-FILL-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createTestDefinitionMock).toHaveBeenCalledWith(
        expect.objectContaining({ code: "FS-EXT4-FILL-2", category: "filesystem" }),
      ),
    );
    // Слоты td_1 (SLOTS) перенесены на новый тест в том же порядке/составе.
    await waitFor(() => expect(listTestCommandArgsMock).toHaveBeenCalledWith("td_1"));
    await waitFor(() => expect(createTestCommandArgMock).toHaveBeenCalledTimes(2));
    expect(createTestCommandArgMock).toHaveBeenNthCalledWith(1, "td_3", {
      kind: "literal",
      literal_value: "backup_image.py",
      variable_id: null,
      override_value: null,
    });
    expect(createTestCommandArgMock).toHaveBeenNthCalledWith(2, "td_3", {
      kind: "variable",
      literal_value: null,
      variable_id: "gv_1",
      override_value: null,
    });
  });
});

describe("TestsWorkzone — конструктор команды", () => {
  async function openConstructor() {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: /Конструктор/ }));
    await screen.findByText(/Конструктор команды/);
  }

  it("показывает слоты по порядку position, литерал и переменную", async () => {
    await openConstructor();
    expect(await screen.findByText("backup_image.py")).toBeInTheDocument();
    expect(screen.getByText("RC")).toBeInTheDocument();
    expect(listTestCommandArgsMock).toHaveBeenCalledWith("td_1");
  });

  async function chooseCopySource() {
    await openConstructor();
    await screen.findByText("backup_image.py");
    fireEvent.click(screen.getByRole("button", { name: "Скопировать из" }));
    expect(screen.getByRole("button", { name: "ОК" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Выберите тест для копирования" }));
    expect(screen.queryByRole("option", { name: /FS-EXT4-FILL/ })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("option", { name: /DB-PG-TPCC/ }));
  }

  it("копирует параметры выбранного теста и позволяет редактировать копию", async () => {
    const copied = [{ ...SLOTS[0], id: "arg_copy", literal_value: "--copied-test" }];
    await chooseCopySource();
    expect(screen.getByText(/Параметры выбранного теста заменят текущие/)).toBeInTheDocument();
    copyTestCommandArgsMock.mockResolvedValue(copied);
    listTestCommandArgsMock.mockResolvedValue(copied);
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    await screen.findByText("--copied-test");
    expect(copyTestCommandArgsMock).toHaveBeenCalledWith("td_1", "td_2");
    expect(createTestDefinitionMock).not.toHaveBeenCalled();
    expect(screen.queryByText("backup_image.py")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Изменить слот" }));
    fireEvent.change(screen.getByDisplayValue("--copied-test"), { target: { value: "--my-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateTestCommandArgMock).toHaveBeenCalledWith(
      "td_1", "arg_copy", expect.objectContaining({ literal_value: "--my-test" }),
    ));
  });

  it("отмена копирования сохраняет параметры", async () => {
    await chooseCopySource();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(copyTestCommandArgsMock).not.toHaveBeenCalled();
    expect(screen.getByText("backup_image.py")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать из" })).toBeInTheDocument();
  });

  it("ошибка копирования сохраняет параметры и позволяет повторить запрос", async () => {
    await chooseCopySource();
    copyTestCommandArgsMock.mockRejectedValueOnce(new Error("network down"));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
    expect(screen.getByText("backup_image.py")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    await waitFor(() => expect(copyTestCommandArgsMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: "Скопировать из" })).toBeInTheDocument();
  });

  it("блокирует повторную отправку и редактирование во время копирования", async () => {
    await chooseCopySource();
    let resolveCopy!: (value: TestCommandArg[]) => void;
    copyTestCommandArgsMock.mockImplementation(() => new Promise<TestCommandArg[]>((resolve) => {
      resolveCopy = resolve;
    }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    expect(screen.getByRole("button", { name: "Копирование…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить слот" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "Изменить слот" })[0]).toBeDisabled();
    resolveCopy(SLOTS);
    await screen.findByRole("button", { name: "Скопировать из" });
    expect(copyTestCommandArgsMock).toHaveBeenCalledTimes(1);
  });

  it("добавление литерал-слота вызывает createTestCommandArg с kind=literal", async () => {
    await openConstructor();
    await screen.findByText("backup_image.py");

    fireEvent.click(screen.getByRole("button", { name: /Добавить слот/ }));
    fireEvent.change(screen.getByPlaceholderText("значение аргумента"), {
      target: { value: "--dry-run" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(createTestCommandArgMock).toHaveBeenCalledWith("td_1", {
        kind: "literal",
        literal_value: "--dry-run",
        variable_id: null,
        override_value: null,
      }),
    );
  });

  it("добавление слота-переменной выбирает переменную из Dropdown и вызывает createTestCommandArg с kind=variable", async () => {
    await openConstructor();
    await screen.findByText("backup_image.py");

    fireEvent.click(screen.getByRole("button", { name: /Добавить слот/ }));
    fireEvent.click(screen.getByRole("button", { name: "Переменная" }));
    fireEvent.click(screen.getByRole("button", { name: /выберите переменную/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Release candidate · RC" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(createTestCommandArgMock).toHaveBeenCalledWith("td_1", {
        kind: "variable",
        literal_value: null,
        variable_id: "gv_1",
        override_value: null,
      }),
    );
  });

  it("удаление слота запрашивает подтверждение и вызывает deleteTestCommandArg", async () => {
    await openConstructor();
    await screen.findByText("backup_image.py");

    fireEvent.click(screen.getAllByRole("button", { name: "Удалить слот" })[0]);

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(deleteTestCommandArgMock).toHaveBeenCalledWith("td_1", "arg_1"));
  });

  it("кнопка ↓ на первом слоте меняет местами position первого и второго слота", async () => {
    await openConstructor();
    await screen.findByText("backup_image.py");

    fireEvent.click(screen.getAllByRole("button", { name: "Переместить слот ниже" })[0]);

    await waitFor(() => {
      expect(updateTestCommandArgMock).toHaveBeenCalledWith("td_1", "arg_1", { position: 1 });
      expect(updateTestCommandArgMock).toHaveBeenCalledWith("td_1", "arg_2", { position: 0 });
    });
  });
});

describe("TestsWorkzone — глобальные переменные", () => {
  async function openVariables() {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(screen.getByRole("button", { name: /Переменные/ }));
    await screen.findByText("Глобальные переменные");
  }

  it("показывает каталог переменных с code/label/source/value_type/choices_source/is_sensitive", async () => {
    await openVariables();
    expect(screen.getByText("RC")).toBeInTheDocument();
    expect(screen.getByText("Release candidate")).toBeInTheDocument();
    expect(screen.getAllByText("launch_context").length).toBeGreaterThan(0);
    expect(screen.getAllByText("string").length).toBeGreaterThan(0);
    expect(screen.getByText("TEST_PASSWORD")).toBeInTheDocument();
    expect(screen.getByText("чувствительно")).toBeInTheDocument();
    expect(listGlobalVariablesMock).toHaveBeenCalledWith({ limit: 200 });
  });

  it("создание переменной вызывает createGlobalVariable и обновляет список", async () => {
    await openVariables();

    fireEvent.click(screen.getByRole("button", { name: /Добавить переменную/ }));
    fireEvent.change(screen.getByPlaceholderText("код, например RC"), {
      target: { value: "KERNEL_VERSION" },
    });
    fireEvent.change(screen.getByPlaceholderText("метка"), {
      target: { value: "Версия ядра" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(createGlobalVariableMock).toHaveBeenCalledWith(
        expect.objectContaining({
          code: "KERNEL_VERSION",
          label: "Версия ядра",
          source: "launch_context",
          value_type: "string",
        }),
      ),
    );
    await waitFor(() => expect(listGlobalVariablesMock).toHaveBeenCalledTimes(2));
  });

  it("редактирование переменной вызывает updateGlobalVariable с id переменной", async () => {
    await openVariables();

    fireEvent.click(screen.getAllByRole("button", { name: "Изменить переменную" })[0]);
    const labelInput = await screen.findByDisplayValue("Release candidate");
    fireEvent.change(labelInput, { target: { value: "Release candidate v2" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateGlobalVariableMock).toHaveBeenCalledWith(
        "gv_1",
        expect.objectContaining({ label: "Release candidate v2" }),
      ),
    );
  });

  it("удаление переменной запрашивает подтверждение и вызывает deleteGlobalVariable", async () => {
    await openVariables();

    fireEvent.click(screen.getAllByRole("button", { name: "Удалить переменную" })[0]);

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(deleteGlobalVariableMock).toHaveBeenCalledWith("gv_1"));
  });

  it("новая переменная доступна конструктору сразу после создания, без перезагрузки страницы", async () => {
    const newVariable: GlobalVariable = {
      id: "gv_3",
      code: "NEW_VAR",
      label: "Новая переменная",
      source: "static",
      value_type: "string",
      choices_source: null,
      is_sensitive: false,
      description: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
    };
    listGlobalVariablesMock.mockResolvedValueOnce({ items: VARIABLES, total: VARIABLES.length, limit: 200, offset: 0 });
    listGlobalVariablesMock.mockResolvedValueOnce({
      items: [...VARIABLES, newVariable],
      total: VARIABLES.length + 1,
      limit: 200,
      offset: 0,
    });
    createGlobalVariableMock.mockResolvedValue(newVariable);

    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    // создаём переменную через панель управления (без перехода на другую
    // страницу и без ре-рендера renderWorkzone — тот же смонтированный компонент)
    fireEvent.click(screen.getByRole("button", { name: /Переменные/ }));
    await screen.findByText("Глобальные переменные");
    fireEvent.click(screen.getByRole("button", { name: /Добавить переменную/ }));
    fireEvent.change(screen.getByPlaceholderText("код, например RC"), { target: { value: "NEW_VAR" } });
    fireEvent.change(screen.getByPlaceholderText("метка"), { target: { value: "Новая переменная" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(listGlobalVariablesMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("NEW_VAR")).toBeInTheDocument();

    // закрываем панель переменных и открываем конструктор команды — он
    // переиспользует тот же useQuery страницы, а не тянет свой собственный
    // список переменных, поэтому видит NEW_VAR немедленно
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: /Конструктор/ }));
    await screen.findByText(/Конструктор команды/);

    fireEvent.click(screen.getByRole("button", { name: /Добавить слот/ }));
    fireEvent.click(screen.getByRole("button", { name: "Переменная" }));
    fireEvent.click(screen.getByRole("button", { name: /выберите переменную/ }));
    expect(await screen.findByRole("option", { name: /NEW_VAR/ })).toBeInTheDocument();
  });
});

describe("TestsWorkzone — источники переменных", () => {
  async function openVariables() {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(screen.getByRole("button", { name: /Переменные/ }));
    await screen.findByText("Глобальные переменные");
  }

  async function newVariable(code: string, source: RegExp) {
    fireEvent.click(screen.getByRole("button", { name: /Добавить переменную/ }));
    fireEvent.change(screen.getByPlaceholderText("код, например RC"), { target: { value: code } });
    fireEvent.change(screen.getByPlaceholderText("метка"), { target: { value: code.toLowerCase() } });
    fireEvent.click(screen.getByRole("button", { name: /launch_context — из контекста запуска/ }));
    fireEvent.click(await screen.findByRole("option", { name: source }));
  }

  it("шаблон: автодополнение кода после «{» и source_ref.template в теле запроса", async () => {
    await openVariables();
    await newVariable("PAGE", /template — шаблон/);

    const input = screen.getByRole("textbox", { name: "Шаблон" }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "X_{R" } });
    input.setSelectionRange(4, 4);
    fireEvent.select(input);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "{RC}" }));
    expect(input.value).toBe("X_{RC}");
    // подсветка: известный код отдельно
    expect(screen.getByTestId("template-highlight")).toHaveTextContent("X_{RC}");

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(createGlobalVariableMock).toHaveBeenCalledWith(
        expect.objectContaining({ code: "PAGE", source: "template", source_ref: { template: "X_{RC}" } }),
      ),
    );
  });

  it("неизвестный код шаблона подсвечивается до сохранения", async () => {
    await openVariables();
    await newVariable("PAGE", /template — шаблон/);
    fireEvent.change(screen.getByRole("textbox", { name: "Шаблон" }), { target: { value: "{NOPE}_{RC}" } });
    expect(screen.getByText("{NOPE}")).toHaveAttribute("title", "Неизвестная переменная");
    expect(screen.getByText(/Нет в каталоге: NOPE/)).toBeInTheDocument();
  });

  it("цикл шаблонов с сервиса показывается у поля шаблона, без всплывающего сообщения", async () => {
    createGlobalVariableMock.mockRejectedValueOnce(new ApiError(422, {
      error: "validation_error", error_code: "VARIABLE_TEMPLATE_CYCLE", message: "Template cycle",
      details: { code: "PAGE", cycle: ["PAGE", "RC", "PAGE"] },
    }));
    await openVariables();
    await newVariable("PAGE", /template — шаблон/);
    fireEvent.change(screen.getByRole("textbox", { name: "Шаблон" }), { target: { value: "{RC}" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Цикл подстановок: PAGE → RC → PAGE")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Шаблон" })).toHaveAttribute("aria-invalid", "true");
    // форма осталась открытой
    expect(screen.getByPlaceholderText("код, например RC")).toHaveValue("PAGE");
  });

  it("неверная ссылка с сервиса показывается с подсказкой", async () => {
    createGlobalVariableMock.mockRejectedValueOnce(new ApiError(422, {
      error: "validation_error", error_code: "VARIABLE_SOURCE_REF_INVALID",
      message: "A variable revealing a credential secret must be is_sensitive=true",
      details: { hint: "включите is_sensitive" },
    }));
    await openVariables();
    await newVariable("CONF_TOKEN", /department_integration/);
    fireEvent.click(screen.getByRole("button", { name: "— поле —" }));
    fireEvent.click(await screen.findByRole("option", { name: "credential_id (учётные данные)" }));
    fireEvent.click(screen.getByRole("button", { name: "— login или secret —" }));
    fireEvent.click(await screen.findByRole("option", { name: /secret — секрет/ }));
    expect(screen.getByText(/только в чувствительной переменной/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(createGlobalVariableMock).toHaveBeenCalledWith(expect.objectContaining({
        source: "department_integration",
        source_ref: { field: "credential_id", credential_part: "secret" },
      })),
    );
    expect(await screen.findByText(/must be is_sensitive=true — включите is_sensitive/)).toBeInTheDocument();
  });

  it("поле теста с запасным полем уходит в source_ref", async () => {
    await openVariables();
    await newVariable("SHORT", /test_field — поле теста/);
    fireEvent.click(screen.getByRole("button", { name: "— поле —" }));
    fireEvent.click(await screen.findByRole("option", { name: "short_name" }));
    fireEvent.click(screen.getByRole("button", { name: "— без запасного поля —" }));
    fireEvent.click(await screen.findByRole("option", { name: "full_name" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(createGlobalVariableMock).toHaveBeenCalledWith(expect.objectContaining({
        source: "test_field", source_ref: { field: "short_name", fallback: "full_name" },
      })),
    );
  });

  it("строка каталога показывает ссылку источника, форма правки предзаполнена", async () => {
    listGlobalVariablesMock.mockResolvedValue({
      items: [{
        ...VARIABLES[0], id: "gv_9", code: "RC_RELEASE", label: "Релиз", source: "os_version",
        source_ref: { field: "name", segments: 3, uu_segments: 5 },
      }],
      total: 1, limit: 200, offset: 0,
    });
    await openVariables();
    expect(screen.getByText("name segments=3 uu_segments=5")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Изменить переменную" }));
    expect(await screen.findByDisplayValue("3")).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("5"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(updateGlobalVariableMock).toHaveBeenCalledWith("gv_9", expect.objectContaining({
        source: "os_version", source_ref: { field: "name", segments: 3, uu_segments: null },
      })),
    );
  });
});

describe("TestsWorkzone — поля теста и превью запуска", () => {
  const STANDS = {
    items: [{ id: "st_1", server_id: "srv_1", department_id: "dep_a", server: { display_name: "stand3" } }],
    total: 1, limit: 500, offset: 0,
  };

  async function openPreview() {
    listTestStandsMock.mockResolvedValue(STANDS);
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: "Превью запуска" }));
    await screen.findByText(/Превью запуска · FS-EXT4-FILL/);
    const show = screen.getByRole("button", { name: /Показать/ });
    expect(show).toBeDisabled();
    fireEvent.click(await screen.findByRole("button", { name: "Выберите стенд" }));
    fireEvent.click(await screen.findByRole("option", { name: "stand3" }));
    fireEvent.click(screen.getByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(await screen.findByRole("option", { name: "1.8.1.6" }));
    await waitFor(() => expect(show).not.toBeDisabled());
    fireEvent.click(show);
  }

  it("короткое имя, экранирование dates.conf и исход теста уходят в createTestDefinition", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(screen.getByRole("button", { name: /Добавить тест/ }));
    fireEvent.change(await screen.findByPlaceholderText("FS-EXT4-FILL"), { target: { value: "file_systems.xfs" } });
    fireEvent.change(screen.getByPlaceholderText("filesystem / ext4 fill+remove cycle"), {
      target: { value: "file system benchmark. XFS" },
    });
    fireEvent.change(screen.getByPlaceholderText("XFS"), { target: { value: "XFS" } });
    fireEvent.click(screen.getByRole("button", { name: /shell — каждый токен/ }));
    fireEvent.click(await screen.findByRole("option", { name: /legacy — кавычки/ }));
    fireEvent.click(screen.getByRole("button", { name: /Статус тест-кейса в Zephyr/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Код выхода starter.sh" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createTestDefinitionMock).toHaveBeenCalledWith(expect.objectContaining({
        short_name: "XFS", dates_quoting: "legacy", verdict_source: "exit_code",
      })),
    );
  });

  it("без правок: short_name=null, dates_quoting=shell, verdict_source=zephyr", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(screen.getAllByRole("button", { name: "Изменить тест" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(updateTestDefinitionMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
        short_name: null, dates_quoting: "shell", verdict_source: "zephyr",
      })),
    );
  });

  it("карточка теста открывает превью запуска", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(screen.getAllByRole("button", { name: "Изменить тест" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: /Превью запуска/ }));
    expect(await screen.findByText(/Превью запуска · /)).toBeInTheDocument();
  });

  it("превью: выбор стенда, РЦ и ядра, показ команды, dates.conf, файлов и переменных", async () => {
    await openPreview();

    await waitFor(() =>
      expect(previewTestLaunchMock).toHaveBeenCalledWith("td_1", {
        stand_id: "st_1", os_version_id: "osv_1", kernel: "6.1.90-1-generic", mode: "orel", debug: false, testenv: false,
        step_index: 0,
      }),
    );
    const result = await screen.findByTestId("launch-preview-result");
    expect(within(result).getByLabelText("Команда запуска")).toHaveTextContent(
      "sudo bash /home/u/starter.sh file_systems git_token_qi_preview.conf dates_qi_preview.conf 1.8.1.6 ''",
    );
    expect(within(result).getByLabelText("dates.conf")).toHaveTextContent(
      "--confluence-new-page XFS_1.8.1.6_orel_6.1.90-1-generic_stand3",
    );
    expect(within(result).getByLabelText("/home/u/git_token_qi_preview.conf")).toHaveTextContent("***");
    expect(within(result).getByText("/home/u/starter.sh")).toBeInTheDocument();
    expect(within(result).getByLabelText("Команда остановки")).toHaveTextContent("pkill");
    expect(within(result).getByText("CONFLUENCE_TOKEN").closest("tr")).toHaveTextContent("***");
    expect(within(result).getByText("QUEUE_ITEM_ID").closest("tr")).toHaveTextContent("задание (профиль запуска)");
    expect(within(result).getByText(/профиль «Легаси starter.sh» v1/)).toBeInTheDocument();
  });

  it("ошибки этапов превью показываются с подсказкой", async () => {
    previewTestLaunchMock.mockResolvedValue({
      ...PREVIEW,
      dates_content_masked: null,
      files: PREVIEW.files.map((f) => (f.role === "dates" ? { ...f, content: null } : f)),
      errors: [{
        stage: "dates", error_code: "VARIABLE_VALUE_MISSING", message: "Zephyr folder folder_tree_id is not known",
        details: { hint: "сгенерируйте СТП" },
      }],
    });
    await openPreview();

    const alert = await screen.findByText(/провалится на подготовке задания/);
    expect(alert.parentElement).toHaveTextContent("VARIABLE_VALUE_MISSING");
    expect(alert.parentElement).toHaveTextContent("(сгенерируйте СТП)");
    expect(screen.getAllByText("не собрано — см. ошибки выше").length).toBe(2);
  });

  it("override_value слота подсказывает подстановки {CODE}", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");
    fireEvent.click(within(screen.getByText("FS-EXT4-FILL").closest("tr")!).getByRole("button", { name: /Конструктор/ }));
    await screen.findByText("backup_image.py");
    fireEvent.click(screen.getByRole("button", { name: /Добавить слот/ }));
    fireEvent.click(screen.getByRole("button", { name: "Переменная" }));
    expect(screen.getByText(/В override работают подстановки/)).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "override_value" });
    fireEvent.change(input, { target: { value: "{RC}_{MISSING}" } });
    expect(screen.getByText("{MISSING}")).toHaveAttribute("title", "Неизвестная переменная");
  });
});
