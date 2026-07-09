import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { toBase64 } from "@/lib/base64";
import type { Vm, VmAccount } from "@/api/server/vms";
import type { ServerAccount } from "@/api/server/types";
import type { EntityRef } from "@/pages/server/tabs/_entity";

// Диалоги подтверждают сразу — деструктив доходит до API без Radix-портала.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => true),
    prompt: vi.fn(async () => ({ ok: true, reason: "test" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: ReactNode }) => children,
}));

vi.mock("@/api/server/vms", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/server/vms")>();
  return {
    ...actual,
    listVmDisks: vi.fn(() =>
      Promise.resolve({
        items: [
          {
            id: "vmd_sys",
            vm_id: "vm-x1",
            name: "system",
            size_gb: 40,
            path: "/vms/vm-x1.qcow2",
            target_dev: "vda",
            serial: "vm-x1_system",
            is_system: true,
            fs: null,
            mount: "/",
            state: "ready",
          },
        ],
      }),
    ),
    listVmAccounts: vi.fn(() => Promise.resolve(VM_ACCOUNTS)),
    listVmIpPools: vi.fn(() => Promise.resolve({ items: [] })),
    getAvailableIps: vi.fn(() =>
      Promise.resolve({ pool_id: "", ips: [] }),
    ),
    astraUpdateVm: vi.fn(() =>
      Promise.resolve({ task_id: "tsk_a", status: "queued" }),
    ),
    setVmNetwork: vi.fn(() =>
      Promise.resolve({ task_id: "tsk_n", status: "queued" }),
    ),
  };
});

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(() =>
    Promise.resolve({
      items: [{ id: "osv1", name: "1.8.1.6", repositories: [], discovered_at: "x" }],
      total: 1,
      limit: 200,
      offset: 0,
    }),
  ),
}));

vi.mock("@/api/server/accounts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/server/accounts")>();
  return {
    ...actual,
    getAccount: vi.fn(() =>
      Promise.resolve({ ...FULL_ACCOUNT, password_b64: toBase64("s3cret") }),
    ),
    listAccounts: vi.fn(() =>
      Promise.resolve({ items: [FULL_ACCOUNT, NEW_ACCOUNT], total: 2, limit: 200, offset: 0 }),
    ),
    bindAccountVms: vi.fn(() =>
      Promise.resolve({ account_id: "acc-new", login: "newacc", vm_ids: ["vm-x1"] }),
    ),
    unbindAccountVm: vi.fn(() =>
      Promise.resolve({ account_id: "acc-ci", login: "ci-runner", vm_ids: [] }),
    ),
    provisionAccountOnVm: vi.fn(() =>
      Promise.resolve({ operation: "provision", vm_id: "vm-x1", task_id: "t", status: "queued" }),
    ),
    updateAccountOnVm: vi.fn(() =>
      Promise.resolve({ operation: "update", vm_id: "vm-x1", task_id: "t", status: "queued" }),
    ),
    rotateAccountUserInitiated: vi.fn(() =>
      Promise.resolve({ id: "acc-ci", login: "ci-runner", rotated_at: "x" }),
    ),
  };
});

// Поллинг исхода задачи не должен ходить в сеть.
vi.mock("@/api/server/misc", () => ({
  getTask: vi.fn(() => new Promise(() => {})),
}));

import { OverviewTab } from "@/pages/server/tabs/overview";
import { HardwareTab } from "@/pages/server/tabs/hardware";
import { ManageTab } from "@/pages/server/tabs/manage";
import { AccountsTab } from "@/pages/server/tabs/accounts";
import { listVmDisks, astraUpdateVm, setVmNetwork } from "@/api/server/vms";
import * as accountsApi from "@/api/server/accounts";

