/**
 * Thin wrappers для `server_service` домена `vm` (виртуализация).
 *
 * База `/api/server/v1`. Базовый контракт (согласован с дизайном
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
 * Управление дисками, изменение ресурсов и каталог образов:
 *   GET    /vms/{id}/disks
 *   POST   /vms/{id}/disks               (202 task)
 *   DELETE /vms/{id}/disks/{disk_id}     (202 task)
 *   POST   /vms/{id}/disks/{disk_id}/resize  (202 task)
 *   PATCH  /vms/{id}                     (202 task, cpu/ram)
 *   GET    /vm-images
 *   POST   /vm-images/refresh
 *
 * Снимки, обновление ОС/allta/пароля и режим кред:
 *   GET    /vms/{id}/snapshots
 *   POST   /vms/{id}/snapshots               (202 task)
 *   POST   /vms/{id}/snapshots/{snap}/revert (202 task)
 *   DELETE /vms/{id}/snapshots/{snap}        (202 task)
 *   POST   /vms/{id}/astra-update            (202 task)
 *   POST   /vms/{id}/allta-update            (202 task)
 *   POST   /vms/{id}/passwd                  (202 task)
 *   PATCH  /vms/{id}/cred-strategy           (режим кред, синхронно)
 *
 * Prepare ВМ, mgmt-креды, сеть и IPAM-пулы:
 *   POST   /vms/{id}/prepare                 (202 task)
 *   POST   /vms/{id}/mgmt-creds/rotate       (202 task)
 *   POST   /vms/{id}/network                 (202 task)
 *   GET    /vms/available-ips?pool_id=       (свободные IP пула)
 *   GET    /vm-ip-pools
 *   POST   /vm-ip-pools
 *   PATCH  /vm-ip-pools/{id}
 *   DELETE /vm-ip-pools/{id}
 *
 * Пока сервис не отдаёт эти маршруты, страница `/vm` работает на mock-данных
 * (`@/mocks/vm`) в mock-режиме.
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
  /**
   * Подготовлена ли ВМ (`vm.prepare` пройден): базовая учётка `u:1` снята,
   * заведены per-VM управляющие креды. До prepare mgmt-кред нет.
   */
  is_managed?: boolean;
  /** Логин управляющей учётки ВМ (появляется после prepare). null — нет. */
  mgmt_user?: string | null;
  /** Момент последней ротации управляющих кред. null — не ротировались. */
  mgmt_creds_rotated_at?: Iso8601 | null;
  /** true, пока worker применяет свежую ротацию управляющих кред. */
  mgmt_creds_pending_apply?: boolean;
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
  /**
   * Пул IPAM, из которого берётся адрес для bridge. null — авто-выбор пула
   * бекендом (по отделу/хабу). Для `nat` не используется.
   */
  pool_id?: string | null;
  number?: number | null;
}

/** Тело PATCH /vms/{id}/status — смена booking-статуса. */
export interface VmStatusPatch {
  status: string;
}

/** Тело PATCH /vms/{id} — изменение ресурсов ВМ (cpu/ram). 202-задача. */
export interface VmUpdateRequest {
  cpu?: number;
  ram_mb?: number;
}

/**
 * Жизненный цикл диска ВМ (`vm_disks.state`). `ready` — привязан и готов;
 * промежуточные — во время worker-операции; `error` — операция упала. Хвост
 * строкой — backend может расширить набор.
 */
export type VmDiskState =
  | "creating"
  | "ready"
  | "resizing"
  | "deleting"
  | "error"
  | (string & {});

/**
 * Диск ВМ (`vm_disks`). Системный диск (`is_system`) — образный root-диск ВМ,
 * его нельзя удалить отдельно (сносится только с самой ВМ).
 */
export interface VmDisk {
  id: string;
  vm_id: string;
  name: string;
  size_gb: number;
  /** Путь qcow2 на хабе. null — ещё не создан. */
  path: string | null;
  /** Целевое устройство в домене (`vda`/`vdb`…). */
  target_dev: string | null;
  /** Серийник для сопоставления в госте (`<vm>_<disk>`). */
  serial: string | null;
  is_system: boolean;
  /** Файловая система (`ext4`/`xfs`/…). null — не форматировался. */
  fs: string | null;
  /** Точка монтирования в госте. null — не монтируется. */
  mount: string | null;
  state: VmDiskState;
  created_at?: Iso8601;
  updated_at?: Iso8601;
}

/** Envelope GET /vms/{id}/disks. */
export interface VmDiskListResponse {
  items: VmDisk[];
}

