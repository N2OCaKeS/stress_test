import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Формы create/edit пользователя должны иметь поля Фамилия/Имя/Отчество и
// отправлять их в last_name/first_name/middle_name.

const createUserMock = vi.fn(async (_body: unknown) => ({ id: "usr_new" }));
const updateUserMock = vi.fn(async (_id: string, _body: unknown) => ({
  id: "usr_1",
}));

vi.mock("@/api/auth/users", () => ({
  createUser: (body: unknown) => createUserMock(body),
  updateUser: (id: string, body: unknown) => updateUserMock(id, body),
}));
vi.mock("@/api/auth/groups", () => ({ createGroup: vi.fn() }));
vi.mock("@/api/auth/bots", () => ({ createBot: vi.fn(), issueBotToken: vi.fn() }));

import { CreateUserForm, EditRolesForm } from "@/pages/users/_userActions";

const DEPTS = [{ id: "dep_1", name: "Ядро DBOS" }];

beforeEach(() => {
  createUserMock.mockClear();
  updateUserMock.mockClear();
});

describe("CreateUserForm — поля ФИО", () => {
  it("рендерит инпуты Фамилия/Имя/Отчество", () => {
    render(
      <CreateUserForm
        depts={DEPTS}
        mockMode={false}
        onSuccess={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByLabelText("Фамилия")).toBeInTheDocument();
    expect(screen.getByLabelText("Имя")).toBeInTheDocument();
    expect(screen.getByLabelText("Отчество")).toBeInTheDocument();
  });

  it("шлёт last_name/first_name/middle_name в createUser", async () => {
    const onSuccess = vi.fn();
    render(
      <CreateUserForm
        depts={DEPTS}
        mockMode={false}
        onSuccess={onSuccess}
        onCancel={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("username"), {
      target: { value: "ivanov" },
    });
    fireEvent.change(screen.getByLabelText("Фамилия"), {
      target: { value: "Иванов" },
    });
    fireEvent.change(screen.getByLabelText("Имя"), {
      target: { value: "Иван" },
    });
    fireEvent.change(screen.getByLabelText("Отчество"), {
      target: { value: "Иванович" },
    });
    // dept-select обёрнут в div с ошибкой-подсказкой, поэтому берём по роли:
    // он первый combobox в форме (перед platform_role).
    fireEvent.change(screen.getAllByRole("combobox")[0], {
      target: { value: "dep_1" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
    const body = createUserMock.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toMatchObject({
      username: "ivanov",
      last_name: "Иванов",
      first_name: "Иван",
      middle_name: "Иванович",
    });
    expect(onSuccess).toHaveBeenCalled();
  });
});

describe("EditRolesForm — поля ФИО", () => {
  it("рендерит инпуты ФИО с текущими значениями и шлёт изменения", async () => {
    const onSuccess = vi.fn();
    render(
      <EditRolesForm
        user={{
          id: "usr_1",
          username: "ivanov",
          platform_role: null,
          dept_id: null,
          last_name: "Иванов",
          first_name: "Иван",
          middle_name: null,
        }}
        depts={[]}
        mockMode={false}
        onSuccess={onSuccess}
        onCancel={() => {}}
      />,
    );

    const last = screen.getByLabelText("Фамилия") as HTMLInputElement;
    expect(last.value).toBe("Иванов");
    expect((screen.getByLabelText("Имя") as HTMLInputElement).value).toBe("Иван");

    fireEvent.change(last, { target: { value: "Смирнов" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(updateUserMock).toHaveBeenCalledTimes(1));
    const [id, body] = updateUserMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe("usr_1");
    expect(body).toMatchObject({ last_name: "Смирнов" });
    expect(onSuccess).toHaveBeenCalled();
  });
});
