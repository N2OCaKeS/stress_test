import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// ── Моки API auth ──────────────────────────────────────────────────────────
const listServiceRolesMock = vi.fn();
vi.mock("@/api/auth/service_roles", () => ({
  get listServiceRoles() {
    return listServiceRolesMock;
  },
}));

const listUsersByDepartmentMock = vi.fn();
const resolveUserMock = vi.fn();
vi.mock("@/api/auth/users", () => ({
  get listUsersByDepartment() {
    return listUsersByDepartmentMock;
  },
  get resolveUser() {
    return resolveUserMock;
  },
}));

// secret_service API дёргается из других мест модуля — глушим, чтобы импорт
// SecretLive.tsx не утянул реальные сетевые враппера.
vi.mock("@/api/secret/credentials", () => ({}));
vi.mock("@/api/secret/roleAcls", () => ({
  addRoleAcl: vi.fn(),
  listRoleAcls: vi.fn(),
  revokeRoleAcl: vi.fn(),
}));
vi.mock("@/api/secret/deptGrants", () => ({
  addDeptGrant: vi.fn(),
  listDeptGrants: vi.fn(),
  revokeDeptGrant: vi.fn(),
}));
vi.mock("@/api/secret/userAcls", () => ({
  addUserAcl: vi.fn(),
  listUserAcls: vi.fn(),
  revokeUserAcl: vi.fn(),
}));

// Карта отделов нужна DeptPicker'у (cross-сценарий) и подписям.
vi.mock("@/lib/labels", () => ({
  useLabelMaps: () => ({
    depts: new Map<string, string>([
      ["dep_core", "Core"],
      ["dep_dtkk", "ДТКК"],
    ]),
    groups: new Map(),
    services: new Map(),
  }),
  useUserLabel: () => "—",
}));

import { AclModal, UserAclModal } from "@/pages/secret/SecretLive";

function role(name: string, description: string | null = null) {
  return {
    role_name: name,
    description,
    department_id: "dep_core",
    service_name: "secret_service",
    is_system: name === "admin",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function user(id: string, username: string) {
  return { id, username } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AclModal — выбор роли", () => {
  it("рендерит select ролей из listServiceRoles для department-секрета", async () => {
    listServiceRolesMock.mockResolvedValue([
      role("reader", "только чтение"),
      role("operator"),
    ]);

    render(
      <AclModal
        isCross={false}
        actorDeptId="dep_core"
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );

    // dept залочен на отдел владельца → роли грузятся сразу.
    await waitFor(() => {
      expect(listServiceRolesMock).toHaveBeenCalledWith(
        "dep_core",
        "secret_service",
      );
    });

    const select = await screen.findByLabelText("role_name");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: /reader/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /operator/ })).toBeTruthy();
  });

  it("откатывается на текстовый ввод при ошибке каталога (403)", async () => {
    listServiceRolesMock.mockRejectedValue(new Error("forbidden"));

    render(
      <AclModal
        isCross={false}
        actorDeptId="dep_core"
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );

    await waitFor(() => {
      expect(listServiceRolesMock).toHaveBeenCalled();
    });

    // select не появился — поле осталось текстовым input'ом.
    await waitFor(() => {
      expect(screen.queryByLabelText("role_name")).toBeNull();
    });
    expect(screen.getByPlaceholderText(/reader \/ operator/)).toBeTruthy();
  });

  it("откатывается на текстовый ввод при пустом каталоге", async () => {
    listServiceRolesMock.mockResolvedValue([]);

    render(
      <AclModal
        isCross={false}
        actorDeptId="dep_core"
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );

    await waitFor(() => {
      expect(listServiceRolesMock).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/reader \/ operator/)).toBeTruthy();
    });
  });

  it("сохраняет submit-контракт RoleACL (dept_id/role_name/can_read/can_write)", async () => {
    listServiceRolesMock.mockResolvedValue([role("operator"), role("reader")]);
    const onSubmit = vi.fn();

    render(
      <AclModal
        isCross={false}
        actorDeptId="dep_core"
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );

    const select = (await screen.findByLabelText("role_name")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "reader" } });
    fireEvent.click(screen.getByRole("button", { name: /Выдать/ }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        dept_id: "dep_core",
        role_name: "reader",
        can_read: true,
        can_write: false,
      });
    });
  });

  it("cross-секрет: каталог ролей грузится после выбора recipient-отдела", async () => {
    listServiceRolesMock.mockResolvedValue([role("reader")]);

    render(
      <AclModal
        isCross
        actorDeptId="dep_core"
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );

    // Пока dept не выбран — запроса ролей нет.
    expect(listServiceRolesMock).not.toHaveBeenCalled();

    const deptSelect = screen.getByRole("combobox");
    fireEvent.change(deptSelect, { target: { value: "dep_dtkk" } });

    await waitFor(() => {
      expect(listServiceRolesMock).toHaveBeenCalledWith(
        "dep_dtkk",
        "secret_service",
      );
    });
  });
});

describe("UserAclModal — выбор пользователя", () => {
  it("dep_admin-персона: пикер пользователей из listUsersByDepartment", async () => {
    listUsersByDepartmentMock.mockResolvedValue({
      items: [user("usr_1", "alice"), user("usr_2", "bob")],
      total: 2,
      totalKnown: true,
    });
    const onSubmit = vi.fn();

    render(
      <UserAclModal
        canPick
        pickerDeptId="dep_core"
        resolveDeptId="dep_core"
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );

    await waitFor(() => {
      expect(listUsersByDepartmentMock).toHaveBeenCalledWith("dep_core", {
        limit: 200,
      });
    });

    const select = (await screen.findByLabelText("пользователь")) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    fireEvent.change(select, { target: { value: "usr_2" } });
    fireEvent.click(screen.getByRole("button", { name: /Выдать/ }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        user_id: "usr_2",
        can_read: true,
        can_write: false,
      });
    });
    expect(resolveUserMock).not.toHaveBeenCalled();
  });

  it("сервис-админ (canPick=false): ручной ввод username с резолвом", async () => {
    resolveUserMock.mockResolvedValue({ user_id: "usr_9" });
    const onSubmit = vi.fn();

    render(
      <UserAclModal
        canPick={false}
        pickerDeptId="dep_core"
        resolveDeptId="dep_core"
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );

    // Без права на список юзеров — list не дёргается, поле текстовое.
    expect(listUsersByDepartmentMock).not.toHaveBeenCalled();
    const input = screen.getByPlaceholderText(/username или usr_/);
    fireEvent.change(input, { target: { value: "charlie" } });
    fireEvent.click(screen.getByRole("button", { name: /Выдать/ }));

    await waitFor(() => {
      expect(resolveUserMock).toHaveBeenCalledWith("charlie");
    });
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        user_id: "usr_9",
        can_read: true,
        can_write: false,
      });
    });
  });

  it("personal-секрет (pickerDeptId=null): ручной ввод даже у account_admin", () => {
    render(
      <UserAclModal
        canPick
        pickerDeptId={null}
        resolveDeptId="dep_core"
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );

    expect(listUsersByDepartmentMock).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText(/username или usr_/)).toBeTruthy();
  });
});
