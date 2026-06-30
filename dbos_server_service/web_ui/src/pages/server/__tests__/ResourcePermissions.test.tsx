import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const apiGetMock = vi.fn();
const apiPutMock = vi.fn();
const apiDeleteMock = vi.fn();
const apiPostMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPut: (...a: unknown[]) => apiPutMock(...a),
    apiDelete: (...a: unknown[]) => apiDeleteMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
  };
});

import { ResourceInstancePermissions } from "@/pages/server/_resourcePermissions";

// Каталог: инстанс-грантуемое (view, power_reboot), глобальное create и
// worker-callback inventory_submit — последние два редактор обязан скрыть.
const CATALOG = [
  {
    entity_type: "server",
    description: "Сервер",
    actions: [
      { action: "view", description: "desc-view", sensitive: false, worker_only: false },
      { action: "create", description: "desc-create", sensitive: false, worker_only: false },
      { action: "power_reboot", description: "desc-reboot", sensitive: true, worker_only: false },
      { action: "inventory_submit", description: "desc-inv", sensitive: false, worker_only: true },
    ],
  },
];

// Базовая тип-wide матрица: operator умеет view + power_reboot, guest — view.
// admin считается полным доступом неявно. worker_bot базы не имеет — но и в
// матрице его быть не должно вовсе.
const BASE_MATRIX = {
  items: [
    { entity_type: "server", role: "guest", action: "view" },
    { entity_type: "server", role: "operator", action: "view" },
    { entity_type: "server", role: "operator", action: "power_reboot" },
  ],
  total: 3,
  described: false,
};

interface SetupOpts {
  // Инстанс-гранты, которые отдаёт by-resource.
  grants?: Array<Record<string, unknown>>;
  // Кастомные роли отдела (auth service_roles).
  serviceRoles?: Array<{ role_name: string }>;
}

