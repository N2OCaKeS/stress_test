import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

// ── Моки API и контекстов ────────────────────────────────────────────────────

const getCatalogMock = vi.fn();
const listPermissionsMock = vi.fn();
const setPermissionMock = vi.fn();
const deletePermissionMock = vi.fn();
vi.mock("@/api/secret/permissions", () => ({
  getSecretPermissionCatalog: (...a: unknown[]) => getCatalogMock(...a),
  listSecretPermissions: (...a: unknown[]) => listPermissionsMock(...a),
  setSecretPermission: (...a: unknown[]) => setPermissionMock(...a),
  deleteSecretPermission: (...a: unknown[]) => deletePermissionMock(...a),
}));

const listServiceRolesMock = vi.fn();
vi.mock("@/api/auth/service_roles", () => ({
  listServiceRoles: (...a: unknown[]) => listServiceRolesMock(...a),
  createServiceRole: vi.fn(),
  patchServiceRole: vi.fn(),
  deleteServiceRole: vi.fn(),
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

import { ServicesSecretPermissions } from "@/pages/admin/services/ServicesSecretPermissions";

// Каталог: единственная сущность `secret` с полным набором действий.
const CATALOG = [
  {
    entity_type: "secret",
    description: "Секрет",
    actions: [
      { action: "read", description: "desc-read", sensitive: false },
      { action: "reveal", description: "desc-reveal", sensitive: true },
      { action: "write", description: "desc-write", sensitive: false },
      { action: "delete", description: "desc-delete", sensitive: true },
    ],
  },
];

function grantEntry(role: string, action: string) {
  return {
    id: `prm_${role}_${action}`,
    entity_type: "secret",
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
    items: [grantEntry("operator", "read")],
    total: 1,
    described: true,
  });
  listServiceRolesMock.mockResolvedValue([
    { role_name: "operator", is_system: false, description: "" },
  ]);
  setPermissionMock.mockResolvedValue({});
  deletePermissionMock.mockResolvedValue({ ok: true });
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesSecretPermissions />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesSecretPermissions — батч-модель", () => {
  beforeEach(() => {
    getCatalogMock.mockReset();
    listPermissionsMock.mockReset();
    setPermissionMock.mockReset();
    deletePermissionMock.mockReset();
    listServiceRolesMock.mockReset();
    setup();
  });

  it("рендерит каталог: залоченные guest/admin + кастомная роль", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-read")).toBeInTheDocument(),
    );
    // Системные роли в матрице присутствуют (текст встречается и в пояснении,
    // поэтому проверяем ≥1) и залочены — ячейки admin/guest disabled.
    expect(screen.getAllByText("guest").length).toBeGreaterThan(0);
    expect(screen.getAllByText("admin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("operator").length).toBeGreaterThan(0);
    // Залоченная строка: чекбокс read у guest недоступен для правки.
    const guestRead = screen.getAllByTitle(
      /desc-read/,
    ) as HTMLInputElement[];
    expect(guestRead.some((el) => el.disabled)).toBe(true);
  });

  it("клик по ячейке кастомной роли локален — PUT по «Сохранить»", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle(/desc-write Поставить/)).toBeInTheDocument(),
    );
    const cell = screen.getByTitle(/desc-write Поставить/) as HTMLInputElement;
    expect(cell.checked).toBe(false);
    fireEvent.click(cell);
    // Локально — ничего в сеть.
    await new Promise((r) => setTimeout(r, 20));
    expect(setPermissionMock).not.toHaveBeenCalled();
    expect(deletePermissionMock).not.toHaveBeenCalled();

    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(setPermissionMock).toHaveBeenCalledTimes(1));
    expect(setPermissionMock).toHaveBeenCalledWith("operator", "write", {
      target_department_id: "core",
    });
  });

  it("снятие уже выданного права шлёт DELETE по «Сохранить»", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle(/desc-read Снять/)).toBeInTheDocument(),
    );
    const cell = screen.getByTitle(/desc-read Снять/) as HTMLInputElement;
    expect(cell.checked).toBe(true);
    fireEvent.click(cell);
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith("operator", "read", {
      target_department_id: "core",
    });
  });

  it("«Очистить роль» снимает права выбранной роли → DELETE по «Сохранить»", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle("desc-read")).toBeInTheDocument(),
    );
    // Единственная незалоченная роль — operator, она и выбрана в дропдауне.
    const clearBtn = screen.getAllByRole("button", {
      name: "Очистить роль",
    })[0];
    fireEvent.click(clearBtn);
    const saveButtons = screen.getAllByRole("button", { name: /Сохранить/ });
    await waitFor(() => expect(saveButtons[0]).not.toBeDisabled());
    fireEvent.click(saveButtons[0]);
    await waitFor(() => expect(deletePermissionMock).toHaveBeenCalledTimes(1));
    expect(deletePermissionMock).toHaveBeenCalledWith("operator", "read", {
      target_department_id: "core",
    });
    expect(setPermissionMock).not.toHaveBeenCalled();
  });
});