/** Тело POST /vms/{id}/disks — создать и привязать доп. диск. 202-задача. */
export interface VmDiskCreateRequest {
  name: string;
  size_gb: number;
  /** Файловая система для форматирования (`ext4`/`xfs`/…). null — не форматировать. */
  fs?: string | null;
  /** Точка монтирования в госте. null — не монтировать. */
  mount?: string | null;
}

/** Тело POST /vms/{id}/disks/{disk_id}/resize — новый размер (только рост). */
export interface VmDiskResizeRequest {
  size_gb: number;
}

/**
 * Семейство образа каталога:
 *  - `universal` — `vm_station` с внутренними qemu-img снимками нескольких ОС;
 *  - `single` — бокс под конкретную ОС/ФС/размер.
 */
export type VmImageKind = "universal" | "single" | (string & {});

/**
 * Образ каталога `vm_images` (источник — `test-box-config.json` на FTP).
 * `name` — то же значение, что уходит в `VmCreateRequest.box`.
 */
export interface VmImage {
  name: string;
  kind: VmImageKind;
  description?: string | null;
  /** Для `universal` — версии ОС, запечённые снимками в образе. */
  os_versions?: string[];
  /** Размер артефакта в байтах (если известен). */
  size_bytes?: number | null;
  /** URL `.tar.gz` в каталоге (обычно не нужен UI). */
  url?: string | null;
}

/** Envelope GET /vm-images. */
export interface VmImageListResponse {
  items: VmImage[];
}

/**
 * Тип снимка (`vm_snapshots.kind`):
 *  - `disk_only` — только диск (`virsh snapshot-create-as --disk-only`);
 *  - `full` — диск + память/состояние домена.
 */
export type VmSnapshotKind = "disk_only" | "full" | (string & {});

/**
 * Состояние снимка (`vm_snapshots.state`). `ready` — готов; промежуточные —
 * во время worker-операции; `error` — операция упала. Хвост строкой — backend
 * может расширить набор.
 */
export type VmSnapshotState =
  | "creating"
  | "ready"
  | "deleting"
  | "reverting"
  | "error"
  | (string & {});

/**
 * Снимок ВМ (`vm_snapshots`). Системные golden-снимки (`is_system`, имена
 * `<ver>_build`) в UI скрыты — их не показываем и не даём трогать (дизайн §6,
 * NQ4). Цепочка родителей — `parent_snapshot_id`; текущий активный — `is_current`.
 */
export interface VmSnapshot {
  id: string;
  vm_id: string;
  name: string;
  description: string | null;
  parent_snapshot_id: string | null;
  kind: VmSnapshotKind;
  is_system: boolean;
  state: VmSnapshotState;
  /** Размер снимка в байтах (если известен). */
  size_bytes: number | null;
  is_current: boolean;
  created_at: Iso8601;
  created_by?: string | null;
}

/** Envelope GET /vms/{id}/snapshots. */
export interface VmSnapshotListResponse {
  items: VmSnapshot[];
}

/** Тело POST /vms/{id}/snapshots — создать снимок. 202-задача. */
export interface VmSnapshotCreateRequest {
  name: string;
  description?: string | null;
  kind: VmSnapshotKind;
}

/**
 * Тело POST /vms/{id}/astra-update — обновление ОС до выбранной версии
 * каталога. Worker резолвит rc/репозитории по `os_version_id`, гоняет
 * `astra-update -A -T -r` и переснимает снимок под новую версию (дизайн §7,
 * референс §2.3).
 */
export interface VmAstraUpdateRequest {
  rc: string;
}

/**
 * Тело POST /vms/{id}/passwd — смена пароля учётки `u`. По режиму кред ВМ
 * (`per_snapshot`/`reroll`) worker либо хранит креды на снимок, либо
 * перекатывает пароль по всем не-`_build` снимкам (дизайн §6).
 */
