/**
 * Mock-данные VM-зоны для dev/mock-режима (`VITE_USE_MOCK_AUTH=true`).
 *
 * Пока сервис не отдаёт `/vms`
 * и `/servers/{id}/prepare-vms-hub`, страница `/vm` и VM-группа в списке
 * серверов рендерятся на этих данных. Живой режим (`false`) ходит в
 * `@/api/server/vms`.
 */

import type {
  Vm,
  VmConsoleKind,
  VmConsoleResponse,
  VmDisk,
  VmHub,
  VmImage,
  VmIpPool,
  VmPreset,
  VmSnapshot,
} from "@/api/server/vms";
import type { ServerAccount } from "@/api/server/types";

/**
 * Кандидаты в VMS-hub — серверы, ещё НЕ подготовленные под виртуализацию.
 * Для них в UI показывается кнопка «Подготовить как VMS-hub».
 */
export interface MockHubCandidate {
  id: string;
  hostname: string;
  display_name: string | null;
  ip_address: string;
  department_id: string;
}

export const MOCK_VM_HUBS: VmHub[] = [
  {
    id: "srv-07",
    hostname: "srv-node-07",
    display_name: "kvm-hub-core-1",
    ip_address: "10.177.103.207",
    department_id: "core",
    vm_count: 3,
  },
  {
    id: "srv-24",
    hostname: "srv-node-24",
    display_name: "kvm-hub-dtkk-1",
    ip_address: "10.177.101.44",
    department_id: "dtkk",
    vm_count: 1,
  },
];

export const MOCK_HUB_CANDIDATES: MockHubCandidate[] = [
  {
    id: "srv-31",
    hostname: "srv-node-31",
    display_name: null,
    ip_address: "10.177.100.51",
    department_id: "core",
  },
  {
    id: "srv-42",
    hostname: "srv-node-42",
    display_name: "spare-42",
    ip_address: "10.177.102.62",
    department_id: "infra",
  },
];

const NOW = "2026-07-07T09:00:00Z";

export const MOCK_VMS: Vm[] = [
  {
    id: "vm-101",
    name: "alse-1.8-rc",
    number: 101,
    hub_server_id: "srv-07",
    department_id: "core",
    os_version: "1.8.1.6",
    box: "vm_station",
    network_mode: "bridge",
    ip_address: "10.177.103.51",
    status: "free",
    power_state: "on",
    cpu: 4,
    ram_mb: 8192,
    disk_gb: 80,
    autostart: true,
    cred_strategy: "per_snapshot",
    busy_state: "free",
    busy_since: null,
    ping_reachable: true,
    ping_latency_ms: 0.6,
    is_managed: true,
    mgmt_user: "dbosmgr",
    mgmt_creds_rotated_at: "2026-07-05T11:20:00Z",
    mgmt_creds_pending_apply: false,
    created_at: NOW,
    updated_at: NOW,
    created_by: "alice",
  },
  {
    id: "vm-102",
    name: "alse-1.7-regress",
    number: 102,
    hub_server_id: "srv-07",
    department_id: "core",
    os_version: "1.7.5.9",
    box: "vm_station",
    network_mode: "nat",
    ip_address: "192.168.122.44",
    status: "run test",
    power_state: "on",
    cpu: 2,
    ram_mb: 4096,
    disk_gb: 40,
    autostart: false,
    cred_strategy: "per_snapshot",
    busy_state: "testing",
    busy_since: NOW,
    busy_note: "regress-цикл RC",
    busy_user_id: "alice",
    ping_reachable: true,
    ping_latency_ms: 1.2,
    created_at: NOW,
    updated_at: NOW,
    created_by: "alice",
  },
  {
    id: "vm-103",
    name: "xfs-memleak",
    number: 103,
    hub_server_id: "srv-07",
    department_id: "core",
    os_version: "1.8.1.6",
    box: "xfs.15GB",
    network_mode: "bridge",
    ip_address: "10.177.103.52",
    status: "free",
    power_state: "off",
    cpu: 2,
    ram_mb: 2048,
    disk_gb: 15,
    autostart: false,
    cred_strategy: "reroll",
    busy_state: "free",
    busy_since: null,
    ping_reachable: false,
    ping_latency_ms: null,
    is_managed: false,
    mgmt_user: null,
    mgmt_creds_rotated_at: null,
    mgmt_creds_pending_apply: false,
    created_at: NOW,
    updated_at: NOW,
    created_by: "alice",
  },
  {
    id: "vm-201",
    name: "dtkk-sandbox",
    number: 201,
    hub_server_id: "srv-24",
    department_id: "dtkk",
    os_version: "1.8.1.6",
    box: "vm_station",
    network_mode: "nat",
    ip_address: "192.168.122.10",
    status: "debug test",
    power_state: "on",
    cpu: 8,
    ram_mb: 16384,
    disk_gb: 120,
    autostart: true,
    cred_strategy: "per_snapshot",
    busy_state: "busy",
    busy_since: NOW,
    busy_note: "ручной debug",
    busy_user_id: "bob",
    ping_reachable: true,
    ping_latency_ms: 0.9,
    created_at: NOW,
    updated_at: NOW,
    created_by: "bob",
  },
];

