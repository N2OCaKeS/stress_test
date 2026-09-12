import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { TestCommandArg, TestDefinition, GlobalVariable } from "@/api/testing/types";

const confirmMock = vi.fn(async () => true);
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

const listTestDefinitionsMock = vi.fn();
const createTestDefinitionMock = vi.fn();
const updateTestDefinitionMock = vi.fn();
const deleteTestDefinitionMock = vi.fn();
vi.mock("@/api/testing/testDefinitions", () => ({
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

const listGlobalVariablesMock = vi.fn();
const getGlobalVariableChoicesMock = vi.fn();
const createGlobalVariableMock = vi.fn();
const updateGlobalVariableMock = vi.fn();
const deleteGlobalVariableMock = vi.fn();
vi.mock("@/api/testing/global_variables", () => ({
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
    department_id: null,
    pinned_stand_id: null,
    changelog_component: null,
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
    department_id: null,
    pinned_stand_id: null,
    changelog_component: null,
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

  it("редактирование теста вызывает updateTestDefinition с id теста", async () => {
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getAllByRole("button", { name: "Изменить тест" })[0]);
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

    fireEvent.click(screen.getAllByRole("button", { name: "Удалить тест" })[0]);

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(deleteTestDefinitionMock).toHaveBeenCalledWith("td_1"));
  });

  it("клонирование теста предзаполняет форму и копирует слоты команды исходного теста", async () => {
    createTestDefinitionMock.mockResolvedValue({ ...TESTS[0], id: "td_3", code: "FS-EXT4-FILL.copy" });
    renderWorkzone();
    await screen.findByText("FS-EXT4-FILL");

    fireEvent.click(screen.getAllByRole("button", { name: "Клонировать тест" })[0]);

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
    fireEvent.click(screen.getAllByRole("button", { name: /Конструктор/ })[0]);
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
    fireEvent.click(await screen.findByRole("option", { name: "RC · Release candidate" }));
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
    fireEvent.click(screen.getAllByRole("button", { name: /Конструктор/ })[0]);
    await screen.findByText(/Конструктор команды/);

    fireEvent.click(screen.getByRole("button", { name: /Добавить слот/ }));
    fireEvent.click(screen.getByRole("button", { name: "Переменная" }));
    fireEvent.click(screen.getByRole("button", { name: /выберите переменную/ }));
    expect(await screen.findByRole("option", { name: /NEW_VAR/ })).toBeInTheDocument();
  });
});