export interface VmPasswdRequest {
  password: string;
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

/**
 * Пул IPAM (`vm_ip_pool`) — диапазон статических адресов для bridge-ВМ.
 * Привязан к отделу; `server_id` — опциональный override на конкретный хаб
 * (пул действует только на нём). Учёт занятости ведёт сервис по `vm.ip_address`.
 */
export interface VmIpPool {
  id: string;
  name: string;
  /** CIDR подсети, например `10.177.103.0/24`. */
  cidr: string;
  /** Шлюз по умолчанию. */
  gateway: string;
  /** Маска (dotted), напр. `255.255.255.0`. Может дублировать префикс CIDR. */
  netmask: string;
  /** DNS-серверы. */
  dns: string[];
  /** Начало выделяемого диапазона (включительно). */
  range_start: string;
  /** Конец выделяемого диапазона (включительно). */
  range_end: string;
  department_id: string;
  /** Override: пул только для этого хаба. null — на весь отдел. */
  server_id?: string | null;
  created_at?: Iso8601;
  updated_at?: Iso8601;
}

/** Envelope GET /vm-ip-pools. */
export interface VmIpPoolListResponse {
  items: VmIpPool[];
}

/** Тело POST /vm-ip-pools — создать пул. */
export interface VmIpPoolCreateRequest {
  name: string;
  cidr: string;
  gateway: string;
  netmask: string;
  dns: string[];
  range_start: string;
  range_end: string;
  department_id: string;
  server_id?: string | null;
}

/** Тело PATCH /vm-ip-pools/{id} — частичное изменение пула. */
export type VmIpPoolUpdateRequest = Partial<
  Omit<VmIpPoolCreateRequest, "department_id">
>;

/** Ответ GET /vms/available-ips — свободные адреса выбранного пула. */
export interface AvailableIpsResponse {
  pool_id: string;
  ips: string[];
}

/**
 * Тело POST /vms/{id}/network — сменить сетевой режим/адрес ВМ. Backend
 * перекладывает домен на bridge/NAT (правка XML, статика через гостя, reboot),
 * поэтому это 202-задача. Для `bridge` берётся `ip_address` или свободный из
 * `pool_id`; `null`/`null` — авто.
 */
export interface VmNetworkRequest {
  network_mode: VmNetworkMode;
  ip_address?: string | null;
  pool_id?: string | null;
}

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

// ── ресурсы (cpu/ram) ────────────────────────────────────────────────────────

/**
 * `PATCH /api/server/v1/vms/{id}` — изменение ресурсов ВМ (cpu/ram). Backend
 * останавливает домен, правит XML и запускает заново — поэтому это 202-задача.
 */
export function updateVm(
  id: string,
  body: VmUpdateRequest,
): Promise<TaskDispatchResponse> {
  return apiPatch<TaskDispatchResponse>(`/server/v1/vms/${id}`, body);
}

// ── диски ─────────────────────────────────────────────────────────────────────

/** `GET /api/server/v1/vms/{id}/disks` — диски ВМ. */
export function listVmDisks(vmId: string): Promise<VmDiskListResponse> {
  return apiGet<VmDiskListResponse>(`/server/v1/vms/${vmId}/disks`);
}

/** `POST /api/server/v1/vms/{id}/disks` — создать и привязать диск (202). */
export function createVmDisk(
  vmId: string,
  body: VmDiskCreateRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/disks`, body);
}

/** `DELETE /api/server/v1/vms/{id}/disks/{disk_id}` — отвязать и снести диск (202). */
export function deleteVmDisk(
  vmId: string,
  diskId: string,
  body?: ReasonBody,
): Promise<TaskDispatchResponse> {
  return apiDelete<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/disks/${diskId}`,
    body,
  );
}

/** `POST /api/server/v1/vms/{id}/disks/{disk_id}/resize` — увеличить диск (202). */
export function resizeVmDisk(
  vmId: string,
  diskId: string,
  body: VmDiskResizeRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/disks/${diskId}/resize`,
    body,
  );
}

// ── каталог образов ───────────────────────────────────────────────────────────

/** `GET /api/server/v1/vm-images` — каталог образов (для модалки создания ВМ). */
export function listVmImages(): Promise<VmImageListResponse> {
  return apiGet<VmImageListResponse>("/server/v1/vm-images");
}

/**
 * `POST /api/server/v1/vm-images/refresh` — перечитать каталог с FTP
 * (`test-box-config.json`) и вернуть свежий список.
 */
export function refreshVmImages(): Promise<VmImageListResponse> {
  return apiPost<VmImageListResponse>("/server/v1/vm-images/refresh");
}

// ── снимки ────────────────────────────────────────────────────────────────────

/**
 * `GET /api/server/v1/vms/{id}/snapshots` — снимки ВМ. Backend отдаёт и
 * системные `_build`, но UI их прячет (дизайн §6, NQ4).
 */
export function listVmSnapshots(vmId: string): Promise<VmSnapshotListResponse> {
  return apiGet<VmSnapshotListResponse>(`/server/v1/vms/${vmId}/snapshots`);
}

/** `POST /api/server/v1/vms/{id}/snapshots` — создать снимок (202). */
export function createVmSnapshot(
  vmId: string,
  body: VmSnapshotCreateRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/snapshots`,
    body,
  );
}

/** `POST /api/server/v1/vms/{id}/snapshots/{snap}/revert` — откат на снимок (202). */
export function revertVmSnapshot(
  vmId: string,
  snapshotId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/snapshots/${snapshotId}/revert`,
  );
}

/** `DELETE /api/server/v1/vms/{id}/snapshots/{snap}` — удалить снимок (202). */
export function deleteVmSnapshot(
  vmId: string,
  snapshotId: string,
): Promise<TaskDispatchResponse> {
  return apiDelete<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/snapshots/${snapshotId}`,
  );
}