/** Стандартные боксы каталога — для дропдауна в модалке создания. */
export const MOCK_VM_BOXES: string[] = [
  "vm_station",
  "1.8.1.o",
  "xfs.15GB",
  "15GB.single",
];

/**
 * Каталог образов `vm_images` — mock-фолбэк для модалки создания ВМ в
 * mock-режиме (живой режим ходит в `GET /vm-images`).
 */
export const MOCK_VM_IMAGES: VmImage[] = [
  {
    name: "vm_station",
    kind: "universal",
    description: "Universal-станция (обе ОС в снимках)",
    os_versions: ["1.7.5.9", "1.8.1.6"],
    min_disk_gb: 30,
    size_bytes: 21_000_000_000,
  },
  {
    name: "1.8.1.o",
    kind: "single",
    description: "Astra 1.8.1 Орёл",
    min_disk_gb: 12,
    size_bytes: 9_000_000_000,
  },
  {
    name: "xfs.15GB",
    kind: "single",
    description: "XFS, 15 ГБ",
    min_disk_gb: 15,
    size_bytes: 4_500_000_000,
  },
  {
    name: "15GB.single",
    kind: "single",
    description: "Single 15 ГБ",
    min_disk_gb: 15,
    size_bytes: 4_500_000_000,
  },
];

/**
 * Диски ВМ (`vm_disks`), keyed по `vm.id` — mock-фолбэк раздела «Диски» в
 * карточке ВМ. Системный диск помечен `is_system` и не удаляется отдельно.
 */
export const MOCK_VM_DISKS: Record<string, VmDisk[]> = {
  "vm-101": [
    {
      id: "disk-101-sys",
      vm_id: "vm-101",
      name: "system",
      size_gb: 80,
      path: "/vms/vm-101.qcow2",
      target_dev: "vda",
      serial: "vm-101_system",
      is_system: true,
      fs: null,
      mount: "/",
      state: "ready",
    },
    {
      id: "disk-101-data",
      vm_id: "vm-101",
      name: "data",
      size_gb: 40,
      path: "/vms/vm-101_data.qcow2",
      target_dev: "vdb",
      serial: "vm-101_data",
      is_system: false,
      fs: "ext4",
      mount: "/data",
      state: "ready",
    },
  ],
  "vm-102": [
    {
      id: "disk-102-sys",
      vm_id: "vm-102",
      name: "system",
      size_gb: 40,
      path: "/vms/vm-102.qcow2",
      target_dev: "vda",
      serial: "vm-102_system",
      is_system: true,
      fs: null,
      mount: "/",
      state: "ready",
    },
  ],
  "vm-103": [
    {
      id: "disk-103-sys",
      vm_id: "vm-103",
      name: "system",
      size_gb: 15,
      path: "/vms/vm-103.qcow2",
      target_dev: "vda",
      serial: "vm-103_system",
      is_system: true,
      fs: "xfs",
      mount: "/",
      state: "ready",
    },
  ],
  "vm-201": [
    {
      id: "disk-201-sys",
      vm_id: "vm-201",
      name: "system",
      size_gb: 120,
      path: "/vms/vm-201.qcow2",
      target_dev: "vda",
      serial: "vm-201_system",
      is_system: true,
      fs: null,
      mount: "/",
      state: "ready",
    },
    {
      id: "disk-201-scratch",
      vm_id: "vm-201",
      name: "scratch",
      size_gb: 200,
      path: "/vms/vm-201_scratch.qcow2",
      target_dev: "vdb",
      serial: "vm-201_scratch",
      is_system: false,
      fs: "xfs",
      mount: "/scratch",
      state: "ready",
    },
  ],
};

/**
 * OS-версии каталога — mock-фолбэк для дропдауна в модалке astra-update ВМ
 * (живой режим ходит в `GET /os-versions`).
 */
export const MOCK_VM_OS_VERSIONS: { id: string; name: string }[] = [
  { id: "osv_1_7_5_9", name: "1.7.5.9" },
  { id: "osv_1_8_1_6", name: "1.8.1.6" },
];

/**
 * Снимки ВМ (`vm_snapshots`), keyed по `vm.id` — mock-фолбэк раздела «Снимки»
 * в карточке ВМ. Системные `<ver>_build` (`is_system`) в UI скрыты — держим их
 * в фикстуре, чтобы проверять фильтрацию.
 */
