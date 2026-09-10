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
const createTestCommandArgMock = vi.fn();
const updateTestCommandArgMock = vi.fn();
const deleteTestCommandArgMock = vi.fn();
vi.mock("@/api/testing/testCommandArgs", () => ({
  listTestCommandArgs: (...args: unknown[]) => listTestCommandArgsMock(...args),
  createTestCommandArg: (...args: unknown[]) => createTestCommandArgMock(...args),
  updateTestCommandArg: (...args: unknown[]) => updateTestCommandArgMock(...args),
  deleteTestCommandArg: (...args: unknown[]) => deleteTestCommandArgMock(...args),
}));

const listGlobalVariablesMock = vi.fn();
const getGlobalVariableChoicesMock = vi.fn();
vi.mock("@/api/testing/global_variables", () => ({
  listGlobalVariables: (...args: unknown[]) => listGlobalVariablesMock(...args),
  getGlobalVariableChoices: (...args: unknown[]) => getGlobalVariableChoicesMock(...args),
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
    readiness: "draft",
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
  listGlobalVariablesMock.mockResolvedValue({ items: VARIABLES, total: VARIABLES.length, limit: 200, offset: 0 });
  createTestDefinitionMock.mockResolvedValue(TESTS[0]);
  updateTestDefinitionMock.mockResolvedValue(TESTS[0]);
  deleteTestDefinitionMock.mockResolvedValue({ ok: true });
  createTestCommandArgMock.mockResolvedValue(SLOTS[0]);
  updateTestCommandArgMock.mockResolvedValue(SLOTS[0]);
  deleteTestCommandArgMock.mockResolvedValue({ ok: true });
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
        expect.objectContaining({ code: "NET-IPERF3", full_name: "network / iperf3 throughput" }),
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
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateTestDefinitionMock).toHaveBeenCalledWith(
        "td_1",
        expect.objectContaining({ full_name: "filesystem / ext4 fill+remove cycle v2" }),
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
