/**
 * Thin wrappers для `server_service` домена `vm` (виртуализация).
 *
 * База `/api/server/v1`. Контракт волны 1 (согласован с дизайном
 * `obsidian/VM-менеджер — дизайн (ФИНАЛ).md` §1/§12):
 *   GET    /vms
 *   GET    /vms/{id}
 *   POST   /vms                     (202 task)
 *   DELETE /vms/{id}                (202 task)
 *   POST   /vms/{id}/power          (202 task)
 *   POST   /vms/{id}/reserve
 *   POST   /vms/{id}/release
 *   PATCH  /vms/{id}/status
 *   GET    /vms/by-number/{n}
 *   GET    /servers/by-number/{n}
 *   POST   /servers/{id}/prepare-vms-hub  (202 task)
 *
 * Backend домена `vm` в разработке (волна 1). Пока сервис не отдаёт эти
 * маршруты, страница `/vm` работает на mock-данных (`@/mocks/vm`) в
 * mock-режиме; типы полей сверяются с доменным агентом на интеграции.
 *
 * Типы VM-сущности живут здесь же, а не в общем `./types.ts`: домен молодой,
 * держим его самодостаточным, чтобы не разносить правки по файлам, пока форма
 * ответа не устоялась.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  Iso8601,
  OffsetPaginatedResponse,
  ReasonBody,
  Server,
  TaskDispatchResponse,
} from "@/api/server/types";

// ── типы ──────────────────────────────────────────────────────────────────

/** Сетевой режим ВМ: мост на физ-NIC (static IP из пула) или NAT libvirt. */
export type VmNetworkMode = "bridge" | "nat";

/** Состояние питания домена (`virsh domstate`). Зеркало server power_state. */
export type VmPowerState = "on" | "off" | "unknown";

/**
 * Действие питания ВМ (`POST /vms/{id}/power`).
 *  - `start` — `virsh start`;
 *  - `shutdown` — graceful ACPI-выключение;
 *  - `reboot` — graceful reboot;
 *  - `reset` — hard reset (power-cycle);
 *  - `destroy` — hard power-off.
 */
export type VmPowerAction = "start" | "shutdown" | "reboot" | "reset" | "destroy";

/**
 * Стратегия управляющих кред на снимок:
 *  - `per_snapshot` — каждый снимок хранит свои шифр-креды (дёшево, дефолт);
 *  - `reroll` — единый пароль во всех снимках (паритет старого `passwd`).
 */
export type VmCredStrategy = "per_snapshot" | "reroll";

/**
 * Занятость ВМ (TTL-lock, зеркало серверного busy_state). Держим строкой с
 * хвостом — backend может расширить набор.
 */
export type VmBusyState = "free" | "busy" | "testing" | (string & {});

/**
 * Карточка ВМ (ответ GET/POST /vms). `status` — booking-статус
 * (`free` / `run test` / `debug test` / `<login>`), отдельный от питания.
 */
export interface Vm {
  id: string;
  name: string;
  /** Глобально уникальный номер (в паре servers+vm). null — не задан. */
  number: number | null;
  hub_server_id: string;
  department_id: string;
  os_version: string | null;
  box: string;
  network_mode: VmNetworkMode;
  ip_address: string | null;
  /** Booking-статус: free / run test / debug test / <login>. */
  status: string;
  power_state: VmPowerState;
  cpu: number;
  ram_mb: number;
  disk_gb: number;
  autostart: boolean;
  cred_strategy: VmCredStrategy;
  busy_state: VmBusyState;
  busy_since?: Iso8601 | null;
  busy_note?: string | null;
  busy_user_id?: string | null;
  /** Доступность по ICMP-ping с последней пробы. null — пробы не было. */
  ping_reachable?: boolean | null;
  ping_latency_ms?: number | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by?: string | null;
}

/**
 * VMS-hub — сервер, подготовленный под виртуализацию
 * (`virtualization=true`, статус `vms_hub`). Отдельного list-эндпоинта в
 * контракте нет: хабы выбираются из списка серверов по флагу `virtualization`.
 * Форма — подмножество `Server` плюс derived-счётчик ВМ.
 */