export const MOCK_VM_SNAPSHOTS: Record<string, VmSnapshot[]> = {
  "vm-101": [
    {
      id: "snap-101-18build",
      vm_id: "vm-101",
      name: "1.8.1.6_orel_build",
      description: "golden build 1.8.1.6 (скрыт)",
      parent_snapshot_id: null,
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "oryol",
      os_version: "1.8.1.6",
      is_system: true,
      state: "ready",
      size_bytes: 2_400_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "system",
    },
    {
      id: "snap-101-18-orel",
      vm_id: "vm-101",
      name: "1.8.1.6_орёл",
      description: "deliverable 1.8.1.6 Орёл",
      parent_snapshot_id: "snap-101-18build",
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "oryol",
      os_version: "1.8.1.6",
      is_system: false,
      state: "ready",
      size_bytes: 2_600_000_000,
      is_current: true,
      created_at: NOW,
      created_by: "system",
    },
    {
      id: "snap-101-18-smol",
      vm_id: "vm-101",
      name: "1.8.1.6_смоленск",
      description: "deliverable 1.8.1.6 Смоленск",
      parent_snapshot_id: "snap-101-18-orel",
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "smolensk",
      os_version: "1.8.1.6",
      is_system: false,
      state: "ready",
      size_bytes: 2_650_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "system",
    },
    {
      id: "snap-101-17-orel",
      vm_id: "vm-101",
      name: "1.7.5.9_орёл",
      description: "deliverable 1.7.5.9 Орёл",
      parent_snapshot_id: null,
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "oryol",
      os_version: "1.7.5.9",
      is_system: false,
      state: "ready",
      size_bytes: 2_300_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "system",
    },
    {
      id: "snap-101-17-smol",
      vm_id: "vm-101",
      name: "1.7.5.9_смоленск",
      description: "deliverable 1.7.5.9 Смоленск",
      parent_snapshot_id: "snap-101-17-orel",
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "smolensk",
      os_version: "1.7.5.9",
      is_system: false,
      state: "ready",
      size_bytes: 2_350_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "system",
    },
    {
      id: "snap-101-pre-regress",
      vm_id: "vm-101",
      name: "pre-regress",
      description: "перед regress-циклом",
      parent_snapshot_id: "snap-101-18-orel",
      snapshot_type: "full",
      kind: "user",
      mode: null,
      os_version: null,
      is_system: false,
      state: "ready",
      size_bytes: 3_100_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "alice",
    },
    {
      id: "snap-101-hotfix",
      vm_id: "vm-101",
      name: "hotfix-check",
      description: "проверка хотфикса",
      parent_snapshot_id: "snap-101-18-orel",
      snapshot_type: "disk_only",
      kind: "user",
      mode: null,
      os_version: null,
      is_system: false,
      state: "ready",
      size_bytes: 1_900_000_000,
      is_current: false,
      created_at: NOW,
      created_by: "alice",
    },
  ],
  "vm-201": [
    {
      id: "snap-201-build",
      vm_id: "vm-201",
      name: "1.8.1.6_orel_build",
      description: null,
      parent_snapshot_id: null,
      snapshot_type: "full",
      kind: "os_baseline",
      mode: "oryol",
      os_version: "1.8.1.6",
      is_system: true,
      state: "ready",
      size_bytes: 2_400_000_000,
      is_current: true,
      created_at: NOW,
      created_by: "system",
    },
  ],
};

/**
 * Аккаунты отдела (`server_account`) — mock-фолбэк для мультиселекта привязки
 * учёток в форме создания ВМ (живой режим ходит в `GET /server-accounts`).
 * Общие для серверов и ВМ; фильтруются по отделу хаба.
 */
export const MOCK_VM_ACCOUNTS: ServerAccount[] = [
  {
    id: "acc-core-tester",
    server_ids: [],
    department_id: "core",
    login: "tester",
    source: "managed",
    has_sudo: false,
    unix_groups: ["testers"],
    linked_user_id: null,
    shell: "/bin/bash",
    home_dir: "/home/tester",
    is_active: true,
    password_rotated_at: NOW,
    password_b64: null,
    created_at: NOW,
    updated_at: NOW,
    created_by: "alice",
  },
  {
    id: "acc-core-ci",
    server_ids: ["srv-07"],
    department_id: "core",
    login: "ci-runner",
    source: "managed",
    has_sudo: true,
    unix_groups: ["ci", "sudo"],
    linked_user_id: null,
    shell: "/bin/bash",
    home_dir: "/home/ci-runner",
    is_active: true,
    password_rotated_at: NOW,
    password_b64: null,
    created_at: NOW,
    updated_at: NOW,
    created_by: "alice",
  },
  {
    id: "acc-dtkk-ops",
    server_ids: [],
    department_id: "dtkk",
    login: "ops",
    source: "managed",
    has_sudo: true,
    unix_groups: ["ops"],
    linked_user_id: null,
    shell: "/bin/bash",
    home_dir: "/home/ops",
    is_active: true,
    password_rotated_at: NOW,
    password_b64: null,
    created_at: NOW,
    updated_at: NOW,
    created_by: "bob",
  },
];