// ── обновление ОС / allta / пароль ─────────────────────────────────────────────

/**
 * `POST /api/server/v1/vms/{id}/astra-update` — обновить ОС ВМ до выбранной
 * версии каталога (202). Worker переснимает снимок под новую версию.
 */
export function astraUpdateVm(
  vmId: string,
  body: VmAstraUpdateRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/astra-update`,
    body,
  );
}

/**
 * `POST /api/server/v1/vms/{id}/allta-update` — обновить guest-allta `.deb`
 * по всем не-`_build` снимкам ВМ (202).
 */
export function alltaUpdateVm(vmId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/allta-update`);
}

/**
 * `POST /api/server/v1/vms/{id}/passwd` — сменить пароль учётки `u` (202).
 * Поведение зависит от режима кред ВМ (`per_snapshot`/`reroll`).
 */
export function vmPasswd(
  vmId: string,
  body: VmPasswdRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/passwd`, body);
}

// ── режим кред ──────────────────────────────────────────────────────────────────

/**
 * `PATCH /api/server/v1/vms/{id}/cred-strategy` — сменить режим управляющих
 * кред ВМ (`per_snapshot`/`reroll`). Это метаданные (не libvirt-операция),
 * поэтому синхронный ответ с обновлённой карточкой ВМ.
 */
export function setVmCredStrategy(
  vmId: string,
  strategy: VmCredStrategy,
): Promise<Vm> {
  return apiPatch<Vm>(`/server/v1/vms/${vmId}/cred-strategy`, {
    cred_strategy: strategy,
  });
}

// ── prepare / mgmt-креды ────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/vms/{id}/prepare` — подготовить ВМ (202). Worker заходит
 * по базовой учётке `u:1`, гоняет подмножество серверного bootstrap, сносит
 * базовую учётку и заводит per-VM управляющие креды (дизайн §9). После успеха
 * `is_managed=true`.
 */
export function prepareVm(vmId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/prepare`);
}

/**
 * `POST /api/server/v1/vms/{id}/mgmt-creds/rotate` — ротация управляющих кред
 * ВМ (202). Генерирует новую пару/пароль, применяет через worker и отзывает
 * старый материал. ВМ обязана быть подготовлена (`is_managed`).
 */
export function rotateVmMgmtCreds(vmId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/mgmt-creds/rotate`,
  );
}

// ── сеть ────────────────────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/vms/{id}/network` — сменить сетевой режим/адрес (202).
 * Backend перекладывает домен на bridge/NAT и, для статики, прописывает адрес
 * в госте с последующим reboot (дизайн §8).
 */
export function setVmNetwork(
  vmId: string,
  body: VmNetworkRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/network`, body);
}

/**
 * `GET /api/server/v1/vms/available-ips` — свободные адреса выбранного пула.
 * Сервис считает занятость по `vm.ip_address` и резервам (опц. ARP-проба).
 */
export function getAvailableIps(poolId: string): Promise<AvailableIpsResponse> {
  return apiGet<AvailableIpsResponse>("/server/v1/vms/available-ips", {
    query: { pool_id: poolId },
  });
}

// ── IPAM: пулы (vm_ip_pool) ─────────────────────────────────────────────────────

/** Параметры фильтрации GET /vm-ip-pools. */
export interface ListVmIpPoolsQuery {
  department_id?: string;
  server_id?: string;
}

/** `GET /api/server/v1/vm-ip-pools` — список IPAM-пулов. */
export function listVmIpPools(
  query: ListVmIpPoolsQuery = {},
): Promise<VmIpPoolListResponse> {
  return apiGet<VmIpPoolListResponse>("/server/v1/vm-ip-pools", {
    query: {
      department_id: query.department_id,
      server_id: query.server_id,
    },
  });
}

/** `POST /api/server/v1/vm-ip-pools` — создать пул. */
export function createVmIpPool(body: VmIpPoolCreateRequest): Promise<VmIpPool> {
  return apiPost<VmIpPool>("/server/v1/vm-ip-pools", body);
}

/** `PATCH /api/server/v1/vm-ip-pools/{id}` — изменить пул. */
export function updateVmIpPool(
  poolId: string,
  body: VmIpPoolUpdateRequest,
): Promise<VmIpPool> {
  return apiPatch<VmIpPool>(`/server/v1/vm-ip-pools/${poolId}`, body);
}

/** `DELETE /api/server/v1/vm-ip-pools/{id}` — удалить пул. */
export function deleteVmIpPool(poolId: string): Promise<void> {
  return apiDelete<void>(`/server/v1/vm-ip-pools/${poolId}`);
}
