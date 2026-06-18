import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

// Сетевые вызовы страницы мокаем — нужен только smoke-рендер (заголовок,
// плейсхолдер поиска, loading state). Список серверов держим в pending.
const listServersMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/servers", () => ({
  get listServers() {
    return listServersMock;
  },
}));

const listAccountsMock = vi.fn(() => new Promise(() => {}));
const getAccountMock = vi.fn(() => new Promise(() => {}));
const adoptFromHostMock = vi.fn(() => new Promise(() => {}));
const createAccountMock = vi.fn((_input?: unknown) => new Promise(() => {}));
const importUnknownUserMock = vi.fn((_b?: unknown) => new Promise(() => {}));
const listIgnoredLoginsMock = vi.fn(() => new Promise(() => {}));
const addIgnoredLoginMock = vi.fn((_b?: unknown) => new Promise(() => {}));
const removeIgnoredLoginMock = vi.fn((_l?: unknown) => new Promise(() => {}));
const listAccountAclMock = vi.fn((_id?: unknown) => new Promise(() => {}));
const addAccountAclMock = vi.fn((_id?: unknown, _b?: unknown) =>
  new Promise(() => {}),
);
const revokeAccountAclMock = vi.fn((_id?: unknown, _u?: unknown) =>
  new Promise(() => {}),
);
vi.mock("@/api/server/accounts", () => ({
  get listAccounts() {
    return listAccountsMock;
  },
  get getAccount() {
    return getAccountMock;
  },
  get adoptFromHost() {
    return adoptFromHostMock;
  },
  get createAccount() {
    return createAccountMock;
  },
  get importUnknownUser() {
    return importUnknownUserMock;
  },
  get listIgnoredLogins() {
    return listIgnoredLoginsMock;
  },
  get addIgnoredLogin() {
    return addIgnoredLoginMock;
  },
  get removeIgnoredLogin() {
    return removeIgnoredLoginMock;
  },
  get listAccountAcl() {
    return listAccountAclMock;
  },
  get addAccountAcl() {
    return addAccountAclMock;
  },
  get revokeAccountAcl() {
    return revokeAccountAclMock;
  },
}));

// Пикер юзеров в модалке ACL тянет список отдела; в smoke-тестах держим pending,
// чтобы модалка падала в ручной ввод username — selectAccountAndAcl задаёт его
// явно по необходимости.
const listUsersByDepartmentMock = vi.fn((_d?: unknown, _p?: unknown) =>
  new Promise(() => {}),
);
const resolveUserMock = vi.fn((_u?: unknown) => new Promise(() => {}));
vi.mock("@/api/auth/users", () => ({
  get listUsersByDepartment() {
    return listUsersByDepartmentMock;
  },
  get resolveUser() {
    return resolveUserMock;
  },
}));

// Ревизия дёргает users/inventory (misc) и поллит задачу через getTask.
const usersInventoryMock = vi.fn(() => new Promise(() => {}));
const getTaskMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/misc", () => ({
  get usersInventory() {
    return usersInventoryMock;
  },
  get getTask() {
    return getTaskMock;
  },
}));

// LabelsProvider тащит сетевые загрузки имён — подменяем хук карты серверов и
// label пользователей, остальные экспорты берём из оригинала.
vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  return {
    ...actual,
    useServerMap: () => new Map<string, string>([["srv1", "alpha"]]),
    useUserLabel: () => "—",
  };
});

import { ServerUsers } from "@/pages/server/ServerUsers";

function renderPage() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={["/server/users"]}>
              <ServerUsers />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

const FAKE_ACCOUNT = {
  id: "acc1",
  server_ids: ["srv1"],
  department_id: "dep1",
  login: "dbos-svc",
  source: "managed" as const,
  has_sudo: true,
  unix_groups: ["docker"],
  linked_user_id: null,
  shell: "/bin/bash",
  home_dir: "/home/dbos-svc",
  is_active: true,
  password_rotated_at: null,
  password_b64: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: null,
};

