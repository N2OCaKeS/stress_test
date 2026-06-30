import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

// ── Моки API и контекстов ────────────────────────────────────────────────────

const getCatalogMock = vi.fn();
const listPermissionsMock = vi.fn();
const setPermissionMock = vi.fn();
const deletePermissionMock = vi.fn();
vi.mock("@/api/server/permissions", () => ({
  getPermissionCatalog: (...a: unknown[]) => getCatalogMock(...a),
  listPermissions: (...a: unknown[]) => listPermissionsMock(...a),
  setPermission: (...a: unknown[]) => setPermissionMock(...a),
  deletePermission: (...a: unknown[]) => deletePermissionMock(...a),
}));

const listServiceRolesMock = vi.fn();
vi.mock("@/api/auth/service_roles", () => ({
  listServiceRoles: (...a: unknown[]) => listServiceRolesMock(...a),
  createServiceRole: vi.fn(),
  patchServiceRole: vi.fn(),
  deleteServiceRole: vi.fn(),
}));

vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => Promise.resolve({ items: [] })),
}));
vi.mock("@/api/server/accounts", () => ({
  listAccounts: vi.fn(() => Promise.resolve({ items: [] })),
}));

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: {
      id: "u1",
      dept_id: "core",
      platform_role: "dep_admin",
      service_roles: {},
    },
  }),
}));

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: async () => true }),
}));

import { ServicesServerPermissions } from "@/pages/admin/services/ServicesServerPermissions";

// Каталог: две сущности. У server есть worker-callback `prepare_callback`
// (worker_only) — редактор обязан полностью скрыть его из матрицы.
const CATALOG = [
  {
    entity_type: "server",
    description: "Сервер",
    actions: [
      {
        action: "power_reboot",
        description: "desc-reboot",
        sensitive: true,
        worker_only: false,
      },
      {
        action: "prepare_callback",
        description: "desc-cb",
        sensitive: false,
        worker_only: true,
      },
    ],
  },
  {
    entity_type: "task",
    description: "Задача",
    actions: [
      {
        action: "cancel",
        description: "desc-cancel",
        sensitive: false,
        worker_only: false,
      },
    ],
  },
];

function grantEntry(entity: string, role: string, action: string) {
  return {
    id: `perm_${entity}_${role}_${action}`,
    entity_type: entity,
    role,
    action,
    department_id: "core",
    granted_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function setup() {
  getCatalogMock.mockResolvedValue(CATALOG);
  listPermissionsMock.mockResolvedValue({
    items: [
      grantEntry("server", "operator", "power_reboot"),
      grantEntry("task", "operator", "cancel"),
    ],
    total: 2,
    described: true,
  });
  listServiceRolesMock.mockResolvedValue([
    { role_name: "operator", is_system: false, description: "" },
  ]);
  setPermissionMock.mockResolvedValue({});
  deletePermissionMock.mockResolvedValue(undefined);
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesServerPermissions />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesServerPermissions — батч-модель", () => {
  beforeEach(() => {
    getCatalogMock.mockReset();
    listPermissionsMock.mockReset();
    setPermissionMock.mockReset();
    deletePermissionMock.mockReset();
    listServiceRolesMock.mockReset();
    setup();
  });

  it("worker_only действие полностью скрыто из матрицы", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-reboot")).toBeInTheDocument(),
    );
    // prepare_callback (worker_only) не рендерится ни колонкой, ни ячейкой.
    expect(screen.queryByTitle(/desc-cb/)).not.toBeInTheDocument();
    expect(screen.queryByText("prepare_callback")).not.toBeInTheDocument();
  });

  it("клик по ячейке локален — применяется по «Сохранить» этой таблицы", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle(/desc-reboot Снять/)).toBeInTheDocument(),
    );
    const cell = screen.getByTitle(/desc-reboot Снять/) as HTMLInputElement;
    expect(cell.checked).toBe(true);
    fireEvent.click(cell);
    // Локально — ничего в сеть.
    await new Promise((r) => setTimeout(r, 20));
    expect(setPermissionMock).not.toHaveBeenCalled();
    expect(deletePermissionMock).not.toHaveBeenCalled();
    // Первая таблица (server) — её «Сохранить».
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith(
      "server",
      "operator",
      "power_reboot",
      { target_department_id: "core" },
    );
  });

  it("«Очистить роль в таблице» снимает её права → DELETE по Сохранить", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-reboot")).toBeInTheDocument(),
    );
    const clearBtn = screen.getByRole("button", {
      name: /Очистить роль operator в server/,
    });
    fireEvent.click(clearBtn);
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    await waitFor(() => expect(saveButtons[0]).not.toBeDisabled());
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith(
      "server",
      "operator",
      "power_reboot",
      { target_department_id: "core" },
    );
    expect(setPermissionMock).not.toHaveBeenCalled();
  });

  it("«Очистить во всех таблицах» снимает права роли во всех entity_type", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-reboot")).toBeInTheDocument(),
    );
    // Режим «Управление ролью».
    fireEvent.click(screen.getByText("Управление ролью"));
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "operator" } });
    // Глобальный сброс роли по всем таблицам + применение.
    fireEvent.click(
      await screen.findByRole("button", { name: /Очистить во всех таблицах/ }),
    );
    const saveRole = screen.getByRole("button", { name: /Сохранить роль/ });
    await waitFor(() => expect(saveRole).not.toBeDisabled());
    fireEvent.click(saveRole);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(2));
    const calls = deletePermissionMock.mock.calls.map((c) =>
      [c[0], c[1], c[2]].join("/"),
    );
    expect(calls).toContain("server/operator/power_reboot");
    expect(calls).toContain("task/operator/cancel");
  });

  it("worker_bot не показывается в матрице", async () => {
    listPermissionsMock.mockResolvedValue({
      items: [
        grantEntry("server", "operator", "power_reboot"),
        grantEntry("server", "worker_bot", "prepare_callback"),
      ],
      total: 2,
      described: true,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-reboot")).toBeInTheDocument(),
    );
    expect(screen.queryByText("worker_bot")).not.toBeInTheDocument();
  });
});
