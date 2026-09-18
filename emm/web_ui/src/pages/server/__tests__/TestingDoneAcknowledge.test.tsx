import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Server } from "@/api/server/types";

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({ items: [], total: 0, limit: 200, offset: 0 }),
  ),
}));

const acknowledgeTestingDoneMock = vi.fn<(id: string) => Promise<Server>>();
vi.mock("@/api/server/servers", () => ({
  inventorySync: vi.fn(() => Promise.resolve({ task_id: "task_1", status: "succeeded" })),
  updateServer: vi.fn(),
  acknowledgeTestingDone: (id: string) => acknowledgeTestingDoneMock(id),
}));

vi.mock("@/api/testing/testStands", () => ({
  findActiveQueueItemForServer: vi.fn(() => Promise.resolve(null)),
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

/** Рендерит вкладку под персоной без прав администратора (dave —
 * logging_reader, has_admin=false) — кнопка подтверждения не должна зависеть
 * от роли, поэтому берём заведомо не-админскую персону, а не дефолтную. */
function renderAsNonAdmin(server: Server) {
  window.localStorage.setItem("dbos-persona", "dave");
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <OverviewTab serverId={server.id} server={server} />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

/** То же самое, но держит `server` в состоянии и поднимает свежую карточку
 * через `onServerUpdated`, как реальный `ServerDetail` — иначе после
 * успешного acknowledge пропа осталась бы прежней и бейдж не пропал бы. */
function renderStatefulAsNonAdmin(initial: Server) {
  window.localStorage.setItem("dbos-persona", "dave");

  function Harness() {
    const [server, setServer] = useState(initial);
    return <OverviewTab serverId={server.id} server={server} onServerUpdated={setServer} />;
  }

  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <Harness />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("OverviewTab — «Тестирование завершено»", () => {
  beforeEach(() => {
    window.localStorage.clear();
    acknowledgeTestingDoneMock.mockReset();
  });

  it("не показывает бейдж/кнопку, пока сервер просто free", () => {
    renderAsNonAdmin(baseServer({ busy_state: "free" }));
    expect(screen.queryByText("Тестирование завершено")).not.toBeInTheDocument();
    expect(screen.queryByText("Подтвердить")).not.toBeInTheDocument();
  });

  it("показывает бейдж и кнопку «Подтвердить» не-админу, когда busy_state=testing_done", () => {
    renderAsNonAdmin(
      baseServer({
        busy_state: "testing_done",
        busy_actor_type: "service",
        busy_service_name: "testing_service",
      }),
    );
    expect(screen.getByText("Тестирование завершено")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeInTheDocument();
  });

  it("клик по «Подтвердить» зовёт acknowledge-testing-done и убирает бейдж", async () => {
    const freed = baseServer({ busy_state: "free" });
    acknowledgeTestingDoneMock.mockResolvedValue(freed);

    renderStatefulAsNonAdmin(baseServer({ busy_state: "testing_done" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    await waitFor(() => expect(acknowledgeTestingDoneMock).toHaveBeenCalledWith("srv_1"));
    await waitFor(() =>
      expect(screen.queryByText("Тестирование завершено")).not.toBeInTheDocument(),
    );
  });
});
