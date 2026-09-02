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
const revealSshPrivateKeyMock = vi.fn((_id?: unknown) => new Promise(() => {}));
const revealPrevSshPrivateKeyMock = vi.fn(
  (_id?: unknown) => new Promise(() => {}),
);
const clearPrevSshPrivateKeyMock = vi.fn(
  (_id?: unknown) => new Promise(() => {}),
);
const setAccountSshKeyMock = vi.fn(
  (_id?: unknown, _b?: unknown) => new Promise(() => {}),
);
const rotateAccountSshKeyMock = vi.fn((_id?: unknown) => new Promise(() => {}));
const bindAccountServersMock = vi.fn(
  (_id?: unknown, _b?: unknown) => new Promise(() => {}),
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
  get revealAccountSshPrivateKey() {
    return revealSshPrivateKeyMock;
  },
  get revealPreviousAccountSshPrivateKey() {
    return revealPrevSshPrivateKeyMock;
  },
  get clearPreviousAccountSshPrivateKey() {
    return clearPrevSshPrivateKeyMock;
  },
  get setAccountSshKey() {
    return setAccountSshKeyMock;
  },
  get rotateAccountSshKey() {
    return rotateAccountSshKeyMock;
  },
  get bindAccountServers() {
    return bindAccountServersMock;
  },
}));

