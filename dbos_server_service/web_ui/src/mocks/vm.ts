/**
 * Mock-данные VM-зоны для dev/mock-режима (`VITE_USE_MOCK_AUTH=true`).
 *
 * Backend домена `vm` в разработке (волна 1). Пока сервис не отдаёт `/vms`
 * и `/servers/{id}/prepare-vms-hub`, страница `/vm` и VM-группа в списке
 * серверов рендерятся на этих данных. Живой режим (`false`) ходит в
 * `@/api/server/vms`.
 */

import type { Vm, VmHub } from "@/api/server/vms";

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