const VM: Vm = {
  id: "vm-x1",
  name: "parity-vm",
  hostname: "parity-host",
  number: 7,
  hub_server_id: "srv-07",
  department_id: "core",
  os_version: "1.8.1.6",
  box: "vm_station",
  network_mode: "bridge",
  ip_address: "10.10.0.9",
  status: "free",
  power_state: "on",
  cpu: 4,
  ram_mb: 8192,
  disk_gb: 40,
  autostart: true,
  cred_strategy: "per_snapshot",
  busy_state: "free",
  busy_note: null,
  ping_reachable: true,
  ping_latency_ms: 0.7,
  ping_checked_at: "2026-07-05T00:00:00Z",
  ssh_reachable: true,
  ssh_checked_at: "2026-07-05T00:00:00Z",
  last_error: null,
  is_managed: true,
  mgmt_user: "dbosmgr",
  mgmt_ssh_public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAImock dbosmgr@vm-x1",
  mgmt_creds_rotated_at: "2026-07-05T00:00:00Z",
  mgmt_creds_pending_apply: false,
  network_interfaces: ["eth0"],
  nics: [
    {
      name: "eth0",
      model: "virtio",
      network_mode: "bridge",
      bridge: "br0",
      mac: null,
      ip_address: "10.10.0.9",
    },
  ],
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  created_by: null,
};

const FULL_ACCOUNT: ServerAccount = {
  id: "acc-ci",
  server_ids: [],
  department_id: "core",
  login: "ci-runner",
  source: "managed",
  has_sudo: true,
  unix_groups: ["ci"],
  linked_user_id: null,
  shell: "/bin/bash",
  home_dir: "/home/ci-runner",
  is_active: true,
  password_rotated_at: "2026-06-01T00:00:00Z",
  password_b64: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  created_by: null,
};

const NEW_ACCOUNT: ServerAccount = {
  ...FULL_ACCOUNT,
  id: "acc-new",
  login: "newacc",
  has_sudo: false,
  unix_groups: [],
};

const VM_ACCOUNTS: VmAccount[] = [
  {
    account_id: "acc-ci",
    login: "ci-runner",
    has_sudo: true,
    unix_groups: ["ci"],
    ssh_public_key: null,
    present_on_vm: true,
  },
];

function vmEntity(): EntityRef {
  return { kind: "vm", vm: VM, mock: false, canManage: true, onChanged: vi.fn() };
}