// Пикер юзеров тянет список отдела; в smoke-тестах держим pending.
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
// listTasks — сеялка последней инвентаризации для индикатора в тулбаре.
const usersInventoryMock = vi.fn(() => new Promise(() => {}));
const getTaskMock = vi.fn(() => new Promise(() => {}));
const listTasksMock = vi.fn(() => new Promise(() => {}));
vi.mock("@/api/server/misc", () => ({
  get usersInventory() {
    return usersInventoryMock;
  },
  get getTask() {
    return getTaskMock;
  },
  get listTasks() {
    return listTasksMock;
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
import { toBase64 } from "@/lib/base64";

const SUPPLY_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 supplied";
const SUPPLY_PRIV =
  "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n-----END OPENSSH PRIVATE KEY-----\n";

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
    revealSshPrivateKeyMock.mockReset();
    revealPrevSshPrivateKeyMock.mockReset();
    clearPrevSshPrivateKeyMock.mockReset();
    setAccountSshKeyMock.mockReset();
    rotateAccountSshKeyMock.mockReset();
    bindAccountServersMock.mockReset();
    listUsersByDepartmentMock.mockReset();
    resolveUserMock.mockReset();
    usersInventoryMock.mockReset();
    getTaskMock.mockReset();
    listTasksMock.mockReset();
    listServersMock.mockReturnValue(new Promise(() => {}));
    listAccountsMock.mockReturnValue(new Promise(() => {}));
    getAccountMock.mockReturnValue(new Promise(() => {}));
    adoptFromHostMock.mockReturnValue(new Promise(() => {}));
    createAccountMock.mockReturnValue(new Promise(() => {}));
    importUnknownUserMock.mockReturnValue(new Promise(() => {}));
    listIgnoredLoginsMock.mockReturnValue(new Promise(() => {}));
    addIgnoredLoginMock.mockReturnValue(new Promise(() => {}));
    removeIgnoredLoginMock.mockReturnValue(new Promise(() => {}));
    revealSshPrivateKeyMock.mockReturnValue(new Promise(() => {}));
    revealPrevSshPrivateKeyMock.mockReturnValue(new Promise(() => {}));
    clearPrevSshPrivateKeyMock.mockReturnValue(new Promise(() => {}));
    setAccountSshKeyMock.mockReturnValue(new Promise(() => {}));
    rotateAccountSshKeyMock.mockReturnValue(new Promise(() => {}));
    bindAccountServersMock.mockReturnValue(new Promise(() => {}));
    listUsersByDepartmentMock.mockReturnValue(new Promise(() => {}));
    resolveUserMock.mockReturnValue(new Promise(() => {}));
    usersInventoryMock.mockReturnValue(new Promise(() => {}));
    getTaskMock.mockReturnValue(new Promise(() => {}));
    listTasksMock.mockReturnValue(new Promise(() => {}));
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
        screen.getByRole("button", { name: /Изменить/ }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "Удалить" }),
    ).toBeInTheDocument();
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

  it("показывает подсказку парольной политики у поля пароля в форме создания", async () => {
    selectAccount();
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    expect(
      d.getByText(/минимум 8 символов, буквы и цифры/),
    ).toBeInTheDocument();
  });

  it("создание с генерацией ключа не показывает тело приватного ключа, а тостит про скачивание из карточки", async () => {
    selectAccount();
    const PRIVATE_BODY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfresh\n-----END-----";
    createAccountMock.mockResolvedValue({
      ...FAKE_ACCOUNT,
      id: "acc2",
      login: "new-svc",
      ssh_public_key: "ssh-ed25519 AAAAC3Nz newkey",
      ssh_private_key: PRIVATE_BODY,
    });
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    fireEvent.change(d.getByPlaceholderText("dbos-svc"), {
      target: { value: "new-svc" },
    });
    fireEvent.click(d.getByRole("checkbox", { name: /alpha/ }));
    // Режим SSH «сгенерировать» — дефолтный, отдельно кликать не нужно.
    fireEvent.click(d.getByRole("button", { name: /Создать/ }));

    await waitFor(() => {
      expect(createAccountMock).toHaveBeenCalledTimes(1);
    });

    // Тост направляет за приватным ключом в карточку аккаунта.
    expect(
      await screen.findByText(
        /Скачать приватный ключ можно позже кнопкой .* в карточке аккаунта/,
      ),
    ).toBeInTheDocument();

    // Тело приватного ключа нигде не отрендерено.
    expect(
      screen.queryByText(/BEGIN OPENSSH PRIVATE KEY/),
    ).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(PRIVATE_BODY)).not.toBeInTheDocument();
  });

  it("показывает fingerprint ключа и скачивает приватный ключ по кнопке", async () => {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [
        {
          ...FAKE_ACCOUNT,
          ssh_public_key: "ssh-ed25519 AAAAC3Nz key",
          ssh_key_fingerprint: "SHA256:abc123def",
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    revealSshPrivateKeyMock.mockResolvedValue({
      id: "acc1",
      login: "dbos-svc",
      ssh_private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\nzzz\n-----END-----",
      ssh_public_key: "ssh-ed25519 AAAAC3Nz key",
    });

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    // Fingerprint виден в секции SSH-ключа.
    expect(await screen.findByText("SHA256:abc123def")).toBeInTheDocument();

    // Кнопка скачивания приватного ключа есть у держателя view_password (dep_admin).
    const dl = await screen.findByRole("button", {
      name: /Скачать приватный ключ/,
    });
    fireEvent.click(dl);

    await waitFor(() => {
      expect(revealSshPrivateKeyMock).toHaveBeenCalledWith("acc1");
    });
  });

  it("удаляет предыдущий ключ через clearPreviousAccountSshPrivateKey и прячет кнопки previous", async () => {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    listAccountsMock.mockResolvedValue({
      items: [
        {
          ...FAKE_ACCOUNT,
          ssh_public_key: "ssh-ed25519 AAAAC3Nz key",
          ssh_key_fingerprint: "SHA256:abc123def",
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    clearPrevSshPrivateKeyMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    const clearBtn = await screen.findByRole("button", {
      name: /Удалить предыдущий ключ/,
    });
    fireEvent.click(clearBtn);

    // Подтверждаем в диалоге.
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Удалить/ }));

    await waitFor(() => {
      expect(clearPrevSshPrivateKeyMock).toHaveBeenCalledWith("acc1");
    });

    // После удаления кнопки previous-ключа исчезают.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /Удалить предыдущий ключ/ }),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /Скачать предыдущий ключ/ }),
    ).not.toBeInTheDocument();
  });

  it("после генерации ключа не показывает тело приватного ключа, а предлагает скачать", async () => {
    listServersMock.mockResolvedValue({
      items: [{ id: "srv1", display_name: "alpha", hostname: "alpha.local" }],
      total: 1,
    });
    // Аккаунт уже с ключом — кнопка «Скачать приватный ключ» доступна сразу.
    listAccountsMock.mockResolvedValue({
      items: [
        {
          ...FAKE_ACCOUNT,
          ssh_public_key: "ssh-ed25519 AAAAC3Nz key",
          ssh_key_fingerprint: "SHA256:abc123def",
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    const PRIVATE_BODY = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END-----";
    setAccountSshKeyMock.mockResolvedValue({
      id: "acc1",
      login: "dbos-svc",
      ssh_public_key: "ssh-ed25519 AAAAC3Nz newkey",
      ssh_private_key: PRIVATE_BODY,
      tasks: [],
      skipped: [],
    });

    renderPage();
    fireEvent.click(await screen.findByText("dbos-svc"));

    // Открываем форму замены ключа и сохраняем (режим generate по умолчанию).
    fireEvent.click(await screen.findByRole("button", { name: /Заменить ключ/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Сохранить/ }));

    await waitFor(() => {
      expect(setAccountSshKeyMock).toHaveBeenCalledWith("acc1", {
        ssh_mode: "generate",
        ssh_public_key: null,
      });
    });

    // Тост-уведомление с подсказкой про «скачать сейчас или позже».
    expect(
      await screen.findByText(/Скачать приватный ключ можно сейчас или позже/),
    ).toBeInTheDocument();

    // Тело приватного ключа нигде не отрендерено.
    expect(screen.queryByText(/BEGIN OPENSSH PRIVATE KEY/)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(PRIVATE_BODY)).not.toBeInTheDocument();

    // Механизм «скачать позже» — кнопка reveal остаётся доступной.
    expect(
      screen.getByRole("button", { name: /Скачать приватный ключ/ }),
    ).toBeInTheDocument();
  });

  it("supply: ввод публичного и приватного ключей шлёт ssh_private_key_b64", async () => {
    selectAccount();
    createAccountMock.mockResolvedValue({
      ...FAKE_ACCOUNT,
      id: "acc3",
      login: "svc-supply",
    });
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );
    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);

    fireEvent.change(d.getByPlaceholderText("dbos-svc"), {
      target: { value: "svc-supply" },
    });
    // Режим supply.
    fireEvent.click(
      d.getByRole("radio", { name: /вставить существующий ключ/ }),
    );
    // Публичный ключ — в поле.
    fireEvent.change(d.getByLabelText("Публичный SSH-ключ"), {
      target: { value: SUPPLY_PUB },
    });
    // Открываем ручной ввод приватного и заполняем его.
    fireEvent.click(d.getByRole("button", { name: /Ввести вручную/ }));
    fireEvent.change(d.getByLabelText("Приватный SSH-ключ"), {
      target: { value: SUPPLY_PRIV },
    });

    fireEvent.click(d.getByRole("button", { name: /Создать/ }));

    await waitFor(() => {
      expect(createAccountMock).toHaveBeenCalledTimes(1);
    });
    expect(createAccountMock.mock.calls[0][0]).toMatchObject({
      login: "svc-supply",
      ssh_mode: "supply",
      ssh_public_key: SUPPLY_PUB,
      ssh_private_key_b64: toBase64(SUPPLY_PRIV),
    });
  });

  it("supply: загрузка файла приватного ключа кодирует его в ssh_private_key_b64", async () => {
    selectAccount();
    createAccountMock.mockResolvedValue({
      ...FAKE_ACCOUNT,
      id: "acc4",
      login: "svc-file",
    });
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );
    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);

    fireEvent.change(d.getByPlaceholderText("dbos-svc"), {
      target: { value: "svc-file" },
    });
    fireEvent.click(
      d.getByRole("radio", { name: /вставить существующий ключ/ }),
    );
    fireEvent.change(d.getByLabelText("Публичный SSH-ключ"), {
      target: { value: SUPPLY_PUB },
    });

    // Загружаем приватный ключ файлом — FileReader читает его в состояние.
    const fileInput = d.getByLabelText("Загрузить приватный из файла");
    const file = new File([SUPPLY_PRIV], "id_ed25519", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(await d.findByText(/Приватный ключ задан/)).toBeInTheDocument();

    fireEvent.click(d.getByRole("button", { name: /Создать/ }));

    await waitFor(() => {
      expect(createAccountMock).toHaveBeenCalledTimes(1);
    });
    expect(createAccountMock.mock.calls[0][0]).toMatchObject({
      login: "svc-file",
      ssh_mode: "supply",
      ssh_public_key: SUPPLY_PUB,
      ssh_private_key_b64: toBase64(SUPPLY_PRIV),
    });
  });

  it("supply: без приватного ключа ssh_private_key_b64 не отправляется", async () => {
    selectAccount();
    createAccountMock.mockResolvedValue({
      ...FAKE_ACCOUNT,
      id: "acc5",
      login: "svc-pubonly",
    });
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /Создать пользователя/ }),
    );
    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);

    fireEvent.change(d.getByPlaceholderText("dbos-svc"), {
      target: { value: "svc-pubonly" },
    });
    fireEvent.click(
      d.getByRole("radio", { name: /вставить существующий ключ/ }),
    );
    fireEvent.change(d.getByLabelText("Публичный SSH-ключ"), {
      target: { value: SUPPLY_PUB },
    });

    fireEvent.click(d.getByRole("button", { name: /Создать/ }));

    await waitFor(() => {
      expect(createAccountMock).toHaveBeenCalledTimes(1);
    });
    const arg = createAccountMock.mock.calls[0][0] as Record<string, unknown>;
    expect(arg).toMatchObject({ ssh_mode: "supply", ssh_public_key: SUPPLY_PUB });
    expect(arg.ssh_private_key_b64).toBeUndefined();
  });

  it("показывает кнопку «Поиск на ОС» для оператора/менеджера", async () => {
    selectAccount();
    renderPage();
    // alice (dep_admin) — canOperate + canManage.
    expect(
      await screen.findByRole("button", { name: /Поиск на ОС/ }),
    ).toBeInTheDocument();
    // «Игнор-лист» переехал в администрирование — в тулбаре server users его нет.
    expect(
      screen.queryByRole("button", { name: /Игнор-лист/ }),
    ).not.toBeInTheDocument();
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

  it("discovery-модалка рендерит unlinked_existing и клик «Связать» зовёт bindAccountServers", async () => {
    selectAccount();
    usersInventoryMock.mockResolvedValue({ task_id: "tsk10", status: "queued" });
    getTaskMock.mockResolvedValue({
      id: "tsk10",
      kind: "users.inventory",
      status: "succeeded",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: {
        unknown_users: [],
        diffs: [],
        unlinked_existing: [
          {
            login: "svc-old",
            uid: 1700,
            candidates: [
              {
                account_id: "acc-existing",
                department_id: "dep1",
                source: "managed",
              },
            ],
          },
        ],
      },
    });
    bindAccountServersMock.mockResolvedValue({ ...FAKE_ACCOUNT, id: "acc-existing" });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Поиск на ОС/ }));

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    fireEvent.click(d.getByRole("button", { name: /Сканировать/ }));

    // Секция «Существующие аккаунты — связать с сервером» и строка логина.
    expect(
      await d.findByText(/Существующие аккаунты — связать с сервером/),
    ).toBeInTheDocument();
    expect(d.getByText("svc-old")).toBeInTheDocument();

    // Клик «Связать» дёргает bindAccountServers с server_id отсканированного сервера.
    fireEvent.click(d.getByRole("button", { name: /Связать svc-old/ }));
    await waitFor(() => {
      expect(bindAccountServersMock).toHaveBeenCalledTimes(1);
    });
    expect(bindAccountServersMock).toHaveBeenCalledWith("acc-existing", {
      server_ids: ["srv1"],
    });
  });

  it("после запуска долгого скана и закрытия модалки индикатор «Открыть результат» остаётся в точке запуска", async () => {
    selectAccount();
    usersInventoryMock.mockResolvedValue({ task_id: "tsk-long", status: "queued" });
    // Задача не завершается (вечный poll) — имитируем долгий скан.
    getTaskMock.mockReturnValue(new Promise(() => {}));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Поиск на ОС/ }));

    const dialog = await screen.findByRole("dialog");
    const d = within(dialog);
    fireEvent.click(d.getByRole("button", { name: /Сканировать/ }));

    // Дождались, что dispatch ушёл и точка запуска узнала task_id.
    await waitFor(() => {
      expect(usersInventoryMock).toHaveBeenCalledTimes(1);
    });

    // Закрываем модалку до завершения задачи.
    fireEvent.click(d.getByRole("button", { name: /Закрыть/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    // Индикатор последней инвентаризации остаётся в тулбаре с действием.
    expect(
      await screen.findByRole("button", { name: /Открыть результат/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Поиск на ОС.*идёт/)).toBeInTheDocument();
  });

  it("сеет индикатор последней инвентаризации с backend'а при заходе на страницу", async () => {
    selectAccount();
    // listTasks отдаёт последнюю готовую задачу, getTask — её результат.
    listTasksMock.mockResolvedValue({
      items: [
        {
          id: "tsk-seed",
          kind: "users.inventory",
          status: "succeeded",
          server_id: "srv1",
          created_at: "2026-01-01T00:00:00Z",
          retry_count: 0,
        },
      ],
      total: 1,
      limit: 1,
      offset: 0,
    });
    getTaskMock.mockResolvedValue({
      id: "tsk-seed",
      kind: "users.inventory",
      status: "succeeded",
      server_id: "srv1",
      created_at: "2026-01-01T00:00:00Z",
      retry_count: 0,
      result: { unknown_users: [], diffs: [] },
    });

    renderPage();

    // Индикатор «готов» с кнопкой открытия результата подтянулся без скана.
    expect(
      await screen.findByRole("button", { name: /Открыть результат/ }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(listTasksMock).toHaveBeenCalledWith({
        kind: "users.inventory",
        limit: 1,
      });
    });
  });
});
