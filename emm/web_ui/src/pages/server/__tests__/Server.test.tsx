import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server as ServerType } from "@/api/server/types";

// Диалоговый провайдер монтируется в App.tsx; в smoke-рендере страницы он не
// нужен — мокаем хук no-op'ом, чтобы не тащить Radix-портал в jsdom.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => false),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// Сетевые вызовы страницы списка моки́м — нам важен только smoke-рендер
// (заголовок панели, плейсхолдер поиска, loading state списка).
vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => new Promise(() => {})),
  getServer: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
}));

import { listDepartments } from "@/api/auth/departments";
import { listServers } from "@/api/server/servers";
import { Server } from "@/pages/server/Server";

function mkServer(over: Partial<ServerType> & { id: string }): ServerType {
  return {
    hostname: over.id,
    display_name: null,
    ip_address: "10.10.20.11",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: null,
    os_last_synced_at: null,
    department_id: "core",
    status: "online",
    power_state: "on",
    busy_state: "free",
    busy_user_id: null,
    busy_since: null,
    busy_note: null,
    serial_number: null,
    asset_tag: null,
    location: null,
    cpu_brand: null,
    cpu_model: null,
    cpu_cores: null,
    cpu_threads: null,
    cpu_frequency_ghz: null,
    ram_total_mb: null,
    network_interface_name: null,
    decommissioned_at: null,
    is_managed: false,
    management_user: null,
    prepared_at: null,
    storage: [],
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
    created_by: null,
    ...over,
  };
}

function renderServer(entry = "/servers") {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[entry]}>
            <Server />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Server (list page) smoke", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("рендерится без ошибок и показывает loading state списка", () => {
    renderServer();
    // Aside-плейсхолдер поиска присутствует.
    expect(
      screen.getByPlaceholderText(/Поиск по 0 серверам/),
    ).toBeInTheDocument();
    // listServers ещё в pending — должен отрисоваться индикатор «Загрузка…».
    expect(screen.getByText(/Загрузка…/)).toBeInTheDocument();
    // EmptyPane workzone'ы (id не выбран, action не задан).
    expect(
      screen.getByText(/Выберите сервер слева для просмотра деталей/),
    ).toBeInTheDocument();
  });

  it("создание для dep_admin: отдел read-only и listDepartments не дёргается", () => {
    // Дефолтная persona — alice (dep_admin, dept core). listDepartments ей
    // отдаёт 403, поэтому запрос не должен уходить, а поле отдела — фиксировано.
    renderServer("/servers?action=new");
    expect(screen.getByText("Создание сервера")).toBeInTheDocument();
    // Поле отдела — read-only input с id отдела, а не выпадающий список.
    const deptInput = screen.getByDisplayValue("core");
    expect(deptInput).toHaveAttribute("readonly");
    expect(
      screen.getByText(/сервер создаётся в вашем отделе/),
    ).toBeInTheDocument();
    expect(listDepartments).not.toHaveBeenCalled();
  });

  it("строка списка показывает ping-индикатор доступности", async () => {
    vi.mocked(listServers).mockResolvedValueOnce({
      items: [
        mkServer({ id: "srv_up", ping_reachable: true, ping_latency_ms: 12.3 }),
        mkServer({ id: "srv_down", ping_reachable: false }),
        mkServer({ id: "srv_unknown", ping_reachable: null }),
      ],
      total: 3,
      limit: 200,
      offset: 0,
    });
    renderServer();
    // Доступный бокс — latency-бейдж; недоступный — «недоступен»; без пробы — «—».
    expect(await screen.findByText("12.3 мс")).toBeInTheDocument();
    expect(screen.getByText("недоступен")).toBeInTheDocument();
    expect(
      screen.getByTitle("ping: не проверялось"),
    ).toBeInTheDocument();
  });

  it("строка списка: ровно ping + busy, без status-бейджа", async () => {
    vi.mocked(listServers).mockResolvedValueOnce({
      items: [
        mkServer({
          id: "srv_row",
          status: "maintenance",
          busy_state: "free",
          ping_reachable: true,
          ping_latency_ms: 7,
        }),
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    renderServer();
    // ping-бейдж (latency) и busy-чип (free) присутствуют.
    expect(await screen.findByText("7 мс")).toBeInTheDocument();
    expect(screen.getByText("free")).toBeInTheDocument();
    // Средний status-бейдж убран: его метка «maint» больше не рендерится
    // (значение статуса «maintenance» осталось только в option фильтра).
    expect(screen.queryByText("maint")).not.toBeInTheDocument();
  });
});