function renderTab(node: ReactNode) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>{node}</MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Паритет вкладок ВМ с серверными (live-режим)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("Обзор ВМ рендерит те же 4 секции, что у сервера", () => {
    renderTab(<OverviewTab entity={vmEntity()} />);
    for (const name of [
      /Идентификация/,
      /^Состояние$/,
      /Сеть и OS/,
      /Метки времени/,
    ]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    // Идентификация несёт hostname; состояние — mgmt/last_error.
    expect(screen.getByText("parity-host")).toBeInTheDocument();
    expect(screen.getByText("dbosmgr")).toBeInTheDocument();
  });

  it("Железо ВМ показывает NIC-детализацию и таблицу дисков", async () => {
    renderTab(<HardwareTab serverId="" entity={vmEntity()} />);
    // NIC-таблица из vm.nics.
    expect(
      screen.getByRole("heading", { name: /Сетевые интерфейсы/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("virtio").length).toBeGreaterThan(0);
    expect(screen.getAllByText("br0").length).toBeGreaterThan(0);
    // Таблица дисков — read-only из listVmDisks.
    await waitFor(() => expect(listVmDisks).toHaveBeenCalledWith("vm-x1"));
    expect(
      await screen.findByRole("heading", { name: /^Диски/ }),
    ).toBeInTheDocument();
    expect(await screen.findByText("vda")).toBeInTheDocument();
  });

  it("Управление ВМ несёт astra-update, смену сети и mgmt-креды", async () => {
    renderTab(<ManageTab serverId="" entity={vmEntity()} />);
    expect(
      await screen.findByRole("heading", { name: /Обновление ОС Astra/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Смена сети/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Подготовка и управляющие креды/ }),
    ).toBeInTheDocument();
    // mgmt-креды показывают учётку.
    expect(screen.getByText("dbosmgr")).toBeInTheDocument();

    // astra-update: выбрать версию → «Обновить ОС».
    const select = await screen.findByRole("combobox", {
      name: /Целевая версия ОС/,
    });
    fireEvent.change(select, { target: { value: "osv1" } });
    fireEvent.click(screen.getByRole("button", { name: /Обновить ОС/ }));
    await waitFor(() =>
      expect(astraUpdateVm).toHaveBeenCalledWith("vm-x1", { rc: "1.8.1.6" }),
    );
  });

  it("Управление ВМ: смена сети на nat уходит в setVmNetwork", async () => {
    renderTab(<ManageTab serverId="" entity={vmEntity()} />);
    const modeSelect = await screen.findByRole("combobox", {
      name: /Сетевой режим/,
    });
    fireEvent.change(modeSelect, { target: { value: "nat" } });
    fireEvent.click(screen.getByRole("button", { name: /Сменить сеть/ }));
    await waitFor(() =>
      expect(setVmNetwork).toHaveBeenCalledWith("vm-x1", {
        network_mode: "nat",
        ip_address: null,
        pool_id: null,
      }),
    );
  });

  async function openVmAccount() {
    renderTab(<AccountsTab serverId="" entity={vmEntity()} />);
    fireEvent.click(await screen.findByText("ci-runner"));
    // Детали подтягивают полную карточку (для reveal).
    await waitFor(() =>
      expect(accountsApi.getAccount).toHaveBeenCalledWith("acc-ci"),
    );
  }

  it("Аккаунты ВМ: provision в госте уходит в provisionAccountOnVm", async () => {
    await openVmAccount();
    fireEvent.click(await screen.findByRole("button", { name: /^Provision$/ }));
    await waitFor(() =>
      expect(accountsApi.provisionAccountOnVm).toHaveBeenCalledWith("acc-ci", "vm-x1"),
    );
  });

  it("Аккаунты ВМ: update on host уходит в updateAccountOnVm", async () => {
    await openVmAccount();
    fireEvent.click(await screen.findByRole("button", { name: /Update on host/ }));
    await waitFor(() =>
      expect(accountsApi.updateAccountOnVm).toHaveBeenCalledWith("acc-ci", "vm-x1"),
    );
  });

  it("Аккаунты ВМ: ротация пароля уходит в rotateAccountUserInitiated", async () => {
    await openVmAccount();
    fireEvent.click(await screen.findByRole("button", { name: /Rotate \(sync/ }));
    await waitFor(() =>
      expect(accountsApi.rotateAccountUserInitiated).toHaveBeenCalledWith("acc-ci"),
    );
  });

  it("Аккаунты ВМ: reveal пароля раскрывает plaintext через getAccount", async () => {
    await openVmAccount();
    fireEvent.click(await screen.findByRole("button", { name: /Показать/ }));
    await waitFor(() => expect(screen.getByText("s3cret")).toBeInTheDocument());
  });

  it("Аккаунты ВМ: unbind отвязывает от ВМ с deprovision", async () => {
    await openVmAccount();
    fireEvent.click(await screen.findByRole("button", { name: /Unbind/ }));
    await waitFor(() =>
      expect(accountsApi.unbindAccountVm).toHaveBeenCalledWith("acc-ci", "vm-x1", {
        deprovision: true,
      }),
    );
  });

  it("Аккаунты ВМ: привязка существующей учётки уходит в bindAccountVms", async () => {
    renderTab(<AccountsTab serverId="" entity={vmEntity()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Привязать существующую/ }));
    // В модалке кандидат — не привязанный newacc.
    fireEvent.click(await screen.findByText("newacc"));
    fireEvent.click(screen.getByRole("button", { name: /^Привязать$/ }));
    await waitFor(() =>
      expect(accountsApi.bindAccountVms).toHaveBeenCalledWith(
        "acc-new",
        { vm_ids: ["vm-x1"] },
        { provision: true },
      ),
    );
  });
});