describe("ServerUsers (fleet account list)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listServersMock.mockReset();
    listAccountsMock.mockReset();
    getAccountMock.mockReset();
    adoptFromHostMock.mockReset();
    createAccountMock.mockReset();
    importUnknownUserMock.mockReset();
    listIgnoredLoginsMock.mockReset();
    addIgnoredLoginMock.mockReset();
    removeIgnoredLoginMock.mockReset();
    listAccountAclMock.mockReset();
    addAccountAclMock.mockReset();
    revokeAccountAclMock.mockReset();
    listUsersByDepartmentMock.mockReset();
    resolveUserMock.mockReset();
    usersInventoryMock.mockReset();
    getTaskMock.mockReset();
    listServersMock.mockReturnValue(new Promise(() => {}));
    listAccountsMock.mockReturnValue(new Promise(() => {}));
    getAccountMock.mockReturnValue(new Promise(() => {}));
    adoptFromHostMock.mockReturnValue(new Promise(() => {}));
    createAccountMock.mockReturnValue(new Promise(() => {}));
    importUnknownUserMock.mockReturnValue(new Promise(() => {}));
    listIgnoredLoginsMock.mockReturnValue(new Promise(() => {}));
    addIgnoredLoginMock.mockReturnValue(new Promise(() => {}));
    removeIgnoredLoginMock.mockReturnValue(new Promise(() => {}));
    listAccountAclMock.mockReturnValue(new Promise(() => {}));
    addAccountAclMock.mockReturnValue(new Promise(() => {}));
    revokeAccountAclMock.mockReturnValue(new Promise(() => {}));
    listUsersByDepartmentMock.mockReturnValue(new Promise(() => {}));
    resolveUserMock.mockReturnValue(new Promise(() => {}));
    usersInventoryMock.mockReturnValue(new Promise(() => {}));
    getTaskMock.mockReturnValue(new Promise(() => {}));
  });

  function selectAccount() {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [FAKE_ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });
  }

  it("рендерится и показывает loading state, пока грузятся серверы", () => {
    renderPage();
    expect(screen.getByText(/server_service \/ server users/)).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/Поиск по 0 аккаунтам/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
  });

  it("показывает рабочую зону аккаунта в правой панели по клику на строку", async () => {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [FAKE_ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });

    renderPage();

    const loginCell = await screen.findByText("dbos-svc");
    fireEvent.click(loginCell);

    // Правая рабочая зона показывает управляющие кнопки наверху.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Редактировать/ }),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Удалить/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Ротировать \(БД\)/ }),
    ).toBeInTheDocument();

    // Секция «Серверы аккаунта» с per-server provision/deprovision.
    expect(screen.getByText(/Серверы аккаунта/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Provision/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Deprovision/ }),
    ).toBeInTheDocument();
    // Кнопка привязки сервера тоже на месте.
    expect(
      screen.getByRole("button", { name: /Привязать/ }),
    ).toBeInTheDocument();
  });

  it("показывает кнопку «Ревизия» у сервера в секции", async () => {
    selectAccount();
    renderPage();

    fireEvent.click(await screen.findByText("dbos-svc"));
    expect(
      await screen.findByRole("button", { name: /Ревизия/ }),
    ).toBeInTheDocument();
  });

  it("открывает модалку diff с было/стало и чекбоксами по завершении ревизии", async () => {
    selectAccount();
    usersInventoryMock.mockResolvedValue({ task_id: "tsk1", status: "queued" });
    // Первый же поллинг возвращает succeeded с расхождениями — модалка
    // открывается автоматически (быстрый таск).
    getTaskMock.mockResolvedValue({
      id: "tsk1",
      kind: "users.inventory",
      status: "succeeded",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        diffs: [
          {
            account_id: "acc1",
            login: "dbos-svc",
            fields: {
              has_sudo: { expected: false, found: true },
              unix_groups: { expected: ["postgres"], found: ["wheel"] },
              shell: { expected: "/bin/bash", found: "/bin/sh" },
            },
          },
        ],
      },
    });

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));
    fireEvent.click(await screen.findByRole("button", { name: /Ревизия/ }));

    // Модалка с заголовком и кнопкой применения.
    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    expect(
      d.getByRole("button", { name: /Применить к БД/ }),
    ).toBeInTheDocument();

    // Было → стало для shell (внутри модалки).
    expect(d.getByText("/bin/bash")).toBeInTheDocument();
    expect(d.getByText("/bin/sh")).toBeInTheDocument();

    // Чекбоксы по полям присутствуют и по умолчанию отмечены.
    const sudoCheck = d.getByLabelText(/Применить has_sudo/);
    expect((sudoCheck as HTMLInputElement).checked).toBe(true);
    expect(d.getByLabelText(/Применить unix_groups/)).toBeInTheDocument();
    expect(d.getByLabelText(/Применить shell/)).toBeInTheDocument();
  });

  it("показывает кнопку «Создать пользователя» для canManage-персоны (dep_admin)", async () => {
    selectAccount();
    renderPage();
    // alice (dep_admin) — дефолтная persona, у неё canManage.
    expect(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    ).toBeInTheDocument();
  });

  it("открывает форму создания и сабмит зовёт createAccount", async () => {
    selectAccount();
    createAccountMock.mockResolvedValue({ ...FAKE_ACCOUNT, id: "acc2", login: "new-svc" });
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);

    // login
    fireEvent.change(d.getByPlaceholderText("dbos-svc"), {
      target: { value: "new-svc" },
    });
    // выбрать сервер (чекбокс)
    fireEvent.click(d.getByRole("checkbox", { name: /alpha/ }));
    // сабмит (в модалке только submit-кнопка «Создать»)
    fireEvent.click(d.getByRole("button", { name: /Создать/ }));

    await waitFor(() => {
      expect(createAccountMock).toHaveBeenCalledTimes(1);
    });
    const arg = createAccountMock.mock.calls[0][0];
    expect(arg).toMatchObject({ login: "new-svc", server_ids: ["srv1"] });
  });

  it("показывает кнопки «Поиск на ОС» и «Игнор-лист» для оператора/менеджера", async () => {
    selectAccount();
    renderPage();
    // alice (dep_admin) — canOperate + canManage.
    expect(
      await screen.findByRole("button", { name: /Поиск на ОС/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Игнор-лист/ }),
    ).toBeInTheDocument();
  });

  it("discovery-модалка рендерит unknown_users с режимами add/ignore после скана", async () => {
    selectAccount();
    usersInventoryMock.mockResolvedValue({ task_id: "tsk9", status: "queued" });
    getTaskMock.mockResolvedValue({
      id: "tsk9",
      kind: "users.inventory",
      status: "succeeded",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        unknown_users: [
          {
            login: "ghost",
            uid: 1500,
            has_sudo: true,
            unix_groups: ["wheel"],
            shell: "/bin/bash",
          },
        ],
        diffs: [],
      },
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Поиск на ОС/ }));

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    fireEvent.click(d.getByRole("button", { name: /Сканировать/ }));

    // Незнакомый юзер появился с режимами выбора.
    expect(await d.findByText("ghost")).toBeInTheDocument();
    expect(d.getByLabelText(/Добавить ghost/)).toBeInTheDocument();
    expect(d.getByLabelText(/Игнорировать ghost/)).toBeInTheDocument();
  });

  it("секция «Доступ к учётке» видна для canManage и рендерит гранты из listAccountAcl", async () => {
    selectAccount();
    listAccountAclMock.mockResolvedValue([
      {
        id: "acl1",
        account_id: "acc1",
        user_id: "usr_42",
        department_id: "dep1",
        actions: ["view", "rotate_password"],
        created_by: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    // Секция и её кнопка выдачи.
    expect(await screen.findByText(/Доступ к учётке/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Выдать доступ/ }),
    ).toBeInTheDocument();

    // Грант с человекочитаемыми бэйджами действий + кнопка «Снять».
    expect(await screen.findByText("Видеть")).toBeInTheDocument();
    expect(screen.getByText("Ротация пароля")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Снять/ }),
    ).toBeInTheDocument();
  });

  it("empty-state секции — «Прямых грантов нет»", async () => {
    selectAccount();
    listAccountAclMock.mockResolvedValue([]);

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    expect(
      await screen.findByText(/Прямых грантов нет/),
    ).toBeInTheDocument();
  });

  it("модалка выдачи доступа показывает чекбоксы действий и зовёт addAccountAcl", async () => {
    selectAccount();
    listAccountAclMock.mockResolvedValue([]);
    addAccountAclMock.mockResolvedValue({
      id: "acl2",
      account_id: "acc1",
      user_id: "usr_99",
      department_id: "dep1",
      actions: ["view"],
      created_by: null,
      created_at: "2026-01-01T00:00:00Z",
    });

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    fireEvent.click(
      await screen.findByRole("button", { name: /Выдать доступ/ }),
    );

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);

    // Чекбоксы действий с человекочитаемыми подписями.
    expect(d.getByLabelText("Видеть")).toBeInTheDocument();
    expect(d.getByLabelText("Видеть пароль")).toBeInTheDocument();
    expect(d.getByLabelText("Выдавать sudo")).toBeInTheDocument();

    // Пикер юзеров pending → ручной ввод username (сырой id принимается).
    fireEvent.change(d.getByPlaceholderText(/username, usr_/), {
      target: { value: "usr_99" },
    });
    // «view» отмечен по умолчанию — сразу выдаём.
    fireEvent.click(d.getByRole("button", { name: /Выдать/ }));

    await waitFor(() => {
      expect(addAccountAclMock).toHaveBeenCalledTimes(1);
    });
    expect(addAccountAclMock.mock.calls[0][0]).toBe("acc1");
    expect(addAccountAclMock.mock.calls[0][1]).toMatchObject({
      user_id: "usr_99",
      actions: ["view"],
    });
  });

  it("ignore-модалка рендерит список из listIgnoredLogins", async () => {
    selectAccount();
    listIgnoredLoginsMock.mockResolvedValue([
      {
        id: "ig1",
        department_id: "dep1",
        login: "backup-svc",
        reason: "вендорский",
        created_by: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Игнор-лист/ }));

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    expect(await d.findByText("backup-svc")).toBeInTheDocument();
    expect(
      d.getByRole("button", { name: /Снять игнор/ }),
    ).toBeInTheDocument();
  });
});
