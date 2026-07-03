import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";

// Глобальный список отделов auth_service отдаёт только account_admin; остальным
// ролям — 403. LabelsProvider монтируется на всё приложение и раньше дёргал
// /departments на первой загрузке любой персоной (в т.ч. на /server), засоряя
// сетевой трейс. Здесь проверяем гейт: запрос уходит только у account_admin.

const listDepartmentsMock = vi.fn();
const listGroupsMock = vi.fn();
const listServicesMock = vi.fn();

vi.mock("@/api/auth/departments", () => ({
  listDepartments: (...args: unknown[]) => listDepartmentsMock(...args),
}));
vi.mock("@/api/auth/groups", () => ({
  listGroups: (...args: unknown[]) => listGroupsMock(...args),
}));
vi.mock("@/api/auth/services", () => ({
  listServices: (...args: unknown[]) => listServicesMock(...args),
}));

interface MockUser {
  platform_role: string | null;
  department_id: string | null;
  department_name: string | null;
}

let mockUser: MockUser | null = null;

// USE_MOCK_AUTH=false заставляет провайдер реально «ходить в сеть» (моки выше),
// а не фоллбэчиться на mock-режим. useAuthOptional отдаёт нужную персону.
vi.mock("@/contexts/AuthContext", () => ({
  USE_MOCK_AUTH: false,
  useAuthOptional: () => (mockUser ? { user: mockUser } : null),
}));

import { LabelsProvider } from "@/lib/labels";

function renderProvider() {
  return render(
    <LabelsProvider>
      <div />
    </LabelsProvider>,
  );
}

describe("LabelsProvider — гейт /departments по роли", () => {
  beforeEach(() => {
    listDepartmentsMock.mockReset().mockResolvedValue([]);
    listGroupsMock.mockReset().mockResolvedValue([]);
    listServicesMock.mockReset().mockResolvedValue([]);
    mockUser = null;
  });

  it("account_admin: глобальный список отделов запрашивается", async () => {
    mockUser = {
      platform_role: "account_admin",
      department_id: null,
      department_name: null,
    };
    renderProvider();
    await waitFor(() => expect(listServicesMock).toHaveBeenCalled());
    expect(listDepartmentsMock).toHaveBeenCalled();
  });

  it("dep_admin: /departments не дёргается (backend 403)", async () => {
    mockUser = {
      platform_role: "dep_admin",
      department_id: "core",
      department_name: "Core",
    };
    renderProvider();
    // Группы/сервисы дёргаются в любом случае — ждём их как маркер mount'а.
    await waitFor(() => expect(listServicesMock).toHaveBeenCalled());
    expect(listGroupsMock).toHaveBeenCalled();
    expect(listDepartmentsMock).not.toHaveBeenCalled();
  });

  it("обычный оператор без платформенной роли: /departments не дёргается", async () => {
    mockUser = {
      platform_role: null,
      department_id: "core",
      department_name: "Core",
    };
    renderProvider();
    await waitFor(() => expect(listServicesMock).toHaveBeenCalled());
    expect(listDepartmentsMock).not.toHaveBeenCalled();
  });
});
