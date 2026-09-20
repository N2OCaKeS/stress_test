import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server } from "@/api/server/types";
import type { QueueItemSummary } from "@/api/testing/types";
import { formatMskTime } from "@/lib/datetime";

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
}));
vi.mock("@/api/server/servers", () => ({
  inventorySync: vi.fn(() => Promise.resolve({ task_id: "task_1", status: "succeeded" })),
  updateServer: vi.fn(),
}));
// Карточка автозапускает inventory_sync и опрашивает его исход — не то, что
// проверяет этот файл; отдаём терминальный статус сразу, чтобы поллинг не
// продолжался фоном после сборки утверждений.
vi.mock("@/api/server/misc", () => ({
  getTask: vi.fn(() =>
    Promise.resolve({
      id: "task_1",
      kind: "server.inventory_sync",
      status: "succeeded",
      created_at: "2026-06-11T00:00:00Z",
      finished_at: "2026-06-11T00:00:01Z",
      retry_count: 0,
      result: null,
      last_error: null,
    }),
  ),
}));

const findActiveQueueItemMock = vi.fn<(serverId: string) => Promise<QueueItemSummary | null>>();
vi.mock("@/api/testing/testStands", () => ({
  findActiveQueueItemForServer: (serverId: string) => findActiveQueueItemMock(serverId),
}));

import { OverviewTab } from "@/pages/server/tabs/overview";

function baseServer(over: Partial<Server> = {}): Server {
  return {
    id: "srv_1",
    hostname: "host-01",
    display_name: "Host 01",
    number: null,
    ip_address: "10.0.0.10",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: null,
    os_last_synced_at: null,
    department_id: "dep_1",
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

/** Прогоняет несколько микротасков подряд — карточка цепляет промис за промисом
 * (inventory_sync → task-outcome poll), одного `advanceTimersByTimeAsync(0)`
 * не всегда хватает, чтобы долистать всю цепочку под fake timers. */
async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

function overviewTree(server: Server) {
  return (
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <OverviewTab serverId={server.id} server={server} />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>
  );
}

function renderOverview(server: Server) {
  return render(overviewTree(server));
}

describe("OverviewTab — оценка освобождения стенда", () => {
  beforeEach(() => {
    window.localStorage.clear();
    findActiveQueueItemMock.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("не опрашивает testing_service, когда стенд не занят тестом", async () => {
    renderOverview(baseServer({ busy_state: "free" }));
    await flush();
    expect(findActiveQueueItemMock).not.toHaveBeenCalled();
    expect(screen.getByText("estimated_finish_at")).toBeInTheDocument();
  });

  it("показывает время и живой остаток, когда есть активный item с оценкой", async () => {
    findActiveQueueItemMock.mockResolvedValue({
      queue_item_id: "qi_1",
      state: "running",
      test_id: "test_1",
      started_at: "2026-09-18T10:00:00Z",
      estimated_finish_at: "2026-09-18T10:30:00Z",
    });
    renderOverview(baseServer({ busy_state: "testing" }));

    await flush();

    expect(findActiveQueueItemMock).toHaveBeenCalledWith("srv_1");
    const expectedTime = formatMskTime("2026-09-18T10:30:00Z").slice(0, 5);
    expect(screen.getByText(expectedTime)).toBeInTheDocument();
  });

  it("показывает прочерк, когда item есть, но оценки нет (таймаут теста не задан)", async () => {
    findActiveQueueItemMock.mockResolvedValue({
      queue_item_id: "qi_1",
      state: "running",
      test_id: "test_1",
      started_at: "2026-09-18T10:00:00Z",
      estimated_finish_at: null,
    });
    renderOverview(baseServer({ busy_state: "testing" }));

    await flush();

    const row = (screen.getByText("estimated_finish_at")).parentElement;
    expect(row?.textContent).toContain("—");
  });

  it("перезапрашивает оценку по таймеру и обновляет живой остаток", async () => {
    findActiveQueueItemMock.mockResolvedValue({
      queue_item_id: "qi_1",
      state: "running",
      test_id: "test_1",
      started_at: "2026-09-18T10:00:00Z",
      estimated_finish_at: "2026-09-18T10:30:00Z",
    });
    renderOverview(baseServer({ busy_state: "testing" }));

    await flush();
    expect(findActiveQueueItemMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(findActiveQueueItemMock).toHaveBeenCalledTimes(2);
  });

  it("переход testing → testing_done: таймер и поллинг пропадают, появляются бейдж и «Подтвердить»", async () => {
    findActiveQueueItemMock.mockResolvedValue({
      queue_item_id: "qi_1",
      state: "running",
      test_id: "test_1",
      started_at: "2026-09-18T10:00:00Z",
      estimated_finish_at: "2026-09-18T10:30:00Z",
    });
    const { rerender } = renderOverview(baseServer({ busy_state: "testing" }));
    await flush();

    const expectedTime = formatMskTime("2026-09-18T10:30:00Z").slice(0, 5);
    expect(screen.getByText(expectedTime)).toBeInTheDocument();
    expect(screen.queryByText("Тестирование завершено")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
    const callsWhileTesting = findActiveQueueItemMock.mock.calls.length;

    rerender(overviewTree(baseServer({ busy_state: "testing_done" })));
    await flush();

    expect(screen.queryByText(expectedTime)).not.toBeInTheDocument();
    const row = screen.getByText("estimated_finish_at").parentElement;
    expect(row?.textContent).toContain("—");
    expect(screen.getByText("Тестирование завершено")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeInTheDocument();

    // Поллинг оценки остановлен: ни немедленного запроса, ни по таймеру.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(findActiveQueueItemMock).toHaveBeenCalledTimes(callsWhileTesting);
  });
});