export interface VmHub {
  id: string;
  hostname: string;
  display_name: string | null;
  ip_address: string;
  department_id: string;
  /** Число ВМ на хабе (derived на клиенте из списка ВМ). */
  vm_count: number;
}

/** Тело POST /vms. `ip_address: null` = взять свободный из пула автоматически. */
export interface VmCreateRequest {
  hub_server_id: string;
  name: string;
  cpu: number;
  ram_mb: number;
  disk_gb: number;
  box: string;
  network_mode: VmNetworkMode;
  ip_address?: string | null;
  number?: number | null;
}

/** Тело PATCH /vms/{id}/status — смена booking-статуса. */
export interface VmStatusPatch {
  status: string;
}

/** Параметры фильтрации GET /vms. */
export interface ListVmsQuery {
  hub_server_id?: string;
  department_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

/**
 * Результат lookup'а по номеру: backend возвращает либо ВМ, либо сервер.
 * Дискриминатор — `kind`.
 */
export type ByNumberResult =
  | { kind: "vm"; vm: Vm }
  | { kind: "server"; server: Server };

// ── client ──────────────────────────────────────────────────────────────────

/** `GET /api/server/v1/vms` — страница ВМ (offset envelope). */
export function listVms(
  query: ListVmsQuery = {},
): Promise<OffsetPaginatedResponse<Vm>> {
  return apiGet<OffsetPaginatedResponse<Vm>>("/server/v1/vms", {
    query: {
      hub_server_id: query.hub_server_id,
      department_id: query.department_id,
      status: query.status,
      limit: query.limit,
      offset: query.offset,
    },
  });
}

/** `GET /api/server/v1/vms/{id}` — карточка ВМ. */
export function getVm(id: string): Promise<Vm> {
  return apiGet<Vm>(`/server/v1/vms/${id}`);
}

/** `POST /api/server/v1/vms` — создание ВМ (202, task_id). */
export function createVm(body: VmCreateRequest): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>("/server/v1/vms", body);
}

/** `DELETE /api/server/v1/vms/{id}` — удаление ВМ (202, task_id). */
export function deleteVm(
  id: string,
  body?: ReasonBody,
): Promise<TaskDispatchResponse> {
  return apiDelete<TaskDispatchResponse>(`/server/v1/vms/${id}`, body);
}

/** `POST /api/server/v1/vms/{id}/power` — питание ВМ (202, task_id). */
export function vmPower(
  id: string,
  action: VmPowerAction,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${id}/power`, { action });
}

/** `POST /api/server/v1/vms/{id}/reserve` — бронь ВМ. */
export function reserveVm(id: string, body?: ReasonBody): Promise<Vm> {
  return apiPost<Vm>(`/server/v1/vms/${id}/reserve`, {
    purpose: body?.reason,
  });
}

/** `POST /api/server/v1/vms/{id}/release` — снять бронь ВМ. */
export function releaseVm(id: string): Promise<Vm> {
  return apiPost<Vm>(`/server/v1/vms/${id}/release`);
}

/** `PATCH /api/server/v1/vms/{id}/status` — смена booking-статуса. */
export function patchVmStatus(id: string, body: VmStatusPatch): Promise<Vm> {
  return apiPatch<Vm>(`/server/v1/vms/${id}/status`, body);
}

/** `GET /api/server/v1/vms/by-number/{n}` — ВМ по номеру. */
export function getVmByNumber(n: number): Promise<Vm> {
  return apiGet<Vm>(`/server/v1/vms/by-number/${n}`);
}

/** `GET /api/server/v1/servers/by-number/{n}` — сервер по номеру. */
export function getServerByNumber(n: number): Promise<Server> {
  return apiGet<Server>(`/server/v1/servers/by-number/${n}`);
}

/**
 * `POST /api/server/v1/servers/{id}/prepare-vms-hub` — подготовить сервер как
 * VMS-hub (libvirt/сеть/firewall/pool/образы). 202, task_id.
 */
export function prepareVmsHub(
  serverId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/prepare-vms-hub`,
  );
}