function setupApi(opts: SetupOpts = {}) {
  const grants = opts.grants ?? [
    {
      id: "rrp_1",
      resource_type: "server",
      resource_id: "srv_1",
      role: "operator",
      action: "power_reboot",
      effect: "allow",
      department_id: "core",
      granted_by: "u1",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ];
  const serviceRoles = opts.serviceRoles ?? [{ role_name: "operator" }];
  apiGetMock.mockImplementation((path: string) => {
    if (path.includes("/permissions/catalog")) return Promise.resolve(CATALOG);
    if (path.includes("/resource-permissions/by-resource"))
      return Promise.resolve({ items: grants, total: grants.length });
    if (path === "/server/v1/permissions")
      return Promise.resolve(BASE_MATRIX);
    // auth service-roles
    return Promise.resolve(serviceRoles);
  });
  apiPutMock.mockResolvedValue({});
  apiDeleteMock.mockResolvedValue(undefined);
}

function renderEditor(canEdit = true, opts: SetupOpts = {}) {
  setupApi(opts);
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <ResourceInstancePermissions
            resourceType="server"
            resourceId="srv_1"
            resourceLabel="srv-node-01"
            departmentId="core"
            canEdit={canEdit}
            fetchTargets={async () => []}
          />
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("ResourceInstancePermissions", () => {
  beforeEach(() => {
    // Редактор в mock-режиме читает локальный стор и не дёргает сеть — для
    // проверки live-связки выключаем его.
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    apiGetMock.mockReset();
    apiPutMock.mockReset();
    apiDeleteMock.mockReset();
    apiPostMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("показывает инстанс-грантуемые действия и прячет create / worker-callback", async () => {
    renderEditor();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    expect(screen.getByTitle("desc-reboot")).toBeInTheDocument();
    expect(screen.queryByTitle("desc-create")).not.toBeInTheDocument();
    expect(screen.queryByTitle("desc-inv")).not.toBeInTheDocument();
  });

  it("прячет внутреннюю роль worker_bot, даже если она в грантах", async () => {
    renderEditor(true, {
      grants: [
        {
          id: "rrp_wb",
          resource_type: "server",
          resource_id: "srv_1",
          role: "worker_bot",
          action: "power_reboot",
          effect: "allow",
          department_id: "core",
          granted_by: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      serviceRoles: [{ role_name: "operator" }],
    });
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    expect(screen.queryByText("worker_bot")).not.toBeInTheDocument();
  });

  // Колонки: view, power_reboot. Строки: guest, admin (системные), operator.
  // Чекбоксы по порядку: [guest/view, guest/reboot, admin/view, admin/reboot,
  //  operator/view, operator/reboot].
  it("чекбоксы проставлены по базовой матрице + инстанс-override", async () => {
    renderEditor();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    const cells = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // guest: база только view → view вкл, reboot выкл.
    expect(cells[0].checked).toBe(true);
    expect(cells[1].checked).toBe(false);
    // admin: полный доступ → всё вкл.
    expect(cells[2].checked).toBe(true);
    expect(cells[3].checked).toBe(true);
    // operator: база view+reboot, инстанс allow на reboot → оба вкл.
    expect(cells[4].checked).toBe(true);
    expect(cells[5].checked).toBe(true);
  });

  it("системные роли admin/guest залочены (disabled), клик не копит правок", async () => {
    renderEditor();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    const cells = screen.getAllByRole("checkbox");
    expect(cells[0]).toBeDisabled(); // guest/view
    expect(cells[2]).toBeDisabled(); // admin/view
    fireEvent.click(cells[0]);
    fireEvent.click(cells[2]);
    // «Сохранить» остаётся неактивной — диффа нет.
    expect(screen.getByRole("button", { name: /Сохранить/ })).toBeDisabled();
  });

  it("снятая с базы галка → DELETE/PUT deny через Сохранить", async () => {
    renderEditor();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    const cells = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // operator/power_reboot (индекс 5): пришёл allow, база тоже даёт reboot.
    // Снимаем галку → желаемое false, база true → инстанс deny.
    fireEvent.click(cells[5]);
    const saveBtn = screen.getByRole("button", { name: /Сохранить/ });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    fireEvent.click(saveBtn);
    await waitFor(() => expect(apiPutMock).toHaveBeenCalledTimes(1));
    expect(apiPutMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server/srv_1/operator/power_reboot",
      undefined,
      { query: { effect: "deny" } },
    );
    expect(apiDeleteMock).not.toHaveBeenCalled();
  });

  it("возврат галки к базе снимает override (без запросов на Сохранить)", async () => {
    // Без инстанс-грантов operator живёт ровно по базе (view+reboot).
    renderEditor(true, { grants: [], serviceRoles: [{ role_name: "operator" }] });
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    const cells = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // operator/power_reboot (индекс 5): база true, override нет. Снимаем (deny)
    // → дифф появляется; возвращаем (совпало с базой) → override снят, диффа
    // снова нет.
    fireEvent.click(cells[5]);
    expect(screen.getByRole("button", { name: /Сохранить/ })).not.toBeDisabled();
    fireEvent.click(cells[5]);
    expect(screen.getByRole("button", { name: /Сохранить/ })).toBeDisabled();
  });

  it("view авто-включается и блокируется у роли с другими правами", async () => {
    // Уберём инстанс-грант, оставим базу operator (view+reboot). operator имеет
    // другое право (reboot) → view forced+locked.
    renderEditor(true, { grants: [], serviceRoles: [{ role_name: "operator" }] });
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    const cells = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // operator/view (индекс 4) — checked и disabled.
    expect(cells[4].checked).toBe(true);
    expect(cells[4]).toBeDisabled();
  });

  it("без кастомных ролей всё равно рендерит залоченные admin/guest", async () => {
    renderEditor(true, { grants: [], serviceRoles: [] });
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    // Матрица показана: системные роли видны строками.
    expect(screen.getByText("guest")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    // Колонки view × power_reboot для двух системных ролей → 4 чекбокса,
    // и все они залочены (disabled).
    const cells = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(cells).toHaveLength(4);
    for (const c of cells) expect(c).toBeDisabled();
  });
});
