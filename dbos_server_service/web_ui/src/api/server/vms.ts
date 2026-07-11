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
 * Пресеты стандартных ВМ, autostart, teardown хаба и консоль:
 *   GET    /vm-presets
 *   POST   /vm-presets
 *   PATCH  /vm-presets/{id}
 *   DELETE /vm-presets/{id}
 *   POST   /servers/{id}/create-default-vms  (202 task)
 *   POST   /vms/{id}/autostart               (202 task)
 *   DELETE /servers/{id}/vms-hub             (202 task)
 *   POST   /vms/{id}/console                 (данные для подключения)
 *
 * Пока сервис не отдаёт эти маршруты, страница `/vm` работает на mock-данных
 * (`@/mocks/vm`) в mock-режиме.
 *
 * Типы VM-сущности живут здесь же, а не в общем `./types.ts`: домен молодой,
 * держим его самодостаточным, чтобы не разносить правки по файлам, пока форма
 * ответа не устоялась.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import { listWithTotal, type PaginatedList } from "@/api/auth/users";
import type {
  Iso8601,
  OffsetPaginatedResponse,
  PackageHistoryEntry,
  PackagesBulkActionKind,
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
 * Lifecycle-lock ВМ на время долгой операции (зеркало серверного busy_state).
 * `null`/пусто — ВМ свободна от лока; пока значение непустое, управляющие
 * операции над ВМ отбиваются до callback'а воркера. Хвост строкой — backend
 * может расширить набор.
 */
export type VmBusyState =
  | "creating"
  | "deleting"
  | "updating"
  | "powering"
  | "snapshotting"
  | "reverting"
  | "preparing"
  | "networking"
  | (string & {});

/** Русские подписи lifecycle-операций ВМ для индикатора в карточке/списке. */
const VM_BUSY_LABELS: Record<string, string> = {
  creating: "Создаётся",
  deleting: "Удаляется",
  updating: "Обновляется",
  powering: "Переключение питания",
  snapshotting: "Снимок",
  reverting: "Откат снимка",
  preparing: "Подготовка",
  networking: "Смена сети",
};

/**
 * Подпись текущей lifecycle-операции ВМ. `null` — ВМ не занята операцией
 * (busy_state пуст или неизвестен), тогда показываем booking-статус.
 */
export function vmBusyLabel(state?: VmBusyState | null): string | null {
  if (!state) return null;
  return VM_BUSY_LABELS[state] ?? null;
}

/**
 * Сетевой интерфейс ВМ (`VmResponse.nics[]`). Зеркало серверного списка
 * интерфейсов: устройство, режим подключения и адрес. ВМ создаётся с одним
 * гостевым NIC (`eth0`, virtio). `mac` libvirt генерит сам — обычно null;
 * `bridge` заполнен только для bridge-режима (мост хаба `br0`).
 */
export interface VmNic {
  name: string;
  model: string;
  network_mode: string;
  bridge?: string | null;
  mac?: string | null;
  ip_address?: string | null;
}

/**
 * Карточка ВМ (ответ GET/POST /vms). `status` — booking-статус
 * (`free` / `run test` / `debug test` / `<login>`), отдельный от питания.
 */
export interface Vm {
  id: string;
  name: string;
  /** Hostname гостя (`hostnamectl`). null — берётся имя ВМ. */
  hostname?: string | null;
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
  /** Lifecycle-lock: непустое значение = идёт долгая операция. null — свободна. */
  busy_state: VmBusyState | null;
  busy_since?: Iso8601 | null;
  busy_note?: string | null;
  busy_user_id?: string | null;
  /** Доступность по ICMP-ping с последней пробы. null — пробы не было. */
  ping_reachable?: boolean | null;
  ping_latency_ms?: number | null;
  /** Когда последний раз пробовали ping гостя. null — пробы не было. */
  ping_checked_at?: Iso8601 | null;
  /** Доступность SSH гостя с последней пробы. null — пробы не было. */
  ssh_reachable?: boolean | null;
  /** Когда последний раз пробовали SSH гостя. null — пробы не было. */
  ssh_checked_at?: Iso8601 | null;
  /** Последняя ошибка воркера по ВМ. null — ошибок не было. */
  last_error?: string | null;
  /**
   * Подготовлена ли ВМ (`vm.prepare` пройден): базовая учётка `u:1` снята,
   * заведены per-VM управляющие креды. До prepare mgmt-кред нет.
   */
  is_managed?: boolean;
  /** Логин управляющей учётки ВМ (появляется после prepare). null — нет. */
  mgmt_user?: string | null;
  /** Публичный SSH-ключ управляющей учётки ВМ. Приватный не отдаётся. null — нет. */
  mgmt_ssh_public_key?: string | null;
  /** Момент последней ротации управляющих кред. null — не ротировались. */
  mgmt_creds_rotated_at?: Iso8601 | null;
  /** true, пока worker применяет свежую ротацию управляющих кред. */
  mgmt_creds_pending_apply?: boolean;
  /** Гостевые сетевые интерфейсы ВМ (симметрия со списком у сервера). */
  network_interfaces?: string[];
  /** Детализация NIC: устройство, режим, мост, IP. */
  nics?: VmNic[];
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

/**
 * Деривация списка VMS-hub из серверов: оставляем подготовленные хабы
 * (`is_vms_hub`) и считаем на каждом число ВМ из переданного списка. Общая для
 * страниц /vm и /servers, чтобы маппинг сервер→хаб не расходился между ними.
 */
export function serversToVmHubs(servers: Server[], vms: Vm[]): VmHub[] {
  const counts = new Map<string, number>();
  for (const v of vms) {
    counts.set(v.hub_server_id, (counts.get(v.hub_server_id) ?? 0) + 1);
  }
  return servers
    .filter((s) => s.is_vms_hub === true)
    .map((s) => ({
      id: s.id,
      hostname: s.hostname,
      display_name: s.display_name,
      ip_address: s.ip_address,
      department_id: s.department_id,
      vm_count: counts.get(s.id) ?? 0,
    }));
}

/** Тело POST /vms. `ip_address: null` = взять свободный из пула автоматически. */
export interface VmCreateRequest {
  hub_server_id: string;
  /** Отдел-владелец ВМ. Обязателен бэком (VmCreate) — берём из отдела хаба. */
  department_id: string;
  name: string;
  /**
   * Hostname гостя (`hostnamectl set-hostname`). Пусто/`null` — берётся имя ВМ.
   */
  hostname?: string | null;
  cpu: number;
  ram_mb: number;
  /**
   * Размер системного диска. Worker переразмечает образ бокса через
   * `virt-resize` (рост или сжатие). Меньше `VmImage.min_disk_gb` бокса
   * backend отбивает.
   */
  disk_gb: number;
  box: string;
  /** ID бокса из реестра отдела (реестр перекрывает base_user/os_versions/url). */
  box_id?: string;
  network_mode: VmNetworkMode;
  ip_address?: string | null;
  /**
   * Пул IPAM, из которого берётся адрес для bridge. null — авто-выбор пула
   * бекендом (по отделу/хабу). Для `nat` не используется.
   */
  pool_id?: string | null;
  number?: number | null;
  autostart?: boolean;
  cred_strategy?: VmCredStrategy;
  /**
   * ID аккаунтов отдела (`server_account`), привязываемых к ВМ. Worker
   * провижнит их OS-юзерами в госте. Пусто — без привязки.
   */
  accounts?: string[];
}

/** Тело POST /vms/bulk — батч-создание нескольких ВМ одним запросом. */
export interface VmBulkCreateRequest {
  items: VmCreateRequest[];
}

/**
 * Результат по одной ВМ в ответе `POST /vms/bulk`. `status` — `queued`
 * (задача создания поставлена) либо `failed` (заявка отбита валидацией/
 * ёмкостью), причина в `error`.
 */
export interface VmBulkItemResult {
  name: string;
  status: "queued" | "failed" | (string & {});
  task_id?: string | null;
  vm_id?: string | null;
  error?: string | null;
}

/** Ответ `POST /vms/bulk` — per-item результат в порядке items запроса. */
export interface VmBulkCreateResponse {
  results: VmBulkItemResult[];
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
  /**
   * Минимальный размер системного диска, ГБ — занятое место образа бокса.
   * Меньше этого `virt-resize` физически не ужмёт: UI предупреждает в форме
   * ДО отправки. null/отсутствует — минимум неизвестен, проверку не делаем.
   */
  min_disk_gb?: number | null;
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
 * Способ снятия снимка (`vm_snapshots.snapshot_type`):
 *  - `disk_only` — только диск (`virsh snapshot-create-as --disk-only`);
 *  - `full` — диск + память/состояние домена.
 */
export type VmSnapshotType = "disk_only" | "full" | (string & {});

/**
 * Категория снимка (`vm_snapshots.kind`) — источник группировки в UI (дизайн §6–7):
 *  - `os_baseline` — чистые снимки версии ОС после сборки/astra-update
 *    (`<ver>_<mode>`); их UI группирует по версии;
 *  - `user` — созданные пользователем снимки.
 * Отдельное поле от `snapshot_type` (способ libvirt disk_only/full).
 */
export type VmSnapshotCategory = "os_baseline" | "user" | (string & {});

/**
 * Режим Астры снимка (уровень безопасности): Орёл (0) или Смоленск (2).
 * `orel` — текущее имя режима Орёл; `oryol` — прежнее (читаем на совместимость).
 */
export type VmSnapshotMode = "orel" | "oryol" | "smolensk" | (string & {});

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
  /** Способ снятия (`disk_only`/`full`). */
  snapshot_type: VmSnapshotType;
  /** Категория снимка (`os_baseline`/`user`) — источник группировки. */
  kind: VmSnapshotCategory;
  /** Режим Астры (`oryol`/`smolensk`). null — не относится (пользовательский). */
  mode?: VmSnapshotMode | null;
  /** Версия ОС снимка (для группировки os_baseline). null — не задана. */
  os_version?: string | null;
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
  snapshot_type: VmSnapshotType;
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

// ── пресеты стандартных ВМ (vm_preset) ──────────────────────────────────────

/**
 * Пресет «стандартной ВМ» (`vm_preset`) — шаблон для массового развёртывания
 * (дизайн §1). Разворачивается кнопкой «Развернуть стандартные ВМ» на хабе:
 * bridge-пресет со статикой — один раз глобально (уникальный `fixed_ip`/
 * `number`), NAT-пресет — один раз на хаб-сервер.
 */
export interface VmPreset {
  id: string;
  name: string;
  department_id: string;
  box: string;
  /** Версия ОS для single-боксов; null — определяется образом (universal). */
  os_version: string | null;
  cpu: number;
  ram_mb: number;
  disk_gb: number;
  network_mode: VmNetworkMode;
  /** Для bridge — статический IP (разворачивается один раз глобально). */
  fixed_ip: string | null;
  /** Глобально уникальный номер, присваиваемый развёрнутой ВМ. */
  number: number | null;
  created_at?: Iso8601;
  updated_at?: Iso8601;
}

/** Envelope GET /vm-presets. */
export interface VmPresetListResponse {
  items: VmPreset[];
}

/** Тело POST /vm-presets — создать пресет. */
export interface VmPresetCreateRequest {
  name: string;
  department_id: string;
  box: string;
  os_version?: string | null;
  cpu: number;
  ram_mb: number;
  disk_gb: number;
  network_mode: VmNetworkMode;
  fixed_ip?: string | null;
  number?: number | null;
}

/** Тело PATCH /vm-presets/{id} — частичное изменение (department_id не меняем). */
export type VmPresetUpdateRequest = Partial<
  Omit<VmPresetCreateRequest, "department_id">
>;

/** Параметры фильтрации GET /vm-presets. */
export interface ListVmPresetsQuery {
  department_id?: string;
}

// ── консоль ВМ (SSH / VNC / serial / SPICE) ─────────────────────────────────

/**
 * Вид консоли ВМ (`POST /vms/{id}/console`):
 *  - `ssh` — доступ по SSH (команда + креды mgmt/базовой учётки), дефолт;
 *  - `vnc` — графическая консоль через websockify+noVNC-прокси;
 *  - `serial` — последовательная консоль (`virsh console`);
 *  - `spice` — графическая консоль SPICE через прокси (нужен `graphics=spice`
 *    в домене на этапе create + подготовка консоли на хабе).
 */
export type VmConsoleKind = "ssh" | "vnc" | "serial" | "spice";

/** Тело POST /vms/{id}/console. */
export interface VmConsoleRequest {
  kind: VmConsoleKind;
}

/**
 * Ответ POST /vms/{id}/console — контракт подключения UI к консоли ВМ.
 *
 * Реальный проброс держит отдельный websockify/PTY-прокси: UI подключается к
 * нему по `ws_url` (для vnc/spice), предъявляя `token` (живёт `expires_in`
 * секунд). Для ssh/serial графического прокси нет — отдаются данные подключения
 * (host/port/username для ssh, host/serial_path для serial). server_service
 * токен не хранит: прокси валидирует его по общему секрету.
 */
export interface VmConsoleResponse {
  vm_id: string;
  kind: VmConsoleKind;
  /** Токен доступа: ssh/serial — короткоживущий `vmc_`; vnc/spice — HMAC-подпись для прокси. */
  token: string;
  /** Сколько секунд токен действителен. */
  expires_in: number;
  /** ssh — IP гостя; vnc/spice/serial — IP hub'а (там прокси). null — неизвестен. */
  host?: string | null;
  /** Путь websocket-эндпоинта прокси для этой ВМ и вида консоли. */
  ws_path: string;
  /** Полный ws(s)-URL прокси (vnc/spice). null для ssh/serial. */
  ws_url?: string | null;
  /** Порт дисплея на hub'е (vnc/spice) либо SSH-порт (ssh). null для serial. */
  port?: number | null;
  /** Устройство serial-консоли в госте (serial), иначе null. */
  serial_path?: string | null;
  /** Управляющий пользователь для ssh. Пароль/ключ прокси тянет отдельно. */
  username?: string | null;
  /** Пароль графической консоли (vnc/spice), если ВМ его требует. Обычно null. */
  password?: string | null;
}

/**
 * http(s)-ссылка на self-hosted вьювер прокси (noVNC/spice-html5) с
 * предъявлением токена в query. Прокси по этому пути на GET отдаёт HTML-вьювер,
 * на WS — сам поток; вьювер встраивается в рабочую область через same-origin
 * iframe.
 *
 * Хост из `ws_url` (backend зашивает туда абсолютный `VM_CONSOLE_PROXY_WS_BASE`,
 * например `wss://emm.devos…`) намеренно отбрасываем: пользователь может зайти
 * по IP, где этот DNS не резолвится. Берём только path+query и клеим к текущему
 * origin — как это делают консольные WS-хелперы. null, если у ответа нет
 * `ws_url` (ssh/serial или прокси ещё не развёрнут).
 */
export function vmConsoleViewerUrl(
  session: Pick<VmConsoleResponse, "ws_url" | "token">,
): string | null {
  if (!session.ws_url) return null;
  let pathAndQuery: string;
  try {
    const u = new URL(session.ws_url);
    pathAndQuery = `${u.pathname}${u.search}`;
  } catch {
    // ws_url без схемы/хоста — уже относительный, срезаем возможный ws(s)://host.
    pathAndQuery = session.ws_url.replace(/^ws(s?):\/\/[^/]*/i, "");
  }
  const base = pathAndQuery.startsWith("/") ? pathAndQuery : `/${pathAndQuery}`;
  const sep = base.includes("?") ? "&" : "?";
  return `${window.location.origin}${base}${sep}token=${encodeURIComponent(session.token)}`;
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

/**
 * `POST /api/server/v1/vms/bulk` — батч-создание нескольких ВМ одним запросом.
 * Диспатчит по задаче на каждую ВМ, «одна упала — остальные едут»: ответ несёт
 * per-item статус (`queued`/`failed`). Одиночное создание — частный случай
 * (один элемент).
 */
export function createVmsBulk(
  body: VmBulkCreateRequest,
): Promise<VmBulkCreateResponse> {
  return apiPost<VmBulkCreateResponse>("/server/v1/vms/bulk", body);
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

/** Параметры GET /vms/{id}/snapshots — поиск по имени + пагинация. */
export interface ListVmSnapshotsQuery {
  q?: string;
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/server/v1/vms/{id}/snapshots` — снимки ВМ. Backend отдаёт и
 * системные `_build`, но UI их прячет (дизайн §6, NQ4). Поддерживает поиск
 * (`?q=`) и пагинацию; без параметров вызывается без query.
 */
export function listVmSnapshots(
  vmId: string,
  query: ListVmSnapshotsQuery = {},
): Promise<VmSnapshotListResponse> {
  const hasQuery =
    query.q != null || query.limit != null || query.offset != null;
  const path = `/server/v1/vms/${vmId}/snapshots`;
  if (!hasQuery) return apiGet<VmSnapshotListResponse>(path);
  return apiGet<VmSnapshotListResponse>(path, {
    query: { q: query.q, limit: query.limit, offset: query.offset },
  });
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

// ── инвентаризация (наследовано от сервера) ─────────────────────────────────────

/**
 * `POST /api/server/v1/vms/{id}/inventory-sync` — снять hardware-inventory
 * гостя ВМ (202). VM-аналог серверного inventory-sync: worker заходит по SSH
 * через hub под управляющими кредами, снимает hostname/kernel/cpu/disks/os и
 * сдаёт результат callback'ом (обновляет `vm.os_version`). ВМ обязана быть
 * подготовлена (`is_managed`), иметь IP гостя и живой hub.
 */
export function inventorySyncVm(vmId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/inventory-sync`,
  );
}

/**
 * `POST /api/server/v1/vms/{id}/users-inventory` — снять OS-пользователей
 * гостя ВМ (202). VM-аналог серверного users-inventory: worker читает
 * getent passwd/group с гостя по SSH через hub, server_service reconcile'ит
 * привязанные учётки (warn-on-drift, БД-истину не перетирает). ВМ обязана быть
 * подготовлена (`is_managed`), иметь IP гостя и живой hub.
 */
export function usersInventoryVm(vmId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/users-inventory`,
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

// ── пресеты стандартных ВМ ──────────────────────────────────────────────────

/** `GET /api/server/v1/vm-presets` — список пресетов стандартных ВМ. */
export function listVmPresets(
  query: ListVmPresetsQuery = {},
): Promise<VmPresetListResponse> {
  return apiGet<VmPresetListResponse>("/server/v1/vm-presets", {
    query: { department_id: query.department_id },
  });
}

/** `POST /api/server/v1/vm-presets` — создать пресет. */
export function createVmPreset(
  body: VmPresetCreateRequest,
): Promise<VmPreset> {
  return apiPost<VmPreset>("/server/v1/vm-presets", body);
}

/** `PATCH /api/server/v1/vm-presets/{id}` — изменить пресет. */
export function updateVmPreset(
  presetId: string,
  body: VmPresetUpdateRequest,
): Promise<VmPreset> {
  return apiPatch<VmPreset>(`/server/v1/vm-presets/${presetId}`, body);
}

/** `DELETE /api/server/v1/vm-presets/{id}` — удалить пресет. */
export function deleteVmPreset(presetId: string): Promise<void> {
  return apiDelete<void>(`/server/v1/vm-presets/${presetId}`);
}

/**
 * `POST /api/server/v1/servers/{id}/create-default-vms` — развернуть на хабе
 * стандартные ВМ из пресетов отдела (202, task_id). Backend применяет правила
 * «bridge — один раз глобально / NAT — один раз на хаб».
 */
export function createDefaultVms(
  serverId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/create-default-vms`,
  );
}

// ── autostart / teardown / консоль ──────────────────────────────────────────

/**
 * `POST /api/server/v1/vms/{id}/autostart` — включить/выключить автозапуск ВМ
 * (`virsh autostart`). Worker правит домен по SSH — поэтому 202-задача.
 */
export function setVmAutostart(
  vmId: string,
  enabled: boolean,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(`/server/v1/vms/${vmId}/autostart`, {
    enabled,
  });
}

/**
 * `DELETE /api/server/v1/servers/{id}/vms-hub` — разобрать VMS-hub (снести
 * libvirt-конфигурацию/мост/pool, вернуть сервер в обычное состояние). 202,
 * task_id. Привилегированное действие уровня prepare.
 */
export function teardownVmsHub(
  serverId: string,
  body?: ReasonBody,
): Promise<TaskDispatchResponse> {
  return apiDelete<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/vms-hub`,
    body,
  );
}

/**
 * `POST /api/server/v1/vms/{id}/console` — получить данные для подключения к
 * консоли ВМ выбранного вида (ssh/vnc/serial/spice). Для vnc/spice бэк
 * возвращает ws(s)-URL прокси и токен; для ssh/serial — параметры подключения.
 */
export function openVmConsole(
  vmId: string,
  kind: VmConsoleKind,
): Promise<VmConsoleResponse> {
  return apiPost<VmConsoleResponse>(`/server/v1/vms/${vmId}/console`, { kind });
}

// ── учётки ВМ ─────────────────────────────────────────────────────────────────

/**
 * Учётка, привязанная к ВМ (`GET /vms/{id}/accounts`). Подмножество
 * `server_account` без секретов; `present_on_vm` показывает дрейф (привязка
 * есть, а в госте учётки нет).
 */
export interface VmAccount {
  account_id: string;
  login: string;
  has_sudo: boolean;
  unix_groups: string[];
  /** Публичный SSH-ключ учётки (открытый). null, если не задан. */
  ssh_public_key?: string | null;
  /** Реально ли учётка заведена в госте (false — дрейф). */
  present_on_vm: boolean;
}

/**
 * `GET /api/server/v1/vms/{id}/accounts` — учётки, привязанные к ВМ (worker
 * провижнит их OS-юзерами в госте). Backend отдаёт голый массив без envelope.
 */
export function listVmAccounts(vmId: string): Promise<VmAccount[]> {
  return apiGet<VmAccount[]>(`/server/v1/vms/${vmId}/accounts`);
}

// ── пакеты гостя ВМ ──────────────────────────────────────────────────────────

/** Установленный пакет в госте ВМ (`dpkg -l` / `rpm -qa`). */
export interface VmPackage {
  name: string;
  version: string | null;
}

/**
 * Ответ `GET /vms/{id}/packages` — сохранённый инвентарь + флаг диспатча.
 * По умолчанию отдаёт последний снятый воркером срез; при `?refresh=true`
 * дополнительно диспатчит свежий probe (`dispatched=true`, `task_id`), а
 * `packages`/`synced_at` пока несут прежний список — он обновится по callback'у.
 */
export interface VmPackagesResponse {
  vm_id: string;
  packages: VmPackage[];
  package_count: number;
  /** Откуда снят список (`dpkg`/`rpm`) или null. */
  source?: string | null;
  /** Когда список последний раз синкнут воркером (UTC ISO). null — probe не было. */
  synced_at?: string | null;
  /** Был ли по этому запросу поставлен свежий probe `vm.list_packages`. */
  dispatched: boolean;
  /** ID задачи `vm.list_packages`, если `dispatched=true`. */
  task_id?: string | null;
}

/**
 * `GET /api/server/v1/vms/{id}/packages` — установленные в госте пакеты
 * (последний снятый срез). `refresh=true` дополнительно ставит свежий probe
 * `vm.list_packages`: ВМ обязана быть prepared, иметь IP гостя и живой hub,
 * иначе 409 (VM_PREPARE_REQUIRED / VM_GUEST_IP_UNKNOWN / HUB_UNAVAILABLE).
 * `pattern` — shell glob фильтра (`linux-image*`, `*-dev`); пусто → `*`.
 */
export function listVmPackages(
  vmId: string,
  opts: { refresh?: boolean; pattern?: string } = {},
): Promise<VmPackagesResponse> {
  const path = `/server/v1/vms/${vmId}/packages`;
  const query: Record<string, string | boolean | undefined> = {};
  if (opts.refresh) query.refresh = true;
  if (opts.pattern) query.pattern = opts.pattern;
  if (Object.keys(query).length === 0) return apiGet<VmPackagesResponse>(path);
  return apiGet<VmPackagesResponse>(path, { query });
}

/**
 * Тело `POST /vms/{id}/packages/action` — установка/удаление/обновление пакетов
 * в госте ВМ. `packages` обязателен для `install`/`remove`; для `update` пусто
 * означает upgrade всех пакетов гостя. Набор действий тот же, что у серверного
 * bulk-action.
 */
export interface VmPackagesActionRequest {
  action: PackagesBulkActionKind;
  packages?: string[];
}

/**
 * `POST /api/server/v1/vms/{id}/packages/action` — поставить install/remove/
 * update пакетов гостя ВМ через worker (202, task_id). ВМ обязана быть prepared,
 * иметь IP гостя и живой hub, иначе backend отобьёт 409
 * (VM_PREPARE_REQUIRED / VM_GUEST_IP_UNKNOWN / HUB_UNAVAILABLE). Итог задачи
 * добирают поллингом `GET /tasks/{task_id}`.
 */
export function vmPackagesAction(
  vmId: string,
  body: VmPackagesActionRequest,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/vms/${vmId}/packages/action`,
    body,
  );
}

/** Параметры пагинации истории запросов пакетов ВМ. */
export interface VmPackageHistoryQuery {
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/server/v1/vms/{id}/packages/history` — страница прошлых сборов
 * пакетов гостя ВМ (DESC по времени). Форма записи совпадает с серверной
 * (`PackageHistoryEntry`): запрошенный паттерн + найденные пакеты прямо из
 * `task.result`, без повторного probe. Backend отдаёт голый массив +
 * `X-Total-Count`; протаскиваем оба через `listWithTotal` для «N из M».
 */
export function getVmPackageHistory(
  vmId: string,
  query: VmPackageHistoryQuery = {},
): Promise<PaginatedList<PackageHistoryEntry>> {
  const { limit = 20, offset = 0 } = query;
  return listWithTotal<PackageHistoryEntry>(
    `/server/v1/vms/${vmId}/packages/history`,
    { limit, offset },
  );
}
