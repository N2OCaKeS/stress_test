import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

// ── Моки API и контекстов ────────────────────────────────────────────────────

const getCatalogMock = vi.fn();
const listPermissionsMock = vi.fn();
const setPermissionMock = vi.fn();
const deletePermissionMock = vi.fn();
vi.mock("@/api/testing/permissions", () => ({
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

let mockPersona: {
  id: string;
  dept_id: string | null;
  platform_role: string | null;
  service_roles: Record<string, string>;
} = {
  id: "u1",
  dept_id: "core",
  platform_role: "dep_admin",
  service_roles: {},
};
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({ persona: mockPersona }),
}));

vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: async () => true }),
}));

import { ServicesTestingPermissions } from "@/pages/admin/services/ServicesTestingPermissions";

// Каталог: две сущности. У test_stand есть чувствительное
// `view_test_credentials` (не worker_only — testing_service не грантует
// callback'и через матрицу), у department_test_settings — обычный `update`.
const CATALOG = [
  {
    entity_type: "test_stand",
    description: "Тестовый стенд",
    actions: [
      {
        action: "view_test_credentials",
        description: "desc-view-creds",
        sensitive: true,
        worker_only: false,
      },
      {
        action: "update",
        description: "desc-update-stand",
        sensitive: false,
        worker_only: false,
      },
    ],
  },
  {
    entity_type: "department_test_settings",
    description: "Настройки тестирования отдела",
    actions: [
      {
        action: "update",
        description: "desc-update-settings",
        sensitive: false,
        worker_only: false,
      },
    ],
  },
];

function grantEntry(entity: string, role: string, action: string) {
  return {
    id: `prm_${entity}_${role}_${action}`,
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
      grantEntry("test_stand", "operator", "view_test_credentials"),
      grantEntry("department_test_settings", "operator", "update"),
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
        <ServicesTestingPermissions />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesTestingPermissions — RBAC гейт", () => {
  beforeEach(() => {
    getCatalogMock.mockReset();
    listPermissionsMock.mockReset();
    setup();
  });

  it("персона без department_admin/testing.admin/account_admin видит 403", () => {
    mockPersona = {
      id: "u2",
      dept_id: "core",
      platform_role: null,
      service_roles: {},
    };
    renderPage();
    expect(screen.getByText(/403/)).toBeInTheDocument();
    expect(getCatalogMock).not.toHaveBeenCalled();
  });

  it("account_admin видит матрицу, хотя у него нет department_id", async () => {
    mockPersona = {
      id: "u3",
      dept_id: null,
      platform_role: "account_admin",
      service_roles: {},
    };
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view-creds")).toBeInTheDocument(),
    );
  });

  it("носитель testing.admin видит матрицу без department_admin", async () => {
    mockPersona = {
      id: "u4",
      dept_id: "core",
      platform_role: null,
      service_roles: { testing: "admin" },
    };
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view-creds")).toBeInTheDocument(),
    );
  });
});

describe("ServicesTestingPermissions — батч-модель", () => {
  beforeEach(() => {
    getCatalogMock.mockReset();
    listPermissionsMock.mockReset();
    setPermissionMock.mockReset();
    deletePermissionMock.mockReset();
    listServiceRolesMock.mockReset();
    mockPersona = {
      id: "u1",
      dept_id: "core",
      platform_role: "dep_admin",
      service_roles: {},
    };
    setup();
  });

  it("рендерит обе сущности каталога с их действиями", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view-creds")).toBeInTheDocument(),
    );
    expect(screen.getByText("test_stand")).toBeInTheDocument();
    expect(screen.getByText("department_test_settings")).toBeInTheDocument();
    expect(screen.getByTitle("desc-update-settings")).toBeInTheDocument();
  });

  it("клик по ячейке локален — применяется по «Сохранить» этой таблицы", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle(/desc-view-creds Снять/)).toBeInTheDocument(),
    );
    const cell = screen.getByTitle(/desc-view-creds Снять/) as HTMLInputElement;
    expect(cell.checked).toBe(true);
    fireEvent.click(cell);
    // Локально — ничего в сеть.
    await new Promise((r) => setTimeout(r, 20));
    expect(setPermissionMock).not.toHaveBeenCalled();
    expect(deletePermissionMock).not.toHaveBeenCalled();
    // Первая таблица (test_stand) — её «Сохранить».
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith(
      "test_stand",
      "operator",
      "view_test_credentials",
      { target_department_id: "core" },
    );
  });

  it("выдача нового права шлёт PUT по «Сохранить»", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle(/desc-update-stand Поставить/)).toBeInTheDocument(),
    );
    const cell = screen.getByTitle(
      /desc-update-stand Поставить/,
    ) as HTMLInputElement;
    expect(cell.checked).toBe(false);
    fireEvent.click(cell);
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(setPermissionMock).toHaveBeenCalledTimes(1));
    expect(setPermissionMock).toHaveBeenCalledWith(
      "test_stand",
      "operator",
      "update",
      { target_department_id: "core" },
    );
  });

  it("«Очистить роль» снимает права выбранной роли в таблице → DELETE по Сохранить", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view-creds")).toBeInTheDocument(),
    );
    // В таблице test_stand единственная незалоченная роль — operator.
    const clearBtn = screen.getAllByRole("button", {
      name: "Очистить роль",
    })[0];
    fireEvent.click(clearBtn);
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    await waitFor(() => expect(saveButtons[0]).not.toBeDisabled());
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith(
      "test_stand",
      "operator",
      "view_test_credentials",
      { target_department_id: "core" },
    );
    expect(setPermissionMock).not.toHaveBeenCalled();
  });

  it("«Очистить во всех таблицах» снимает права роли во всех entity_type", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view-creds")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Управление ролью"));
    const trigger = await screen.findByRole("button", { name: "— выберите роль —" });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("option", { name: "operator" }));
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
    expect(calls).toContain("test_stand/operator/view_test_credentials");
    expect(calls).toContain("department_test_settings/operator/update");
  });
});