/**
 * IPAM-пулы (`vm_ip_pool`) — mock-фолбэк раздела «IP-пулы» (живой режим ходит в
 * `GET /vm-ip-pools`). Пул `core` привязан к отделу; `core-hub07` — override на
 * конкретный хаб.
 */
export const MOCK_VM_IP_POOLS: VmIpPool[] = [
  {
    id: "pool-core",
    name: "core-lan",
    cidr: "10.177.103.0/24",
    gateway: "10.177.103.1",
    netmask: "255.255.255.0",
    dns: ["10.177.100.10", "8.8.8.8"],
    range_start: "10.177.103.50",
    range_end: "10.177.103.99",
    department_id: "core",
    server_id: null,
    created_at: NOW,
    updated_at: NOW,
  },
  {
    id: "pool-core-hub07",
    name: "core-hub07-only",
    cidr: "10.177.103.0/24",
    gateway: "10.177.103.1",
    netmask: "255.255.255.0",
    dns: ["10.177.100.10"],
    range_start: "10.177.103.150",
    range_end: "10.177.103.180",
    department_id: "core",
    server_id: "srv-07",
    created_at: NOW,
    updated_at: NOW,
  },
  {
    id: "pool-dtkk",
    name: "dtkk-lan",
    cidr: "10.177.101.0/24",
    gateway: "10.177.101.1",
    netmask: "255.255.255.0",
    dns: ["10.177.100.10"],
    range_start: "10.177.101.40",
    range_end: "10.177.101.80",
    department_id: "dtkk",
    server_id: null,
  },
];

/** Свободные адреса по пулу — mock для `GET /vms/available-ips`. */
export const MOCK_AVAILABLE_IPS: Record<string, string[]> = {
  "pool-core": [
    "10.177.103.53",
    "10.177.103.54",
    "10.177.103.55",
    "10.177.103.56",
  ],
  "pool-core-hub07": ["10.177.103.151", "10.177.103.152"],
  "pool-dtkk": ["10.177.101.45", "10.177.101.46", "10.177.101.47"],
};

/**
 * Пресеты стандартных ВМ (`vm_preset`) — mock-фолбэк раздела «Пресеты» (живой
 * режим ходит в `GET /vm-presets`). Bridge-пресет несёт статик-IP/номер
 * (разворачивается один раз глобально), NAT-пресет — один раз на хаб.
 */
export const MOCK_VM_PRESETS: VmPreset[] = [
  {
    id: "preset-core-rc",
    name: "core-rc-bridge",
    department_id: "core",
    box: "vm_station",
    os_version: null,
    cpu: 4,
    ram_mb: 8192,
    disk_gb: 80,
    network_mode: "bridge",
    fixed_ip: "10.177.103.60",
    number: 900,
    created_at: NOW,
    updated_at: NOW,
  },
  {
    id: "preset-core-sandbox",
    name: "core-sandbox-nat",
    department_id: "core",
    box: "1.8.1.o",
    os_version: "1.8.1.6",
    cpu: 2,
    ram_mb: 4096,
    disk_gb: 40,
    network_mode: "nat",
    fixed_ip: null,
    number: null,
    created_at: NOW,
    updated_at: NOW,
  },
  {
    id: "preset-dtkk-xfs",
    name: "dtkk-xfs-nat",
    department_id: "dtkk",
    box: "xfs.15GB",
    os_version: "1.8.1.6",
    cpu: 2,
    ram_mb: 2048,
    disk_gb: 15,
    network_mode: "nat",
    fixed_ip: null,
    number: null,
  },
];

/**
 * Данные консоли ВМ для mock-режима (`POST /vms/{id}/console`). VNC отдаёт
 * поднятый прокси (для демонстрации панели-вьювера), serial — ещё не поднятый
 * (пометка про инфраструктурное развёртывание).
 */
export function mockVmConsole(vm: Vm, kind: VmConsoleKind): VmConsoleResponse {
  const host = vm.ip_address ?? "10.177.103.51";
  const user = vm.mgmt_user ?? "u";
  if (kind === "ssh") {
    return {
      kind: "ssh",
      host,
      port: 22,
      username: user,
      password: null,
      command: `ssh ${user}@${host}`,
    };
  }
  if (kind === "vnc") {
    return {
      kind: "vnc",
      ws_url: `wss://vms-console.local/vms/${vm.id}/vnc`,
      host,
      port: 5901,
      password: "mock-otp-9f3a",
      proxy_ready: true,
    };
  }
  return {
    kind: "serial",
    ws_url: null,
    command: `virsh console ${vm.name}`,
    proxy_ready: false,
  };
}
