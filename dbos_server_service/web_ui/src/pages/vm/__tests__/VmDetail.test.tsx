import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Vm as VmType } from "@/api/server/vms";

// Диалоги подтверждают сразу (confirm=true), чтобы деструктивные операции
// доходили до вызова API без Radix-портала в jsdom.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: true, reason: "test" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// Живой режим: реальные обёртки заменяем шпионами только для тех вызовов,
// которые дёргаются в тесте. Остальные экспорты остаются настоящими и не
// вызываются.
vi.mock("@/api/server/vms", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/server/vms")>();
  return {
    ...actual,
    vmPower: vi.fn(() => Promise.resolve({ task_id: "t1", status: "queued" })),
    listVmDisks: vi.fn(() => Promise.resolve({ items: [] })),
    listVmSnapshots: vi.fn(() => Promise.resolve({ items: [] })),
  };
});

// Поллинг исхода задачи не должен ходить в сеть — держим getTask в пендинге.
vi.mock("@/api/server/misc", () => ({
  getTask: vi.fn(() => new Promise(() => {})),
}));

import { VmDetail } from "@/pages/vm/Vm";
import { vmPower, listVmDisks, listVmSnapshots } from "@/api/server/vms";

const MOCK_VM: VmType = {
  id: "vm-x1",
  name: "detail-vm",
  number: 42,
  hub_server_id: "srv-07",
  department_id: "core",
  os_version: "1.8.1.6",
  box: "vm_station",
  network_mode: "bridge",
  ip_address: "10.10.0.5",
  status: "free",
  power_state: "off",
  cpu: 2,
  ram_mb: 4096,
  disk_gb: 40,
  autostart: false,
  cred_strategy: "per_snapshot",
  busy_state: "free",
  busy_note: null,
  is_managed: true,
  mgmt_user: "dbosmgr",
  mgmt_creds_rotated_at: "2026-06-01T00:00:00Z",
  mgmt_creds_pending_apply: false,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  created_by: null,
};

function renderDetail(canManage = true) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <VmDetail
              vm={MOCK_VM}
              mock={false}
              canManage={canManage}
              onBack={() => {}}
              onChanged={() => {}}
            />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

async function openTab(label: string) {
  fireEvent.click(await screen.findByRole("button", { name: label }));
}

describe("VmDetail (tabbed, live mode)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("рендерит таб-бар со всеми вкладками для управляющей роли", async () => {
    renderDetail(true);
    for (const label of [
      "Обзор",
      "Питание",
      "Снимки",
      "Диски",
      "Сеть",
      "Обслуживание",
    ]) {
      expect(
        await screen.findByRole("button", { name: label }),
      ).toBeInTheDocument();
    }
    // Активна «Обзор» — виден блок «Параметры».
    expect(
      screen.getByRole("heading", { name: /Параметры/ }),
    ).toBeInTheDocument();
  });

  it("для роли без управления прячет вкладки питание/сеть/обслуживание", async () => {
    renderDetail(false);
    // Доступны на чтение.
    expect(await screen.findByRole("button", { name: "Обзор" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Снимки" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Диски" })).toBeInTheDocument();
    // Управляющих вкладок нет.
    expect(screen.queryByRole("button", { name: "Питание" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сеть" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Обслуживание" }),
    ).not.toBeInTheDocument();
  });

  it("переключение на «Диски» монтирует секцию и грузит диски", async () => {
    renderDetail(true);
    await openTab("Диски");
    expect(
      await screen.findByRole("heading", { name: /Диски/ }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(listVmDisks).toHaveBeenCalledWith(MOCK_VM.id),
    );
  });

  it("переключение на «Снимки» монтирует секцию и грузит снимки", async () => {
    renderDetail(true);
    await openTab("Снимки");
    expect(
      await screen.findByRole("heading", { name: /^Снимки$/ }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(listVmSnapshots).toHaveBeenCalledWith(MOCK_VM.id),
    );
  });

  it("вкладка «Питание»: Start вызывает vmPower(id, 'start')", async () => {
    renderDetail(true);
    await openTab("Питание");
    const start = await screen.findByRole("button", { name: /Start/ });
    fireEvent.click(start);
    await waitFor(() =>
      expect(vmPower).toHaveBeenCalledWith(MOCK_VM.id, "start"),
    );
  });

  it("вкладка «Сеть» показывает секцию сети", async () => {
    renderDetail(true);
    await openTab("Сеть");
    expect(
      await screen.findByRole("heading", { name: /^Сеть$/ }),
    ).toBeInTheDocument();
  });
});
