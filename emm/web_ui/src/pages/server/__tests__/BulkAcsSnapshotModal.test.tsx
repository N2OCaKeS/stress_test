import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type { Server } from "@/api/server/types";
import { BulkAcsSnapshotModal } from "@/pages/server/_bulkAcsSnapshotModal";

const listOsVersionsMock = vi.fn();
const listAcsSnapshotsMock = vi.fn();

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...args: unknown[]) => listOsVersionsMock(...args),
}));
vi.mock("@/api/server/acsSnapshots", () => ({
  listAcsSnapshots: (...args: unknown[]) => listAcsSnapshotsMock(...args),
  createAcsSnapshotsBatch: vi.fn(),
  restoreAcsSnapshotsBatch: vi.fn(),
}));

// Пересечение снимков считается по нескольким последовательным Promise.all +
// ре-рендерам (versionsQ, затем restoreCheckQ по каждому серверу) — дефолтный
// таймаут findByRole/waitFor (1с) иногда не укладывается в jsdom, берём с запасом.
const LONG_WAIT = { timeout: 3000 };

function makeServer(id: string, overrides: Partial<Server> = {}): Server {
  return {
    id,
    hostname: `${id}-host`,
    display_name: null,
    number: null,
    ip_address: "10.0.0.1",
    mgmt_ip_address: null,
    ssh_port: 22,
    os_version_id: null,
    os_last_synced_at: null,
    department_id: "dep_a",
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
    is_vms_hub: false,
    ...overrides,
  } as Server;
}

const OS_VERSIONS = [
  { id: "osv_a", name: "1711rc42", description: null, repositories: [], kernels: [], is_urgent_update: false, discovered_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" },
  { id: "osv_b", name: "1711rc43", description: null, repositories: [], kernels: [], is_urgent_update: false, discovered_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" },
  { id: "osv_c", name: "1710rc99", description: null, repositories: [], kernels: [], is_urgent_update: false, discovered_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" },
];

beforeEach(() => {
  vi.clearAllMocks();
  listOsVersionsMock.mockResolvedValue({ items: OS_VERSIONS, total: 3, limit: 200, offset: 0 });
});

function openRestoreTab() {
  fireEvent.click(screen.getByRole("button", { name: /^Восстановить$/ }));
}

describe("BulkAcsSnapshotModal — restore version intersection", () => {
  it("создание снимка (create) показывает полный каталог версий без фильтра по существующим снимкам", async () => {
    render(
      <BulkAcsSnapshotModal
        servers={[makeServer("srv_1"), makeServer("srv_2")]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /— выберите версию —/ }, LONG_WAIT));
    expect(await screen.findByRole("option", { name: "1711rc42" }, LONG_WAIT)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1711rc43" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1710rc99" })).toBeInTheDocument();
    expect(listAcsSnapshotsMock).not.toHaveBeenCalled();
  });

  it("восстановление (restore) сужает список до пересечения снимков на всех серверах", async () => {
    listAcsSnapshotsMock.mockImplementation((serverId: string) => {
      if (serverId === "srv_1") {
        return Promise.resolve({
          snapshots: [
            { name: "srv_1-1711rc42", version_name: "1711rc42" },
            { name: "srv_1-1711rc43", version_name: "1711rc43" },
          ],
        });
      }
      return Promise.resolve({
        snapshots: [{ name: "srv_2-1711rc42", version_name: "1711rc42" }],
      });
    });

    render(
      <BulkAcsSnapshotModal
        servers={[makeServer("srv_1"), makeServer("srv_2")]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    openRestoreTab();

    // Ждём финальное состояние дропдауна напрямую (одна опция), а не
    // промежуточный факт вызова мока — так тест не гонится с реальным
    // разрешением Promise.all внутри useQuery.
    fireEvent.click(await screen.findByRole("button", { name: /— выберите версию —/ }, LONG_WAIT));
    expect(await screen.findByRole("option", { name: "1711rc42" }, LONG_WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "1711rc43" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "1710rc99" })).not.toBeInTheDocument();
  });

  it("пустое пересечение показывает предупреждение вместо списка", async () => {
    listAcsSnapshotsMock.mockImplementation((serverId: string) =>
      Promise.resolve({
        snapshots: [
          { name: `${serverId}-only`, version_name: serverId === "srv_1" ? "1711rc42" : "1710rc99" },
        ],
      }),
    );

    render(
      <BulkAcsSnapshotModal
        servers={[makeServer("srv_1"), makeServer("srv_2")]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    openRestoreTab();

    expect(
      await screen.findByText(/Нет версии, снимок которой есть сразу на всех/, {}, LONG_WAIT),
    ).toBeInTheDocument();
  });

  it("сбой проверки снимков на части серверов — fail-open на полный каталог с предупреждением", async () => {
    listAcsSnapshotsMock.mockImplementation((serverId: string) =>
      serverId === "srv_1"
        ? Promise.resolve({ snapshots: [{ name: "srv_1-1711rc42", version_name: "1711rc42" }] })
        : Promise.reject(new Error("network")),
    );

    render(
      <BulkAcsSnapshotModal
        servers={[makeServer("srv_1"), makeServer("srv_2")]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    openRestoreTab();

    expect(
      await screen.findByText(/Не удалось проверить снимки на части серверов/, {}, LONG_WAIT),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /— выберите версию —/ }));
    expect(await screen.findByRole("option", { name: "1711rc42" }, LONG_WAIT)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1710rc99" })).toBeInTheDocument();
  });

  it("смена выбора серверов сбрасывает уже выбранную версию, если она выпала из пересечения", async () => {
    listAcsSnapshotsMock.mockResolvedValue({
      snapshots: [
        { name: "x-1711rc42", version_name: "1711rc42" },
        { name: "x-1711rc43", version_name: "1711rc43" },
      ],
    });

    render(
      <BulkAcsSnapshotModal
        servers={[makeServer("srv_1"), makeServer("srv_2", { is_vms_hub: true })]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    openRestoreTab();

    fireEvent.click(await screen.findByRole("button", { name: /— выберите версию —/ }, LONG_WAIT));
    fireEvent.click(await screen.findByRole("option", { name: "1711rc43" }, LONG_WAIT));
    expect(await screen.findByRole("button", { name: "1711rc43" }, LONG_WAIT)).toBeInTheDocument();

    // Снимаем чекбокс «кроме VMS-hub» — второй сервер (без снимка 1711rc43,
    // но с 1711rc42) входит в цель: пересечение сужается до {1711rc42} —
    // непустое, но уже без выбранной ранее 1711rc43, версия должна сброситься.
    listAcsSnapshotsMock.mockImplementation((serverId: string) =>
      serverId === "srv_2"
        ? Promise.resolve({ snapshots: [{ name: "y-1711rc42", version_name: "1711rc42" }] })
        : Promise.resolve({
            snapshots: [
              { name: "x-1711rc42", version_name: "1711rc42" },
              { name: "x-1711rc43", version_name: "1711rc43" },
            ],
          }),
    );
    fireEvent.click(screen.getByRole("checkbox"));

    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: /— выберите версию —/ }),
        ).toBeInTheDocument(),
      LONG_WAIT,
    );
  });
});
