import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

function setupApi() {
  apiGetMock.mockImplementation((path: string) => {
    if (path.includes("/permissions/catalog")) return Promise.resolve(CATALOG);
    if (path.includes("/resource-permissions/by-resource")) {
      return Promise.resolve({
        items: [
          {
            id: "rrp_1",
            resource_type: "server",
            resource_id: "srv_1",
            role: "operator",
            action: "power_reboot",
            department_id: "core",
            granted_by: "u1",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
        total: 1,
      });
    }
    // service roles (auth) — без кастомных
    return Promise.resolve([]);
  });
  apiPutMock.mockResolvedValue({});
  apiDeleteMock.mockResolvedValue(undefined);
}

function renderEditor(canEdit = true) {
  return render(
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
    </ThemeProvider>,
  );
}

describe("ResourceInstancePermissions", () => {
  beforeEach(() => {
    // Setup форсит mock-auth; редактор в mock-режиме читает локальный стор и
    // не дёргает сеть — для проверки live-связки выключаем его.
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    apiGetMock.mockReset();
    apiPutMock.mockReset();
    apiDeleteMock.mockReset();
    apiPostMock.mockReset();
    setupApi();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("показывает инстанс-грантуемые действия и прячет create / worker-callback", async () => {
    renderEditor();
    // Заголовки колонок несут title = описание действия (уникально на колонку).
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    expect(screen.getByTitle("desc-reboot")).toBeInTheDocument();
    // create (глобальное) и inventory_submit (worker_only) — колонок нет.
    expect(screen.queryByTitle("desc-create")).not.toBeInTheDocument();
    expect(screen.queryByTitle("desc-inv")).not.toBeInTheDocument();
  });

  it("toggle на пустой ячейке шлёт PUT на инстанс-эндпоинт", async () => {
    renderEditor();
    await waitFor(() =>
      expect(screen.getByTitle("desc-view")).toBeInTheDocument(),
    );
    // Первый чекбокс таблицы — guest::view (порядок ролей: guest, reader,
    // operator, admin; первое действие — view), не выдан.
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    await waitFor(() => expect(apiPutMock).toHaveBeenCalledTimes(1));
    expect(apiPutMock).toHaveBeenCalledWith(
      "/server/v1/resource-permissions/server/srv_1/guest/view",
    );
  });
});
