/**
 * Страница /vm — зона «Виртуализация».
 *
 * Раскладка как у /server: Shell + Aside (по-номеру lookup + список хабов +
 * кандидаты на prepare) + Workzone (список ВМ хаба или карточка ВМ).
 *
 * Базовый функционал VM-менеджера: hub.prepare, список/создание/питание/бронь ВМ,
 * lookup по номеру, статус задач. Backend домена `vm` в разработке — в
 * mock-режиме данные берутся из `@/mocks/vm`, живой режим ходит в
 * `@/api/server/vms`. Тонкая матрица прав `vm.*` (дизайн §2) ещё не приходит в
 * persona — гейтим серверной ролью через `@/lib/rbac`.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  ArrowLeft,
  ArrowUpCircle,
  Boxes,
  Camera,
  Cpu,
  HardDrive,
  KeyRound,
  Maximize2,
  MonitorPlay,
  Network,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Rocket,
  Search,
  ShieldCheck,
  Square,
  Layers,
  TerminalSquare,
  Copy,
  Zap,
  Trash2,
  Undo2,
  Waypoints,
  Lock,
  Unlock,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Tabs } from "@/components/ui/Tabs";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useDeptLabel } from "@/lib/labels";
import {
  canManageVmNet,
  canManageVmPresets,
  canManageVms,
  hasVmZoneAccess,
} from "@/lib/rbac";
import { formatLatencyMs } from "@/pages/server/_serverShared";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { listServers } from "@/api/server/servers";
import { listOsVersions } from "@/api/server/osVersions";
import {
  alltaUpdateVm,
  astraUpdateVm,
  createDefaultVms,
  createVm,
  createVmDisk,
  createVmIpPool,
  createVmPreset,
  createVmSnapshot,
  deleteVm,
  deleteVmDisk,
  deleteVmIpPool,
  deleteVmPreset,
  deleteVmSnapshot,
  getAvailableIps,
  getVmByNumber,
  getServerByNumber,
  listVmDisks,
  listVmImages,
  listVmIpPools,
  listVmPresets,
  listVmSnapshots,
  listVms,
  openVmConsole,
  prepareVm,
  refreshVmImages,
  releaseVm,
  reserveVm,
  resizeVmDisk,
  revertVmSnapshot,
  rotateVmMgmtCreds,
  setVmAutostart,
  setVmCredStrategy,
  setVmNetwork,
  updateVm,
  updateVmIpPool,
  updateVmPreset,
  vmPasswd,
  vmPower,
  type Vm,
  type VmConsoleKind,
  type VmConsoleResponse,
  type VmCreateRequest,
  type VmCredStrategy,
  type VmDisk,
  type VmDiskCreateRequest,
  type VmHub,
  type VmImage,
  type VmIpPool,
  type VmIpPoolCreateRequest,
  type VmIpPoolUpdateRequest,
  type VmNetworkMode,
  type VmNetworkRequest,
  type VmPowerAction,
  type VmPreset,
  type VmPresetCreateRequest,
  type VmPresetUpdateRequest,
  type VmSnapshot,
  type VmSnapshotCreateRequest,
  type VmSnapshotKind,
  type VmUpdateRequest,
} from "@/api/server/vms";
import type { OsVersion, TaskDispatchResponse } from "@/api/server/types";
import {
  MOCK_AVAILABLE_IPS,
  MOCK_VM_DISKS,
  MOCK_VM_HUBS,
  MOCK_VM_IMAGES,
  MOCK_VM_IP_POOLS,
  MOCK_VM_OS_VERSIONS,
  MOCK_VM_PRESETS,
  MOCK_VM_SNAPSHOTS,
  MOCK_VMS,
  mockVmConsole,
} from "@/mocks/vm";

// ── data helpers (mock ↔ live) ──────────────────────────────────────────────

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

// ── page ────────────────────────────────────────────────────────────────────

export function Vm() {
  const { persona } = usePersona();
  const toast = useToast();
  const mock = useMockMode();
  const [params, setParams] = useSearchParams();

  const zoneBlocked = !hasVmZoneAccess(persona);
  const canManage = canManageVms(persona);
  const canNet = canManageVmNet(persona);
  const canPresets = canManageVmPresets(persona);

  const selectedHubId = params.get("hub");
  const selectedVmId = params.get("id");
  const action = params.get("action"); // "new" | null
  const zone = params.get("zone"); // "pools" | "presets" | null

  // Хабы: derived из серверов с is_vms_hub=true (+ счётчик ВМ). В mock-режиме —
  // фикстуры. Подготовка/разбор хаба живут в карточке сервера (вкладка
  // «Управление»), здесь только просмотр хабов и работа с их ВМ.
  const hubsAndVmsQ = useQuery<{ hubs: VmHub[]; vms: Vm[] }>(
    async () => {
      if (mock) {
        return { hubs: MOCK_VM_HUBS, vms: MOCK_VMS };
      }
      const [srv, vmsPage] = await Promise.all([
        listServers({ limit: 200 }),
        listVms({ limit: 500 }),
      ]);
      const counts = new Map<string, number>();
      for (const v of vmsPage.items) {
        counts.set(v.hub_server_id, (counts.get(v.hub_server_id) ?? 0) + 1);
      }
      const hubs: VmHub[] = srv.items
        .filter((s) => (s as { is_vms_hub?: boolean }).is_vms_hub === true)
        .map((s) => ({
          id: s.id,
          hostname: s.hostname,
          display_name: s.display_name,
          ip_address: s.ip_address,
          department_id: s.department_id,
          vm_count: counts.get(s.id) ?? 0,
        }));
      return { hubs, vms: vmsPage.items };
    },
    [mock],
    { enabled: !zoneBlocked, keepPreviousDataOnError: true },
  );

  const hubs = useMemo(() => hubsAndVmsQ.data?.hubs ?? [], [hubsAndVmsQ.data]);
  const allVms = useMemo(() => hubsAndVmsQ.data?.vms ?? [], [hubsAndVmsQ.data]);

  const selectedHub = hubs.find((h) => h.id === selectedHubId) ?? null;
  const selectedVm = allVms.find((v) => v.id === selectedVmId) ?? null;
  const hubVms = useMemo(
    () => (selectedHubId ? allVms.filter((v) => v.hub_server_id === selectedHubId) : []),
    [allVms, selectedHubId],
  );

  // Каталог образов для модалки создания. Живой режим ходит в `/vm-images`;
  // при пустом ответе/сбое остаётся mock-фолбэк, чтобы модалка была рабочей.
  const imagesQ = useQuery<VmImage[]>(
    async () => {
      if (mock) return MOCK_VM_IMAGES;
      const res = await listVmImages();
      return res.items;
    },
    [mock],
    { enabled: !zoneBlocked, keepPreviousDataOnError: true },
  );
  const images = useMemo(
    () =>
      imagesQ.data && imagesQ.data.length > 0 ? imagesQ.data : MOCK_VM_IMAGES,
    [imagesQ.data],
  );

  async function handleRefreshImages() {
    if (mock) {
      imagesQ.refetch();
      toast.info("Каталог образов (mock) обновлён");
      return;
    }
    try {
      await refreshVmImages();
      imagesQ.refetch();
      toast.success("Каталог образов обновлён");
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось обновить каталог образов"));
    }
  }

  function selectHub(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("hub", id);
    else next.delete("hub");
    next.delete("id");
    next.delete("action");
    next.delete("zone");
    setParams(next, { replace: true });
  }
  function selectPools() {
    const next = new URLSearchParams(params);
    next.set("zone", "pools");
    next.delete("hub");
    next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function selectPresets() {
    const next = new URLSearchParams(params);
    next.set("zone", "presets");
    next.delete("hub");
    next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function selectVm(vm: Vm) {
    const next = new URLSearchParams(params);
    next.set("hub", vm.hub_server_id);
    next.set("id", vm.id);
    next.delete("action");
    setParams(next, { replace: true });
  }
  function startCreate() {
    if (!selectedHubId) return;
    const next = new URLSearchParams(params);
    next.set("action", "new");
    next.delete("id");
    setParams(next, { replace: true });
  }
  function closeAction() {
    const next = new URLSearchParams(params);
    next.delete("action");
    setParams(next, { replace: true });
  }

  async function handleCreate(body: VmCreateRequest) {
    try {
      const res = mock ? fakeDispatch() : await createVm(body);
      toast.success(`Создание ВМ ${body.name} — задача поставлена (${res.task_id})`);
      closeAction();
      hubsAndVmsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание ВМ не удалось"));
    }
  }

  async function resolveByNumber(n: number) {
    try {
      if (mock) {
        const vm = MOCK_VMS.find((v) => v.number === n);
        if (vm) {
          selectVm(vm);
          return;
        }
        const hub = MOCK_VM_HUBS.find((h) => Number(h.id.replace(/\D/g, "")) === n);
        if (hub) {
          selectHub(hub.id);
          return;
        }
        toast.warn(`Номер ${n} не найден среди ВМ`);
        return;
      }
      // Live: сперва пробуем ВМ, затем сервер (номер уникален в паре сущностей).
      try {
        const vm = await getVmByNumber(n);
        selectVm(vm);
        return;
      } catch {
        const server = await getServerByNumber(n);
        toast.info(`Номер ${n} — сервер ${server.hostname} (см. раздел «Серверы»)`);
      }
    } catch (e) {
      toast.error(apiErrMsg(e, `Номер ${n} не найден`));
    }
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <ByNumberLookup onResolve={resolveByNumber} />
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {hubsAndVmsQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {hubsAndVmsQ.error && !hubsAndVmsQ.loading && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(hubsAndVmsQ.error, "Список хабов не загрузился")}</div>
              <button className="btn btn-ghost mt-2" onClick={() => hubsAndVmsQ.refetch()}>
                Повторить
              </button>
            </div>
          </div>
        )}
        <div className="group-header px-3 mt-1 text-[11px] uppercase text-dim">
          VMS-hub · {hubs.length}
        </div>
        <div className="px-2 flex flex-col gap-0.5">
          {hubs.map((h) => (
            <HubRow
              key={h.id}
              hub={h}
              active={selectedHubId === h.id && !selectedVmId}
              onSelect={() => selectHub(h.id)}
            />
          ))}
          {!hubsAndVmsQ.loading && hubs.length === 0 && (
            <div className="px-3 py-3 text-xs text-dim">
              Нет подготовленных хабов. Подготовить сервер как VMS-hub можно в
              карточке сервера (вкладка «Управление»).
            </div>
          )}
        </div>
      </div>

      {(canNet || canPresets) && (
        <div className="border-t border-token px-2 py-2 shrink-0 flex flex-col gap-0.5">
          {canNet && (
            <button
              type="button"
              onClick={selectPools}
              className={`cred-row w-full text-left flex items-center gap-2 ${zone === "pools" ? "active" : ""}`}
              title="Настройка IPAM-пулов"
            >
              <Waypoints
                className={`w-4 h-4 ${zone === "pools" ? "text-accent" : "text-dim"}`}
              />
              <span className="flex-1 text-sm">IP-пулы (IPAM)</span>
            </button>
          )}
          {canPresets && (
            <button
              type="button"
              onClick={selectPresets}
              className={`cred-row w-full text-left flex items-center gap-2 ${zone === "presets" ? "active" : ""}`}
              title="Пресеты стандартных ВМ"
            >
              <Layers
                className={`w-4 h-4 ${zone === "presets" ? "text-accent" : "text-dim"}`}
              />
              <span className="flex-1 text-sm">Пресеты ВМ</span>
            </button>
          )}
        </div>
      )}
    </aside>
  );

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / виртуализация">
        <BlockedPane />
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="server_service / виртуализация" middle={aside}>
      {zone === "pools" && canNet ? (
        <IpPoolsPane mock={mock} />
      ) : zone === "presets" && canPresets ? (
        <PresetsPane mock={mock} />
      ) : action === "new" && selectedHub && canManage ? (
        <CreateVmPane
          hub={selectedHub}
          images={images}
          mock={mock}
          onRefreshImages={handleRefreshImages}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedVm ? (
        <VmDetail
          vm={selectedVm}
          mock={mock}
          canManage={canManage}
          onBack={() => selectHub(selectedVm.hub_server_id)}
          onChanged={() => hubsAndVmsQ.refetch()}
        />
      ) : selectedHub ? (
        <HubDetail
          hub={selectedHub}
          vms={hubVms}
          mock={mock}
          canManage={canManage}
          onOpenVm={selectVm}
          onCreate={startCreate}
          onChanged={() => hubsAndVmsQ.refetch()}
        />
      ) : (
        <EmptyPane />
      )}
    </Shell>
  );
}

// ── aside rows ──────────────────────────────────────────────────────────────

function ByNumberLookup({ onResolve }: { onResolve: (n: number) => void }) {
  const [value, setValue] = useState("");
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const n = Number.parseInt(value.trim(), 10);
    if (Number.isFinite(n)) onResolve(n);
  }
  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <Search className="w-4 h-4 text-dim" />
      <input
        className="bg-transparent outline-none flex-1 text-sm"
        placeholder="Поиск по номеру ВМ/сервера…"
        inputMode="numeric"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="submit" className="btn btn-sm" title="Перейти по номеру">
        →
      </button>
    </form>
  );
}

function HubRow({
  hub,
  active,
  onSelect,
}: {
  hub: VmHub;
  active: boolean;
  onSelect: () => void;
}) {
  const deptLabel = useDeptLabel(hub.department_id);
  const name = hub.display_name ?? hub.hostname;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left flex items-center gap-2 ${active ? "active" : ""}`}
    >
      <MonitorPlay className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{name}</div>
        <div className="text-[11px] text-dim flex items-center gap-2">
          <span className="truncate">{deptLabel}</span>
          <span>·</span>
          <span className="mono">{hub.ip_address}</span>
        </div>
      </div>
      <span className="badge" title="ВМ на хабе">
        {hub.vm_count} ВМ
      </span>
    </button>
  );
}

// ── workzone panes ──────────────────────────────────────────────────────────

function BlockedPane() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
        <div className="text-sm font-medium mb-2">
          Раздел недоступен для этой роли
        </div>
        <div className="text-xs text-dim">
          Виртуализация — часть server_service (dept-scoped). Работайте под
          департаментной ролью (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}

function EmptyPane() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <MonitorPlay className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim">
          Выберите VMS-hub слева, чтобы увидеть его виртуальные машины.
        </div>
      </div>
    </section>
  );
}

function HubDetail({
  hub,
  vms,
  mock,
  canManage,
  onOpenVm,
  onCreate,
  onChanged,
}: {
  hub: VmHub;
  vms: Vm[];
  mock: boolean;
  canManage: boolean;
  onOpenVm: (vm: Vm) => void;
  onCreate: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const deptLabel = useDeptLabel(hub.department_id);
  const defaultsOutcome = useTaskOutcome();
  const [pending, setPending] = useState(false);
  const name = hub.display_name ?? hub.hostname;

  async function handleCreateDefaults() {
    const ok = await confirm({
      title: "Развернуть стандартные ВМ",
      message: `Развернуть на хабе ${name} стандартные ВМ из пресетов отдела? Bridge-пресет со статикой разворачивается один раз глобально, NAT-пресет — один раз на хаб.`,
      confirmLabel: "Развернуть",
    });
    if (!ok) return;
    defaultsOutcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await createDefaultVms(hub.id);
      defaultsOutcome.track(
        `create-default-vms · ${name}`,
        res.task_id,
        res.status,
      );
      toast.success(`Развёртывание стандартных ВМ на ${name} — задача поставлена`);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось развернуть стандартные ВМ"));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <MonitorPlay className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{name}</h1>
            <span className="badge badge-ok">VMS-hub</span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{hub.ip_address}</span>
            <span>·</span>
            <span>
              dept: <b>{deptLabel}</b>
            </span>
            <span>·</span>
            <span>{vms.length} ВМ</span>
          </div>
        </div>
        {canManage && (
          <div className="flex items-center gap-2 flex-wrap justify-end">
            <button
              className="btn flex items-center gap-1"
              onClick={handleCreateDefaults}
              disabled={pending}
              title="Развернуть стандартные ВМ из пресетов отдела"
            >
              <Rocket className="w-4 h-4" /> Развернуть стандартные ВМ
            </button>
            <button
              className="btn btn-primary flex items-center gap-1"
              onClick={onCreate}
            >
              <Plus className="w-4 h-4" /> Создать ВМ
            </button>
          </div>
        )}
      </div>

      {defaultsOutcome.tracked && (
        <TaskOutcomeBanner
          outcome={defaultsOutcome.tracked}
          className="mx-5 mt-4"
          successText="Стандартные ВМ развёрнуты."
          onCancelled={defaultsOutcome.reset}
        />
      )}

      <div className="p-5">
        {vms.length === 0 ? (
          <div className="empty-card text-center text-sm text-dim">
            На этом хабе пока нет виртуальных машин.
          </div>
        ) : (
          <div className="surface-2 border border-token rounded overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase text-dim border-b border-token">
                  <th className="text-left px-3 py-2 font-medium">Имя</th>
                  <th className="text-left px-3 py-2 font-medium">№</th>
                  <th className="text-left px-3 py-2 font-medium">ОС / box</th>
                  <th className="text-left px-3 py-2 font-medium">Сеть / IP</th>
                  <th className="text-left px-3 py-2 font-medium">Ресурсы</th>
                  <th className="text-left px-3 py-2 font-medium">Питание</th>
                  <th className="text-left px-3 py-2 font-medium">Статус</th>
                </tr>
              </thead>
              <tbody>
                {vms.map((v) => (
                  <tr
                    key={v.id}
                    className="border-b border-token last:border-b-0 hover-bg cursor-pointer"
                    onClick={() => onOpenVm(v)}
                  >
                    <td className="px-3 py-1.5">{v.name}</td>
                    <td className="px-3 py-1.5 mono text-dim">{v.number ?? "—"}</td>
                    <td className="px-3 py-1.5 text-xs">
                      {v.os_version ?? "—"} · <span className="text-dim">{v.box}</span>
                    </td>
                    <td className="px-3 py-1.5 text-xs">
                      <span className="badge">{v.network_mode}</span>{" "}
                      <span className="mono">{v.ip_address ?? "—"}</span>
                    </td>
                    <td className="px-3 py-1.5 text-xs mono text-dim">
                      {v.cpu} vCPU · {Math.round(v.ram_mb / 1024)} ГБ · {v.disk_gb} ГБ
                    </td>
                    <td className="px-3 py-1.5">
                      <PowerBadge state={v.power_state} />
                    </td>
                    <td className="px-3 py-1.5 text-xs">{v.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function PowerBadge({ state }: { state: Vm["power_state"] }) {
  if (state === "on") return <span className="badge badge-ok">on</span>;
  if (state === "off") return <span className="badge badge-danger">off</span>;
  return <span className="badge">unknown</span>;
}

// ── VM detail (вкладки) ──────────────────────────────────────────────────────

type VmTabId =
  | "overview"
  | "power"
  | "snapshots"
  | "disks"
  | "network"
  | "maintenance";

/**
 * Карточка конкретной ВМ — раскладка вкладками по образцу карточки сервера
 * (`ServerDetail`). Общая шапка с именем/номером/питанием, ниже — таб-бар и
 * содержимое активной вкладки. Управляющие вкладки (питание, сеть,
 * обслуживание) видны только при праве на управление; обзор, снимки и диски
 * доступны и на чтение.
 */
export function VmDetail({
  vm,
  mock,
  canManage,
  onBack,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<VmTabId>("overview");
  const toast = useToast();
  const { confirm, prompt } = useConfirm();
  const deptLabel = useDeptLabel(vm.department_id);
  const powerOutcome = useTaskOutcome();
  const resourceOutcome = useTaskOutcome();
  const [local, setLocal] = useState<Vm>(vm);
  const [pending, setPending] = useState(false);
  const [resourceModal, setResourceModal] = useState(false);

  // vm prop меняется при refetch — подхватываем свежую копию.
  const view = local.id === vm.id ? local : vm;
  const reserved = view.busy_state !== "free" || !!view.busy_note;

  async function handleUpdateResources(body: VmUpdateRequest) {
    resourceOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await updateVm(view.id, body);
      resourceOutcome.track(
        `update cpu/ram · ${view.name}`,
        res.task_id,
        res.status,
      );
      toast.success(`Изменение ресурсов ${view.name} — задача поставлена`);
      setResourceModal(false);
      if (mock) {
        setLocal({
          ...view,
          cpu: body.cpu ?? view.cpu,
          ram_mb: body.ram_mb ?? view.ram_mb,
        });
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить ресурсы"));
    }
  }

  async function power(action: VmPowerAction, danger = false) {
    const ok = await confirm({
      title: `Питание: ${action}`,
      message: `Выполнить «${action}» на ВМ ${view.name}?`,
      confirmLabel: "Выполнить",
      danger,
    });
    if (!ok) return;
    powerOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await vmPower(view.id, action);
      powerOutcome.track(`power ${action}`, res.task_id, res.status);
      toast.success(`Питание «${action}» — задача поставлена`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Операция питания не удалась"));
    }
  }

  async function handleToggleAutostart() {
    if (pending) return;
    const next = !view.autostart;
    setPending(true);
    powerOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await setVmAutostart(view.id, next);
      powerOutcome.track(
        `autostart ${next ? "on" : "off"} · ${view.name}`,
        res.task_id,
        res.status,
      );
      setLocal({ ...view, autostart: next });
      toast.success(
        `Автозапуск ВМ ${view.name} — ${next ? "включён" : "выключен"} (задача поставлена)`,
      );
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить автозапуск"));
    } finally {
      setPending(false);
    }
  }

  async function handleReserve() {
    if (pending) return;
    const { ok, reason } = await prompt({
      title: "Забронировать ВМ",
      message: `Забронировать ${view.name}? Бронь блокирует операции других пользователей.`,
      reason: true,
      reasonLabel: "Примечание",
      reasonRequired: true,
      confirmLabel: "Забронировать",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? { ...view, busy_state: "busy", busy_note: reason.trim(), status: reason.trim() }
        : await reserveVm(view.id, { reason: reason.trim() });
      setLocal(next as Vm);
      onChanged();
      toast.success(`ВМ ${view.name} забронирована`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось забронировать"));
    } finally {
      setPending(false);
    }
  }

  async function handleRelease() {
    if (pending) return;
    const ok = await confirm({
      title: "Снять бронь",
      message: `Снять бронь с ${view.name}?`,
      confirmLabel: "Снять бронь",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? { ...view, busy_state: "free", busy_note: null, status: "free" }
        : await releaseVm(view.id);
      setLocal(next as Vm);
      onChanged();
      toast.success(`Бронь с ${view.name} снята`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось снять бронь"));
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    const { ok, reason } = await prompt({
      title: "Удалить ВМ",
      message: `Удалить ВМ ${view.name}? Домен и диски будут снесены. Действие необратимо.`,
      reason: true,
      reasonLabel: "Причина удаления",
      reasonRequired: true,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      const res = mock ? fakeDispatch() : await deleteVm(view.id, { reason: reason.trim() });
      toast.success(`Удаление ВМ ${view.name} — задача поставлена (${res.task_id})`);
      onBack();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  // Вкладки: обзор/снимки/диски доступны на чтение; питание, сеть и
  // обслуживание — только при праве на управление.
  const tabs: { id: VmTabId; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Обзор", icon: <MonitorPlay className="w-4 h-4" /> },
    ...(canManage
      ? [
          {
            id: "power" as const,
            label: "Питание",
            icon: <Power className="w-4 h-4" />,
          },
        ]
      : []),
    { id: "snapshots", label: "Снимки", icon: <Camera className="w-4 h-4" /> },
    { id: "disks", label: "Диски", icon: <HardDrive className="w-4 h-4" /> },
    ...(canManage
      ? [
          {
            id: "network" as const,
            label: "Сеть",
            icon: <Network className="w-4 h-4" />,
          },
          {
            id: "maintenance" as const,
            label: "Обслуживание",
            icon: <ShieldCheck className="w-4 h-4" />,
          },
        ]
      : []),
  ];
  // Если активная вкладка выпала из набора (сменилась роль/ВМ) — вернуться на обзор.
  const activeTab = tabs.some((t) => t.id === tab) ? tab : "overview";

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
        <button className="btn btn-ghost flex items-center gap-1" onClick={onBack}>
          <ArrowLeft className="w-4 h-4" /> К хабу
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{view.name}</h1>
            <span className="badge">ВМ</span>
            <PowerBadge state={view.power_state} />
            {reserved && (
              <span className="badge badge-warn flex items-center gap-1">
                <Lock className="w-3.5 h-3.5" /> {view.status}
              </span>
            )}
            <PingBadge vm={view} />
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{view.id}</span>
            <span>·</span>
            <span>№ <b className="mono">{view.number ?? "—"}</b></span>
            <span>·</span>
            <span>dept: <b>{deptLabel}</b></span>
          </div>
        </div>
      </div>

      <Tabs
        active={activeTab}
        onChange={(id) => setTab(id as VmTabId)}
        wrap
        tabs={tabs.map((t) => ({ id: t.id, label: t.label, icon: t.icon }))}
      />

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-auto">
        <div className="p-5 flex flex-col gap-4">
          {activeTab === "overview" && (
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-base">Параметры</h3>
                {canManage && (
                  <button
                    className="btn btn-sm flex items-center gap-1"
                    onClick={() => setResourceModal(true)}
                    title="Изменить vCPU и RAM"
                  >
                    <Cpu className="w-3.5 h-3.5" /> Изменить CPU/RAM
                  </button>
                )}
              </div>
              <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm">
                <Field k="Хаб" v={view.hub_server_id} mono />
                <Field k="ОС" v={view.os_version ?? "—"} />
                <Field k="box" v={view.box} />
                <Field k="Сеть" v={view.network_mode} />
                <Field k="IP-адрес" v={view.ip_address ?? "— (авто)"} mono />
                <Field k="vCPU" v={String(view.cpu)} />
                <Field k="RAM" v={`${view.ram_mb} МБ`} />
                <Field k="Диск" v={`${view.disk_gb} ГБ`} />
                <Field k="autostart" v={view.autostart ? "да" : "нет"} />
                <Field k="Стратегия кред" v={view.cred_strategy} />
                <Field k="Питание" v={view.power_state} />
                <Field k="Занятость" v={view.busy_state} />
              </dl>
              {resourceOutcome.tracked && (
                <TaskOutcomeBanner
                  outcome={resourceOutcome.tracked}
                  className="mt-3"
                  successText="Ресурсы применены."
                  onCancelled={resourceOutcome.reset}
                />
              )}
            </div>
          )}

          {activeTab === "power" && canManage && (
            <>
              <div className="card">
                <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
                  <Power className="w-4 h-4 text-accent" /> Питание
                </h3>
                <div className="flex gap-2 flex-wrap">
                  <button
                    className="btn btn-primary flex items-center gap-1"
                    onClick={() => power("start")}
                    disabled={view.power_state === "on"}
                  >
                    <Play className="w-4 h-4" /> Start
                  </button>
                  <button
                    className="btn flex items-center gap-1"
                    onClick={() => power("shutdown")}
                    disabled={view.power_state === "off"}
                  >
                    <Square className="w-4 h-4" /> Shutdown
                  </button>
                  <button
                    className="btn flex items-center gap-1"
                    onClick={() => power("reboot")}
                    disabled={view.power_state === "off"}
                  >
                    <RotateCcw className="w-4 h-4" /> Reboot
                  </button>
                  <button
                    className="btn flex items-center gap-1"
                    onClick={() => power("reset", true)}
                    disabled={view.power_state === "off"}
                    title="Hard reset (power-cycle)"
                  >
                    <RotateCcw className="w-4 h-4" /> Reset
                  </button>
                  <button
                    className="btn btn-danger flex items-center gap-1"
                    onClick={() => power("destroy", true)}
                    disabled={view.power_state === "off"}
                    title="Hard power-off"
                  >
                    <Power className="w-4 h-4" /> Destroy
                  </button>
                </div>
                <div className="flex items-center gap-3 mt-3 pt-3 border-t border-token flex-wrap">
                  <Zap
                    className={`w-4 h-4 ${view.autostart ? "text-accent" : "text-dim"}`}
                  />
                  <div className="flex-1 text-xs text-dim">
                    Автозапуск при старте хаба (<span className="mono">virsh autostart</span>):{" "}
                    <b>{view.autostart ? "включён" : "выключен"}</b>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={view.autostart}
                    aria-label="Автозапуск"
                    onClick={handleToggleAutostart}
                    disabled={pending}
                    className={`btn btn-sm ${view.autostart ? "btn-primary" : ""}`}
                    title="Включить/выключить автозапуск ВМ"
                  >
                    {view.autostart ? "Автозапуск: вкл" : "Автозапуск: выкл"}
                  </button>
                </div>
                {powerOutcome.tracked && (
                  <TaskOutcomeBanner
                    outcome={powerOutcome.tracked}
                    className="mt-3"
                    successText="Питание применено."
                    onCancelled={powerOutcome.reset}
                  />
                )}
              </div>

              <div className="card">
                <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
                  <Lock className="w-4 h-4 text-accent" /> Бронь
                </h3>
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex-1 text-xs text-dim">
                    Booking-статус: <span className="mono">{view.status}</span>
                    {view.busy_note && <> · {view.busy_note}</>}
                  </div>
                  {reserved ? (
                    <button
                      className="btn btn-sm flex items-center gap-1"
                      onClick={handleRelease}
                      disabled={pending}
                    >
                      <Unlock className="w-3.5 h-3.5" /> Снять бронь
                    </button>
                  ) : (
                    <button
                      className="btn btn-sm btn-primary flex items-center gap-1"
                      onClick={handleReserve}
                      disabled={pending}
                    >
                      <Lock className="w-3.5 h-3.5" /> Забронировать
                    </button>
                  )}
                </div>
              </div>
            </>
          )}

          {activeTab === "snapshots" && (
            <>
              <SnapshotsSection
                vm={view}
                mock={mock}
                canManage={canManage}
                onChanged={onChanged}
              />
              {canManage && (
                <CredStrategyCard
                  vm={view}
                  mock={mock}
                  onApplied={(next) => setLocal(next)}
                  onChanged={onChanged}
                />
              )}
            </>
          )}

          {activeTab === "disks" && (
            <DisksSection
              vm={view}
              mock={mock}
              canManage={canManage}
              onChanged={onChanged}
            />
          )}

          {activeTab === "network" && canManage && (
            <NetworkCard vm={view} mock={mock} onChanged={onChanged} />
          )}

          {activeTab === "maintenance" && canManage && (
            <>
              <PrepareMgmtCard
                vm={view}
                mock={mock}
                onApplied={(next) => setLocal(next)}
                onChanged={onChanged}
              />
              <OsOpsSection vm={view} mock={mock} onChanged={onChanged} />
              <ConsoleCard vm={view} mock={mock} />
              <div
                className="card"
                style={{ border: "1px solid var(--danger, #b91c1c)" }}
              >
                <div className="text-sm font-semibold flex items-center gap-2 text-danger mb-2">
                  Опасная зона
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex-1 text-xs text-dim">
                    Удаление ВМ сносит домен libvirt и все её диски. Действие
                    необратимо.
                  </div>
                  <button
                    className="btn btn-danger flex items-center gap-1"
                    onClick={handleDelete}
                  >
                    Удалить ВМ
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {resourceModal && (
        <ResourcesModal
          vm={view}
          onClose={() => setResourceModal(false)}
          onSubmit={handleUpdateResources}
        />
      )}
    </section>
  );
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-dim text-xs">{k}</dt>
      <dd className={mono ? "mono" : undefined}>{v}</dd>
    </>
  );
}

function PingBadge({ vm }: { vm: Vm }) {
  const reachable = vm.ping_reachable;
  if (reachable == null) {
    return (
      <span className="text-xs text-dim" title="ping: не проверялось">
        ping: —
      </span>
    );
  }
  const lat = formatLatencyMs(vm.ping_latency_ms);
  return (
    <span
      className={`text-xs ${reachable ? "text-ok" : "text-danger"}`}
      title="доступность по ping"
    >
      ping: <b>{reachable ? "доступен" : "недоступен"}</b>
      {reachable && lat ? ` · ${lat}` : ""}
    </span>
  );
}

// ── create VM ───────────────────────────────────────────────────────────────

function CreateVmPane({
  hub,
  images,
  mock,
  onRefreshImages,
  onCancel,
  onSubmit,
}: {
  hub: VmHub;
  images: VmImage[];
  mock: boolean;
  onRefreshImages: () => void | Promise<void>;
  onCancel: () => void;
  onSubmit: (body: VmCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [cpu, setCpu] = useState("2");
  const [ramMb, setRamMb] = useState("4096");
  const [diskGb, setDiskGb] = useState("40");
  const [box, setBox] = useState(images[0]?.name ?? "vm_station");
  const selectedImage = images.find((im) => im.name === box) ?? null;
  const [networkMode, setNetworkMode] = useState<VmNetworkMode>("bridge");
  const [poolId, setPoolId] = useState("");
  const [ipMode, setIpMode] = useState<"auto" | "pool" | "manual">("auto");
  const [ip, setIp] = useState("");
  const [number, setNumber] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const cpuN = Number.parseInt(cpu, 10);
  const ramN = Number.parseInt(ramMb, 10);
  const diskN = Number.parseInt(diskGb, 10);
  const numberN = number.trim() ? Number.parseInt(number.trim(), 10) : null;

  const nameError =
    name.trim() && !/^[a-zA-Z0-9._-]+$/.test(name.trim())
      ? "Имя: латиница, цифры, точка, дефис, подчёркивание"
      : null;
  // Сеть валидна: NAT — всегда; bridge — авто, либо выбранный из пула, либо
  // корректный ручной IPv4.
  const ipValid =
    networkMode === "nat" ||
    ipMode === "auto" ||
    (ipMode === "pool" && !!ip) ||
    (ipMode === "manual" && isLikelyIpv4(ip.trim()));
  const valid =
    !!name.trim() &&
    !nameError &&
    Number.isFinite(cpuN) &&
    cpuN > 0 &&
    Number.isFinite(ramN) &&
    ramN > 0 &&
    Number.isFinite(diskN) &&
    diskN > 0 &&
    ipValid;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const bridge = networkMode === "bridge";
    const body: VmCreateRequest = {
      hub_server_id: hub.id,
      name: name.trim(),
      cpu: cpuN,
      ram_mb: ramN,
      disk_gb: diskN,
      box,
      network_mode: networkMode,
      ip_address: !bridge
        ? null
        : ipMode === "manual"
          ? ip.trim()
          : ipMode === "pool"
            ? ip
            : null,
      pool_id: bridge && poolId ? poolId : null,
      number: numberN,
    };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 w-full max-w-2xl">
        <div className="flex items-center gap-2 mb-4">
          <button className="btn btn-ghost flex items-center gap-1" onClick={onCancel} type="button">
            <ArrowLeft className="w-4 h-4" /> Назад
          </button>
          <div className="text-sm text-dim">
            Создание ВМ на хабе <b>{hub.display_name ?? hub.hostname}</b>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="alse-1.8-rc"
            />
            {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
          </label>

          <div className="grid grid-cols-3 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">vCPU *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={cpu}
                onChange={(e) => setCpu(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">RAM, МБ *</span>
              <input
                className="input"
                type="number"
                min={256}
                step={256}
                value={ramMb}
                onChange={(e) => setRamMb(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Диск, ГБ *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={diskGb}
                onChange={(e) => setDiskGb(e.target.value)}
              />
            </label>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs flex items-center justify-between">
              <span>Образ (каталог) *</span>
              <button
                type="button"
                className="btn btn-ghost btn-sm flex items-center gap-1"
                onClick={() => onRefreshImages()}
                title="Перечитать каталог образов с FTP"
              >
                <RefreshCw className="w-3 h-3" /> Обновить каталог
              </button>
            </span>
            <select className="input" value={box} onChange={(e) => setBox(e.target.value)}>
              {images.map((im) => (
                <option key={im.name} value={im.name}>
                  {im.name}
                  {im.kind === "universal" ? " · universal" : ""}
                </option>
              ))}
            </select>
            {selectedImage && (
              <span className="text-[11px] text-dim">
                {selectedImage.description ?? selectedImage.name}
                {selectedImage.os_versions && selectedImage.os_versions.length > 0
                  ? ` · ОС: ${selectedImage.os_versions.join(", ")}`
                  : ""}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Сеть *</span>
            <select
              className="input"
              value={networkMode}
              onChange={(e) => setNetworkMode(e.target.value as VmNetworkMode)}
            >
              <option value="bridge">bridge (static IP из пула)</option>
              <option value="nat">nat (libvirt)</option>
            </select>
          </label>

          {networkMode === "bridge" ? (
            <PoolIpPicker
              mock={mock}
              departmentId={hub.department_id}
              serverId={hub.id}
              poolId={poolId}
              onPoolChange={setPoolId}
              ipMode={ipMode}
              onIpModeChange={setIpMode}
              ip={ip}
              onIpChange={setIp}
            />
          ) : (
            <div className="text-xs text-dim">
              NAT: адрес назначит libvirt (DHCP), пул не используется.
            </div>
          )}

          <button
            type="button"
            className="btn btn-ghost self-start flex items-center gap-1 text-xs"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "Скрыть доп. параметры" : "Доп. параметры"}
          </button>
          {showAdvanced && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Номер (глобально уникальный)</span>
              <input
                className="input"
                type="number"
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder="опционально"
              />
              <span className="text-[11px] text-dim">
                Прочие параметры (снимки, диски, autostart) — в отдельных разделах.
              </span>
            </label>
          )}

          <div className="flex items-center gap-2 mt-2">
            <button type="submit" className="btn btn-primary" disabled={submitting || !valid}>
              {submitting ? "Создаём…" : "Создать ВМ"}
            </button>
            <button type="button" className="btn" onClick={onCancel}>
              Отмена
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

// ── disks ─────────────────────────────────────────────────────────────────────

function DisksSection({
  vm,
  mock,
  canManage,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const diskOutcome = useTaskOutcome();
  const [createOpen, setCreateOpen] = useState(false);
  const [resizeTarget, setResizeTarget] = useState<VmDisk | null>(null);

  const disksQ = useQuery<VmDisk[]>(
    async () => {
      if (mock) return MOCK_VM_DISKS[vm.id] ?? [];
      const res = await listVmDisks(vm.id);
      return res.items;
    },
    [vm.id, mock],
    { keepPreviousDataOnError: true },
  );

  // В mock-режиме операции не ходят на backend — держим локальную копию, чтобы
  // список отражал создание/удаление/resize сразу.
  const [mockDisks, setMockDisks] = useState<VmDisk[] | null>(null);
  useEffect(() => {
    setMockDisks(null);
  }, [vm.id]);
  const disks = mock ? (mockDisks ?? disksQ.data ?? []) : (disksQ.data ?? []);

  async function handleCreate(body: VmDiskCreateRequest) {
    diskOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await createVmDisk(vm.id, body);
      diskOutcome.track(`disk create · ${body.name}`, res.task_id, res.status);
      toast.success(`Создание диска ${body.name} — задача поставлена`);
      setCreateOpen(false);
      if (mock) {
        const next: VmDisk = {
          id: `disk-mock-${Date.now()}`,
          vm_id: vm.id,
          name: body.name,
          size_gb: body.size_gb,
          path: null,
          target_dev: null,
          serial: `${vm.id}_${body.name}`,
          is_system: false,
          fs: body.fs ?? null,
          mount: body.mount ?? null,
          state: "creating",
        };
        setMockDisks([...disks, next]);
      } else {
        disksQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание диска не удалось"));
    }
  }

  async function handleDelete(disk: VmDisk) {
    const ok = await confirm({
      title: "Удалить диск",
      message: `Отвязать и удалить диск ${disk.name} (${disk.size_gb} ГБ)? Данные на нём будут потеряны.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    diskOutcome.reset();
    try {
      const res = mock
        ? fakeDispatch()
        : await deleteVmDisk(vm.id, disk.id, { reason: "ui" });
      diskOutcome.track(`disk delete · ${disk.name}`, res.task_id, res.status);
      toast.success(`Удаление диска ${disk.name} — задача поставлена`);
      if (mock) setMockDisks(disks.filter((d) => d.id !== disk.id));
      else {
        disksQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление диска не удалось"));
    }
  }

  async function handleResize(disk: VmDisk, sizeGb: number) {
    diskOutcome.reset();
    try {
      const res = mock
        ? fakeDispatch()
        : await resizeVmDisk(vm.id, disk.id, { size_gb: sizeGb });
      diskOutcome.track(`disk resize · ${disk.name}`, res.task_id, res.status);
      toast.success(`Resize диска ${disk.name} → ${sizeGb} ГБ — задача поставлена`);
      setResizeTarget(null);
      if (mock)
        setMockDisks(
          disks.map((d) =>
            d.id === disk.id ? { ...d, size_gb: sizeGb, state: "resizing" } : d,
          ),
        );
      else {
        disksQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Resize диска не удался"));
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-accent" /> Диски
        </h3>
        {canManage && (
          <button
            className="btn btn-sm btn-primary flex items-center gap-1"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="w-3.5 h-3.5" /> Создать диск
          </button>
        )}
      </div>

      {disksQ.loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : disksQ.error && disks.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(disksQ.error, "Список дисков не загрузился")}</div>
            <button className="btn btn-ghost mt-2" onClick={() => disksQ.refetch()}>
              Повторить
            </button>
          </div>
        </div>
      ) : disks.length === 0 ? (
        <div className="text-xs text-dim">Дисков нет.</div>
      ) : (
        <div className="surface-2 border border-token rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase text-dim border-b border-token">
                <th className="text-left px-3 py-2 font-medium">Имя</th>
                <th className="text-left px-3 py-2 font-medium">Размер</th>
                <th className="text-left px-3 py-2 font-medium">target</th>
                <th className="text-left px-3 py-2 font-medium">ФС / mount</th>
                <th className="text-left px-3 py-2 font-medium">serial</th>
                <th className="text-left px-3 py-2 font-medium">Тип</th>
                {canManage && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {disks.map((d) => (
                <tr key={d.id} className="border-b border-token last:border-b-0">
                  <td className="px-3 py-1.5">{d.name}</td>
                  <td className="px-3 py-1.5 mono">{d.size_gb} ГБ</td>
                  <td className="px-3 py-1.5 mono text-dim">{d.target_dev ?? "—"}</td>
                  <td className="px-3 py-1.5 text-xs">
                    {d.fs ?? "—"}
                    {d.mount ? ` · ${d.mount}` : ""}
                  </td>
                  <td className="px-3 py-1.5 mono text-dim text-xs">{d.serial ?? "—"}</td>
                  <td className="px-3 py-1.5">
                    {d.is_system ? (
                      <span className="badge">системный</span>
                    ) : (
                      <span className="badge badge-ok">доп.</span>
                    )}
                  </td>
                  {canManage && (
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1 justify-end">
                        <button
                          className="btn btn-sm flex items-center gap-1"
                          title="Изменить размер (только рост)"
                          onClick={() => setResizeTarget(d)}
                        >
                          <Maximize2 className="w-3.5 h-3.5" /> Resize
                        </button>
                        {!d.is_system && (
                          <button
                            className="btn btn-sm btn-danger flex items-center gap-1"
                            title="Удалить диск"
                            onClick={() => handleDelete(d)}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {diskOutcome.tracked && (
        <TaskOutcomeBanner
          outcome={diskOutcome.tracked}
          className="mt-3"
          successText="Операция с диском применена."
          onCancelled={diskOutcome.reset}
        />
      )}

      {createOpen && (
        <DiskCreateModal onClose={() => setCreateOpen(false)} onSubmit={handleCreate} />
      )}
      {resizeTarget && (
        <DiskResizeModal
          disk={resizeTarget}
          onClose={() => setResizeTarget(null)}
          onSubmit={(size) => handleResize(resizeTarget, size)}
        />
      )}
    </div>
  );
}

// ── snapshots ─────────────────────────────────────────────────────────────────

function SnapshotsSection({
  vm,
  mock,
  canManage,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const snapOutcome = useTaskOutcome();
  const [createOpen, setCreateOpen] = useState(false);

  const snapsQ = useQuery<VmSnapshot[]>(
    async () => {
      if (mock) return MOCK_VM_SNAPSHOTS[vm.id] ?? [];
      const res = await listVmSnapshots(vm.id);
      return res.items;
    },
    [vm.id, mock],
    { keepPreviousDataOnError: true },
  );

  const [mockSnaps, setMockSnaps] = useState<VmSnapshot[] | null>(null);
  useEffect(() => {
    setMockSnaps(null);
  }, [vm.id]);
  const raw = mock ? (mockSnaps ?? snapsQ.data ?? []) : (snapsQ.data ?? []);
  // Системные `<ver>_build` в UI не показываем (дизайн §6, NQ4).
  const snapshots = raw.filter((s) => !s.is_system);

  async function handleCreate(body: VmSnapshotCreateRequest) {
    snapOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await createVmSnapshot(vm.id, body);
      snapOutcome.track(`snapshot create · ${body.name}`, res.task_id, res.status);
      toast.success(`Создание снимка ${body.name} — задача поставлена`);
      setCreateOpen(false);
      if (mock) {
        const next: VmSnapshot = {
          id: `snap-mock-${Date.now()}`,
          vm_id: vm.id,
          name: body.name,
          description: body.description ?? null,
          parent_snapshot_id: null,
          kind: body.kind,
          is_system: false,
          state: "creating",
          size_bytes: null,
          is_current: false,
          created_at: new Date().toISOString(),
          created_by: null,
        };
        setMockSnaps([...raw, next]);
      } else {
        snapsQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание снимка не удалось"));
    }
  }

  async function handleRevert(snap: VmSnapshot) {
    const ok = await confirm({
      title: "Откатить на снимок",
      message: `Откатить ВМ ${vm.name} на снимок «${snap.name}»? Текущее состояние диска (и памяти для full) будет заменено на снимковое.`,
      confirmLabel: "Откатить",
      danger: true,
    });
    if (!ok) return;
    snapOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await revertVmSnapshot(vm.id, snap.id);
      snapOutcome.track(`snapshot revert · ${snap.name}`, res.task_id, res.status);
      toast.success(`Откат на «${snap.name}» — задача поставлена`);
      if (mock)
        setMockSnaps(
          raw.map((s) => ({ ...s, is_current: s.id === snap.id })),
        );
      else {
        snapsQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Откат на снимок не удался"));
    }
  }

  async function handleDelete(snap: VmSnapshot) {
    const ok = await confirm({
      title: "Удалить снимок",
      message: `Удалить снимок «${snap.name}»? Действие необратимо.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    snapOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await deleteVmSnapshot(vm.id, snap.id);
      snapOutcome.track(`snapshot delete · ${snap.name}`, res.task_id, res.status);
      toast.success(`Удаление снимка «${snap.name}» — задача поставлена`);
      if (mock) setMockSnaps(raw.filter((s) => s.id !== snap.id));
      else {
        snapsQ.refetch();
        onChanged();
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление снимка не удалось"));
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <Camera className="w-4 h-4 text-accent" /> Снимки
        </h3>
        {canManage && (
          <button
            className="btn btn-sm btn-primary flex items-center gap-1"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="w-3.5 h-3.5" /> Создать снимок
          </button>
        )}
      </div>

      {snapsQ.loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : snapsQ.error && snapshots.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(snapsQ.error, "Список снимков не загрузился")}</div>
            <button className="btn btn-ghost mt-2" onClick={() => snapsQ.refetch()}>
              Повторить
            </button>
          </div>
        </div>
      ) : snapshots.length === 0 ? (
        <div className="text-xs text-dim">Снимков нет.</div>
      ) : (
        <div className="surface-2 border border-token rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase text-dim border-b border-token">
                <th className="text-left px-3 py-2 font-medium">Имя</th>
                <th className="text-left px-3 py-2 font-medium">Описание</th>
                <th className="text-left px-3 py-2 font-medium">Тип</th>
                <th className="text-left px-3 py-2 font-medium">Создан</th>
                <th className="text-left px-3 py-2 font-medium">Текущий</th>
                {canManage && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {snapshots.map((s) => (
                <tr key={s.id} className="border-b border-token last:border-b-0">
                  <td className="px-3 py-1.5">
                    {s.name}
                    {s.state !== "ready" && (
                      <span className="badge ml-2 text-[11px]">{s.state}</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-dim">
                    {s.description ?? "—"}
                  </td>
                  <td className="px-3 py-1.5">
                    <span className="badge">
                      {s.kind === "full" ? "full" : "disk-only"}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-xs mono text-dim">
                    {formatSnapDate(s.created_at)}
                  </td>
                  <td className="px-3 py-1.5">
                    {s.is_current ? (
                      <span className="badge badge-ok">текущий</span>
                    ) : (
                      <span className="text-dim text-xs">—</span>
                    )}
                  </td>
                  {canManage && (
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1 justify-end">
                        <button
                          className="btn btn-sm flex items-center gap-1"
                          title="Откатить ВМ на этот снимок"
                          disabled={s.is_current}
                          onClick={() => handleRevert(s)}
                        >
                          <Undo2 className="w-3.5 h-3.5" /> Откат
                        </button>
                        <button
                          className="btn btn-sm btn-danger flex items-center gap-1"
                          title="Удалить снимок"
                          onClick={() => handleDelete(s)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {snapOutcome.tracked && (
        <TaskOutcomeBanner
          outcome={snapOutcome.tracked}
          className="mt-3"
          successText="Операция со снимком применена."
          onCancelled={snapOutcome.reset}
        />
      )}

      {createOpen && (
        <SnapshotCreateModal
          onClose={() => setCreateOpen(false)}
          onSubmit={handleCreate}
        />
      )}
    </div>
  );
}

function formatSnapDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    dateStyle: "short",
    timeStyle: "short",
  });
}

// ── OS / креды операции (astra-update / allta-update / passwd) ──────────────────

function OsOpsSection({
  vm,
  mock,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const opsOutcome = useTaskOutcome();
  const [astraOpen, setAstraOpen] = useState(false);
  const [passwdOpen, setPasswdOpen] = useState(false);

  async function handleAstra(_osVersionId: string, label: string) {
    opsOutcome.reset();
    try {
      const res = mock
        ? fakeDispatch()
        : await astraUpdateVm(vm.id, { rc: label });
      opsOutcome.track(`astra-update · ${label}`, res.task_id, res.status);
      toast.success(`Обновление ОС до ${label} — задача поставлена`);
      setAstraOpen(false);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление ОС не удалось"));
    }
  }

  async function handleAllta() {
    const ok = await confirm({
      title: "Обновить allta",
      message: `Обновить guest-allta на ВМ ${vm.name}? Пройдёт по всем не-«_build» снимкам, переустановит .deb и переснимет их.`,
      confirmLabel: "Обновить",
    });
    if (!ok) return;
    opsOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await alltaUpdateVm(vm.id);
      opsOutcome.track(`allta-update · ${vm.name}`, res.task_id, res.status);
      toast.success(`Обновление allta ${vm.name} — задача поставлена`);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление allta не удалось"));
    }
  }

  async function handlePasswd(password: string) {
    opsOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await vmPasswd(vm.id, { password });
      opsOutcome.track(`passwd · ${vm.name}`, res.task_id, res.status);
      toast.success(`Смена пароля на ${vm.name} — задача поставлена`);
      setPasswdOpen(false);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Смена пароля не удалась"));
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ArrowUpCircle className="w-4 h-4 text-accent" /> ОС и учётные данные
      </h3>
      <div className="text-xs text-dim mb-3">
        Обновление версии ОС переснимает снимок под выбранную версию; обновление
        allta и смена пароля идут по не-«_build» снимкам согласно режиму кред ВМ.
      </div>
      <div className="flex gap-2 flex-wrap">
        <button
          className="btn flex items-center gap-1"
          onClick={() => setAstraOpen(true)}
        >
          <ArrowUpCircle className="w-4 h-4" /> Обновить ОС (astra-update)
        </button>
        <button
          className="btn flex items-center gap-1"
          onClick={handleAllta}
        >
          <Boxes className="w-4 h-4" /> Обновить allta
        </button>
        <button
          className="btn flex items-center gap-1"
          onClick={() => setPasswdOpen(true)}
        >
          <KeyRound className="w-4 h-4" /> Обновить пароль
        </button>
      </div>

      {opsOutcome.tracked && (
        <TaskOutcomeBanner
          outcome={opsOutcome.tracked}
          className="mt-3"
          successText="Операция применена."
          onCancelled={opsOutcome.reset}
        />
      )}

      {astraOpen && (
        <AstraUpdateModal
          vm={vm}
          mock={mock}
          onClose={() => setAstraOpen(false)}
          onSubmit={handleAstra}
        />
      )}
      {passwdOpen && (
        <PasswdModal
          vm={vm}
          onClose={() => setPasswdOpen(false)}
          onSubmit={handlePasswd}
        />
      )}
    </div>
  );
}

// ── режим кред ──────────────────────────────────────────────────────────────────

function CredStrategyCard({
  vm,
  mock,
  onApplied,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onApplied: (next: Vm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [strategy, setStrategy] = useState<VmCredStrategy>(vm.cred_strategy);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setStrategy(vm.cred_strategy);
  }, [vm.id, vm.cred_strategy]);

  const changed = strategy !== vm.cred_strategy;

  async function apply() {
    if (!changed || pending) return;
    setPending(true);
    try {
      const next = mock
        ? { ...vm, cred_strategy: strategy }
        : await setVmCredStrategy(vm.id, strategy);
      onApplied(next as Vm);
      onChanged();
      toast.success(
        `Режим кред ВМ ${vm.name} → ${credStrategyLabel(strategy)}`,
      );
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось сменить режим кред"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <KeyRound className="w-4 h-4 text-accent" /> Режим управляющих кред
      </h3>
      <div className="text-xs text-dim mb-3">
        Наследуется от отдела; здесь — override на конкретную ВМ.{" "}
        <b>per_snapshot</b> — каждый снимок хранит свои креды (дёшево, дефолт);{" "}
        <b>reroll</b> — единый пароль во всех снимках (перекатывает снимки при
        смене).
      </div>
      <div className="flex items-end gap-2 flex-wrap">
        <label className="flex flex-col gap-1 text-sm flex-1 min-w-[220px]">
          <span className="text-dim text-xs">Режим</span>
          <select
            className="input"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as VmCredStrategy)}
            disabled={pending}
          >
            <option value="per_snapshot">
              per_snapshot — креды на снимок
            </option>
            <option value="reroll">reroll — единый пароль (паритет)</option>
          </select>
        </label>
        <button
          className="btn btn-primary"
          onClick={apply}
          disabled={!changed || pending}
        >
          {pending ? "Применяем…" : "Применить"}
        </button>
      </div>
    </div>
  );
}

function credStrategyLabel(s: VmCredStrategy): string {
  return s === "reroll" ? "reroll" : "per_snapshot";
}

// ── prepare / mgmt-креды ВМ ─────────────────────────────────────────────────────

function PrepareMgmtCard({
  vm,
  mock,
  onApplied,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onApplied: (next: Vm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const outcome = useTaskOutcome();
  const [pending, setPending] = useState(false);

  const managed = vm.is_managed === true;
  // pending — либо backend ещё применяет ротацию, либо мы поллим задачу.
  const applying =
    vm.mgmt_creds_pending_apply === true || (outcome.tracked?.polling ?? false);

  async function handlePrepare() {
    const ok = await confirm({
      title: "Подготовить ВМ",
      message: `Подготовить ВМ ${vm.name}? Worker зайдёт по базовой учётке u:1, выполнит bootstrap, снесёт базовую учётку и заведёт управляющие креды.`,
      confirmLabel: "Подготовить",
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await prepareVm(vm.id);
      outcome.track(`prepare · ${vm.name}`, res.task_id, res.status);
      toast.success(`Подготовка ВМ ${vm.name} — задача поставлена`);
      if (mock) {
        onApplied({
          ...vm,
          is_managed: true,
          mgmt_user: "dbosmgr",
          mgmt_creds_rotated_at: new Date().toISOString(),
          mgmt_creds_pending_apply: false,
        });
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Подготовка ВМ не удалась"));
    } finally {
      setPending(false);
    }
  }

  async function handleRotate() {
    const ok = await confirm({
      title: "Ротировать управляющие креды",
      message: `Сгенерировать новые управляющие креды ВМ ${vm.name} и применить их через worker? Старый материал будет отозван.`,
      confirmLabel: "Ротировать",
      danger: true,
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await rotateVmMgmtCreds(vm.id);
      outcome.track(`mgmt rotate · ${vm.name}`, res.task_id, res.status);
      toast.success(`Ротация кред ВМ ${vm.name} — задача поставлена`);
      if (mock) {
        onApplied({ ...vm, mgmt_creds_pending_apply: true });
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Ротация кред не удалась"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" /> Подготовка и управляющие
        креды
        {applying && (
          <span className="badge badge-warn text-[11px]">
            ротация применяется…
          </span>
        )}
      </h3>

      {!managed ? (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex-1 text-xs text-dim">
            ВМ ещё не подготовлена: базовая учётка <span className="mono">u:1</span>{" "}
            не снята, управляющих кред нет. Подготовка заведёт per-VM креды и
            уберёт базовый доступ.
          </div>
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={handlePrepare}
            disabled={pending}
          >
            <ShieldCheck className="w-4 h-4" />
            {pending ? "Ставим задачу…" : "Подготовить"}
          </button>
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm mb-3">
            <Field k="Состояние" v="подготовлена" />
            <Field k="mgmt-учётка" v={vm.mgmt_user ?? "—"} mono />
            <Field
              k="Креды ротированы"
              v={
                vm.mgmt_creds_rotated_at
                  ? formatSnapDate(vm.mgmt_creds_rotated_at)
                  : "—"
              }
              mono
            />
          </dl>
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 text-xs text-dim">
              Ротация генерирует новую управляющую пару/пароль ВМ и применяет их
              через worker.
            </div>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={handleRotate}
              disabled={pending || applying}
              title="Ротировать управляющие креды ВМ"
            >
              <KeyRound className="w-4 h-4" />
              {applying ? "Ротация идёт…" : "Ротировать креды"}
            </button>
          </div>
        </>
      )}

      {outcome.tracked && (
        <TaskOutcomeBanner
          outcome={outcome.tracked}
          className="mt-3"
          successText="Операция применена."
          onCancelled={outcome.reset}
        />
      )}
    </div>
  );
}

// ── сеть ВМ ─────────────────────────────────────────────────────────────────────

function NetworkCard({
  vm,
  mock,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const outcome = useTaskOutcome();
  const [open, setOpen] = useState(false);

  async function handleApply(body: VmNetworkRequest) {
    outcome.reset();
    try {
      const res = mock ? fakeDispatch() : await setVmNetwork(vm.id, body);
      outcome.track(`network · ${vm.name}`, res.task_id, res.status);
      toast.success(`Смена сети ВМ ${vm.name} — задача поставлена`);
      setOpen(false);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Смена сети не удалась"));
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <Network className="w-4 h-4 text-accent" /> Сеть
        </h3>
        <button
          className="btn btn-sm flex items-center gap-1"
          onClick={() => setOpen(true)}
        >
          <Pencil className="w-3.5 h-3.5" /> Изменить сеть
        </button>
      </div>
      <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm">
        <Field k="Режим" v={vm.network_mode} />
        <Field k="IP-адрес" v={vm.ip_address ?? "— (авто / NAT)"} mono />
      </dl>
      <div className="text-xs text-dim mt-2">
        Смена режима перекладывает домен на bridge/NAT; для статики адрес
        прописывается в госте с последующим reboot.
      </div>

      {outcome.tracked && (
        <TaskOutcomeBanner
          outcome={outcome.tracked}
          className="mt-3"
          successText="Сеть применена."
          onCancelled={outcome.reset}
        />
      )}

      {open && (
        <NetworkModal
          vm={vm}
          mock={mock}
          onClose={() => setOpen(false)}
          onSubmit={handleApply}
        />
      )}
    </div>
  );
}

// ── консоль ВМ ──────────────────────────────────────────────────────────────────

/**
 * Панель консоли ВМ: выбор вида (SSH / VNC / serial) и получение данных
 * подключения. Для SSH показываем готовую команду и креды; для VNC/serial —
 * ws-эндпоинт прокси. noVNC-вьювер не встраиваем (пакета нет в бандле, внешние
 * CDN запрещены CSP) — показываем адрес/порт прокси с пометкой.
 */
function ConsoleCard({ vm, mock }: { vm: Vm; mock: boolean }) {
  const toast = useToast();
  const [kind, setKind] = useState<VmConsoleKind>("ssh");
  const [session, setSession] = useState<VmConsoleResponse | null>(null);
  const [pending, setPending] = useState(false);

  function pick(next: VmConsoleKind) {
    setKind(next);
    setSession(null);
  }

  async function open() {
    setPending(true);
    try {
      const res = mock
        ? mockVmConsole(vm, kind)
        : await openVmConsole(vm.id, kind);
      setSession(res);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось получить данные консоли"));
    } finally {
      setPending(false);
    }
  }

  const kinds: { value: VmConsoleKind; label: string }[] = [
    { value: "ssh", label: "SSH" },
    { value: "vnc", label: "VNC" },
    { value: "serial", label: "Serial" },
  ];

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <TerminalSquare className="w-4 h-4 text-accent" /> Консоль
      </h3>
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <div className="flex items-center gap-1">
          {kinds.map((k) => (
            <button
              key={k.value}
              type="button"
              className={`btn btn-sm ${kind === k.value ? "btn-primary" : ""}`}
              onClick={() => pick(k.value)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-sm flex items-center gap-1"
          onClick={open}
          disabled={pending}
        >
          <TerminalSquare className="w-3.5 h-3.5" />
          {pending ? "Готовим…" : "Открыть консоль"}
        </button>
      </div>

      {session && <ConsoleSession session={session} />}
      {!session && (
        <div className="text-xs text-dim">
          Выберите вид консоли и нажмите «Открыть консоль».
        </div>
      )}
    </div>
  );
}

function ConsoleSession({ session }: { session: VmConsoleResponse }) {
  if (session.kind === "ssh") {
    return (
      <div className="surface-2 border border-token rounded p-3 flex flex-col gap-2">
        <div className="text-xs text-dim">
          Доступ по SSH под учёткой <span className="mono">{session.username}</span>.
        </div>
        <CopyableCommand text={session.command} />
        <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 text-xs">
          <Field k="host" v={`${session.host}:${session.port}`} mono />
          <Field k="Логин" v={session.username} mono />
          {session.password ? (
            <Field k="Пароль" v={session.password} mono />
          ) : (
            <Field k="Пароль" v="— (по ключу)" />
          )}
        </dl>
      </div>
    );
  }

  const isVnc = session.kind === "vnc";
  const port = isVnc ? session.port : null;
  return (
    <div className="surface-2 border border-token rounded p-3 flex flex-col gap-2">
      <div className="text-xs text-dim">
        {isVnc
          ? "Графическая консоль (VNC) через websockify-прокси."
          : "Последовательная консоль (serial) через прокси."}
      </div>
      <div className="border border-dashed border-token rounded p-4 text-center bg-black/5 dark:bg-white/5">
        <TerminalSquare className="w-8 h-8 mx-auto text-dim mb-2" />
        {session.proxy_ready && session.ws_url ? (
          <>
            <div className="text-xs">
              Прокси поднят. Подключайтесь noVNC/websocket-клиентом к эндпоинту:
            </div>
            <div className="mono text-xs break-all mt-1">{session.ws_url}</div>
          </>
        ) : (
          <div className="text-xs text-warn">
            Прокси-эндпоинт разворачивается инфраструктурно. Встроенный вьювер
            появится после его поднятия.
          </div>
        )}
      </div>
      <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 text-xs">
        {isVnc && port != null && (
          <Field k="host" v={`${session.host}:${port}`} mono />
        )}
        {session.ws_url && <Field k="ws-прокси" v={session.ws_url} mono />}
        {isVnc && session.password && (
          <Field k="Пароль VNC" v={session.password} mono />
        )}
      </dl>
      {!isVnc && (
        <>
          <div className="text-xs text-dim">Локальный доступ на хабе:</div>
          <CopyableCommand text={session.command} />
        </>
      )}
    </div>
  );
}

function CopyableCommand({ text }: { text: string }) {
  const toast = useToast();
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast.info("Скопировано в буфер");
    } catch {
      toast.warn("Не удалось скопировать — выделите вручную");
    }
  }
  return (
    <div className="flex items-center gap-2">
      <code className="mono text-xs flex-1 break-all bg-black/5 dark:bg-white/5 rounded px-2 py-1">
        {text}
      </code>
      <button
        type="button"
        className="btn btn-sm flex items-center gap-1"
        onClick={copy}
        title="Скопировать"
      >
        <Copy className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

/**
 * Селектор пула + свободного IP для bridge. Полностью управляемый: пулы и
 * свободные адреса тянет сам (mock ↔ live), значения поднимает наверх.
 */
function PoolIpPicker({
  mock,
  departmentId,
  serverId,
  poolId,
  onPoolChange,
  ipMode,
  onIpModeChange,
  ip,
  onIpChange,
}: {
  mock: boolean;
  departmentId: string;
  serverId: string;
  poolId: string;
  onPoolChange: (v: string) => void;
  ipMode: "auto" | "pool" | "manual";
  onIpModeChange: (v: "auto" | "pool" | "manual") => void;
  ip: string;
  onIpChange: (v: string) => void;
}) {
  const poolsQ = useQuery<VmIpPool[]>(
    async () => {
      if (mock) return MOCK_VM_IP_POOLS;
      const res = await listVmIpPools({ department_id: departmentId });
      return res.items;
    },
    [mock, departmentId],
    { keepPreviousDataOnError: true },
  );
  // Применимы пулы отдела без override и пулы, привязанные к этому хабу.
  const pools = (poolsQ.data ?? []).filter(
    (p) => !p.server_id || p.server_id === serverId,
  );

  const ipsQ = useQuery<string[]>(
    async () => {
      if (!poolId) return [];
      if (mock) return MOCK_AVAILABLE_IPS[poolId] ?? [];
      const res = await getAvailableIps(poolId);
      return res.ips;
    },
    [mock, poolId],
    { enabled: !!poolId && ipMode === "pool", keepPreviousDataOnError: true },
  );
  const availableIps = ipsQ.data ?? [];

  const ipError =
    ipMode === "manual" && ip.trim() && !isLikelyIpv4(ip.trim())
      ? "Ожидается IPv4-адрес"
      : null;

  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Пул IPAM</span>
        <select
          className="input"
          value={poolId}
          onChange={(e) => {
            onPoolChange(e.target.value);
            onIpChange("");
          }}
        >
          <option value="">— авто-выбор пула —</option>
          {pools.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.cidr}
              {p.server_id ? " · хаб-override" : ""}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">IP-адрес</span>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1 text-xs">
            <input
              type="radio"
              checked={ipMode === "auto"}
              onChange={() => onIpModeChange("auto")}
            />
            свободный автоматически
          </label>
          <label className="flex items-center gap-1 text-xs">
            <input
              type="radio"
              checked={ipMode === "pool"}
              onChange={() => onIpModeChange("pool")}
              disabled={!poolId}
            />
            выбрать из пула
          </label>
          <label className="flex items-center gap-1 text-xs">
            <input
              type="radio"
              checked={ipMode === "manual"}
              onChange={() => onIpModeChange("manual")}
            />
            вручную
          </label>
        </div>

        {ipMode === "pool" &&
          (ipsQ.loading ? (
            <div className="text-xs text-dim mt-1">Загрузка свободных IP…</div>
          ) : availableIps.length === 0 ? (
            <div className="text-xs text-warn mt-1">
              В пуле нет свободных адресов.
            </div>
          ) : (
            <select
              className="input mt-1"
              value={ip}
              onChange={(e) => onIpChange(e.target.value)}
            >
              <option value="">— выберите адрес —</option>
              {availableIps.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          ))}

        {ipMode === "manual" && (
          <>
            <input
              className="input mt-1"
              value={ip}
              onChange={(e) => onIpChange(e.target.value)}
              placeholder="10.177.103.51"
            />
            {ipError && (
              <span className="text-[11px] text-danger">{ipError}</span>
            )}
          </>
        )}
      </fieldset>
    </div>
  );
}

function NetworkModal({
  vm,
  mock,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  mock: boolean;
  onClose: () => void;
  onSubmit: (body: VmNetworkRequest) => void | Promise<void>;
}) {
  const [mode, setMode] = useState<VmNetworkMode>(vm.network_mode);
  const [poolId, setPoolId] = useState("");
  const [ipMode, setIpMode] = useState<"auto" | "pool" | "manual">("auto");
  const [ip, setIp] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const ipValid =
    mode === "nat" ||
    ipMode === "auto" ||
    (ipMode === "pool" && !!ip) ||
    (ipMode === "manual" && isLikelyIpv4(ip.trim()));
  const valid = ipValid && !submitting;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) return;
    const body: VmNetworkRequest =
      mode === "nat"
        ? { network_mode: "nat", ip_address: null, pool_id: null }
        : {
            network_mode: "bridge",
            ip_address:
              ipMode === "manual"
                ? ip.trim()
                : ipMode === "pool"
                  ? ip
                  : null,
            pool_id: poolId || null,
          };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Сеть ВМ ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Режим *</span>
            <select
              className="input"
              value={mode}
              onChange={(e) => setMode(e.target.value as VmNetworkMode)}
            >
              <option value="bridge">bridge (static IP из пула)</option>
              <option value="nat">nat (libvirt)</option>
            </select>
          </label>
          {mode === "bridge" && (
            <PoolIpPicker
              mock={mock}
              departmentId={vm.department_id}
              serverId={vm.hub_server_id}
              poolId={poolId}
              onPoolChange={setPoolId}
              ipMode={ipMode}
              onIpModeChange={setIpMode}
              ip={ip}
              onIpChange={setIp}
            />
          )}
          {mode === "nat" && (
            <div className="text-xs text-dim">
              Адрес назначит libvirt (DHCP), IP читается через{" "}
              <span className="mono">domifaddr</span>.
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid}
          >
            {submitting ? "Применяем…" : "Применить сеть"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── IPAM: пулы ──────────────────────────────────────────────────────────────────

function IpPoolsPane({ mock }: { mock: boolean }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<VmIpPool | null>(null);

  const poolsQ = useQuery<VmIpPool[]>(
    async () => {
      if (mock) return MOCK_VM_IP_POOLS;
      const res = await listVmIpPools();
      return res.items;
    },
    [mock],
    { keepPreviousDataOnError: true },
  );

  const [mockPools, setMockPools] = useState<VmIpPool[] | null>(null);
  const pools = mock ? (mockPools ?? poolsQ.data ?? []) : (poolsQ.data ?? []);

  async function handleCreate(body: VmIpPoolCreateRequest) {
    try {
      if (mock) {
        const next: VmIpPool = { id: `pool-mock-${Date.now()}`, ...body };
        setMockPools([...(mockPools ?? poolsQ.data ?? []), next]);
      } else {
        await createVmIpPool(body);
        poolsQ.refetch();
      }
      toast.success(`Пул ${body.name} создан`);
      setCreateOpen(false);
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание пула не удалось"));
    }
  }

  async function handleUpdate(id: string, body: VmIpPoolUpdateRequest) {
    try {
      if (mock) {
        setMockPools(
          (mockPools ?? poolsQ.data ?? []).map((p) =>
            p.id === id ? { ...p, ...body } : p,
          ),
        );
      } else {
        await updateVmIpPool(id, body);
        poolsQ.refetch();
      }
      toast.success("Пул обновлён");
      setEditTarget(null);
    } catch (e) {
      toast.error(apiErrMsg(e, "Изменение пула не удалось"));
    }
  }

  async function handleDelete(pool: VmIpPool) {
    const ok = await confirm({
      title: "Удалить пул",
      message: `Удалить IP-пул ${pool.name} (${pool.cidr})? Выданные адреса останутся на ВМ, но новые из него выделяться не будут.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      if (mock) {
        setMockPools(
          (mockPools ?? poolsQ.data ?? []).filter((p) => p.id !== pool.id),
        );
      } else {
        await deleteVmIpPool(pool.id);
        poolsQ.refetch();
      }
      toast.success(`Пул ${pool.name} удалён`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление пула не удалось"));
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <Waypoints className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold">IP-пулы (IPAM)</h1>
          <div className="text-sm text-dim mt-1">
            Диапазоны статических адресов для bridge-ВМ. Привязка — отдел, с
            override на конкретный хаб.
          </div>
        </div>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={() => setCreateOpen(true)}
        >
          <Plus className="w-4 h-4" /> Создать пул
        </button>
      </div>

      <div className="p-5">
        {poolsQ.loading ? (
          <div className="text-xs text-dim">Загрузка…</div>
        ) : poolsQ.error && pools.length === 0 ? (
          <div className="alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(poolsQ.error, "Список пулов не загрузился")}</div>
              <button className="btn btn-ghost mt-2" onClick={() => poolsQ.refetch()}>
                Повторить
              </button>
            </div>
          </div>
        ) : pools.length === 0 ? (
          <div className="empty-card text-center text-sm text-dim">
            Пулов пока нет. Создайте первый, чтобы выделять адреса bridge-ВМ.
          </div>
        ) : (
          <div className="surface-2 border border-token rounded overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase text-dim border-b border-token">
                  <th className="text-left px-3 py-2 font-medium">Имя</th>
                  <th className="text-left px-3 py-2 font-medium">CIDR</th>
                  <th className="text-left px-3 py-2 font-medium">Шлюз</th>
                  <th className="text-left px-3 py-2 font-medium">Диапазон</th>
                  <th className="text-left px-3 py-2 font-medium">DNS</th>
                  <th className="text-left px-3 py-2 font-medium">Отдел / хаб</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {pools.map((p) => (
                  <tr key={p.id} className="border-b border-token last:border-b-0">
                    <td className="px-3 py-1.5">{p.name}</td>
                    <td className="px-3 py-1.5 mono text-xs">{p.cidr}</td>
                    <td className="px-3 py-1.5 mono text-xs">{p.gateway}</td>
                    <td className="px-3 py-1.5 mono text-xs">
                      {p.range_start} – {p.range_end}
                    </td>
                    <td className="px-3 py-1.5 mono text-xs text-dim">
                      {p.dns.join(", ") || "—"}
                    </td>
                    <td className="px-3 py-1.5 text-xs">
                      {p.department_id}
                      {p.server_id ? (
                        <span className="badge ml-1 text-[11px]">хаб {p.server_id}</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1 justify-end">
                        <button
                          className="btn btn-sm flex items-center gap-1"
                          title="Изменить пул"
                          onClick={() => setEditTarget(p)}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          className="btn btn-sm btn-danger flex items-center gap-1"
                          title="Удалить пул"
                          onClick={() => handleDelete(p)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {createOpen && (
        <IpPoolModal onClose={() => setCreateOpen(false)} onSubmit={handleCreate} />
      )}
      {editTarget && (
        <IpPoolModal
          pool={editTarget}
          onClose={() => setEditTarget(null)}
          onSubmit={(body) => handleUpdate(editTarget.id, body)}
        />
      )}
    </section>
  );
}

/**
 * Модалка создания/редактирования IPAM-пула. Без пула на входе — режим
 * создания (нужен department_id); с пулом — редактирование (department_id не
 * меняем).
 */
function IpPoolModal({
  pool,
  onClose,
  onSubmit,
}: {
  pool?: VmIpPool;
  onClose: () => void;
  onSubmit: (body: VmIpPoolCreateRequest) => void | Promise<void>;
}) {
  const editing = !!pool;
  const [name, setName] = useState(pool?.name ?? "");
  const [cidr, setCidr] = useState(pool?.cidr ?? "");
  const [gateway, setGateway] = useState(pool?.gateway ?? "");
  const [netmask, setNetmask] = useState(pool?.netmask ?? "255.255.255.0");
  const [dns, setDns] = useState((pool?.dns ?? []).join(", "));
  const [rangeStart, setRangeStart] = useState(pool?.range_start ?? "");
  const [rangeEnd, setRangeEnd] = useState(pool?.range_end ?? "");
  const [departmentId, setDepartmentId] = useState(pool?.department_id ?? "");
  const [serverId, setServerId] = useState(pool?.server_id ?? "");
  const [submitting, setSubmitting] = useState(false);

  const cidrError =
    cidr.trim() && !/^\d{1,3}(\.\d{1,3}){3}\/\d{1,2}$/.test(cidr.trim())
      ? "Ожидается CIDR, напр. 10.177.103.0/24"
      : null;
  const gwError =
    gateway.trim() && !isLikelyIpv4(gateway.trim())
      ? "Ожидается IPv4-адрес"
      : null;
  const rangeError =
    (rangeStart.trim() && !isLikelyIpv4(rangeStart.trim())) ||
    (rangeEnd.trim() && !isLikelyIpv4(rangeEnd.trim()))
      ? "Границы диапазона — IPv4-адреса"
      : null;
  const valid =
    !!name.trim() &&
    !!cidr.trim() &&
    !cidrError &&
    !!gateway.trim() &&
    !gwError &&
    !!rangeStart.trim() &&
    !!rangeEnd.trim() &&
    !rangeError &&
    (editing || !!departmentId.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    const body: VmIpPoolCreateRequest = {
      name: name.trim(),
      cidr: cidr.trim(),
      gateway: gateway.trim(),
      netmask: netmask.trim(),
      dns: dns
        .split(",")
        .map((d) => d.trim())
        .filter(Boolean),
      range_start: rangeStart.trim(),
      range_end: rangeEnd.trim(),
      department_id: departmentId.trim(),
      server_id: serverId.trim() ? serverId.trim() : null,
    };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={editing ? `Пул ${pool!.name}` : "Новый IP-пул"} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="core-lan"
              autoFocus
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">CIDR *</span>
              <input
                className="input"
                value={cidr}
                onChange={(e) => setCidr(e.target.value)}
                placeholder="10.177.103.0/24"
              />
              {cidrError && <span className="text-[11px] text-danger">{cidrError}</span>}
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Маска</span>
              <input
                className="input"
                value={netmask}
                onChange={(e) => setNetmask(e.target.value)}
                placeholder="255.255.255.0"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Шлюз *</span>
            <input
              className="input"
              value={gateway}
              onChange={(e) => setGateway(e.target.value)}
              placeholder="10.177.103.1"
            />
            {gwError && <span className="text-[11px] text-danger">{gwError}</span>}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Начало диапазона *</span>
              <input
                className="input"
                value={rangeStart}
                onChange={(e) => setRangeStart(e.target.value)}
                placeholder="10.177.103.50"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Конец диапазона *</span>
              <input
                className="input"
                value={rangeEnd}
                onChange={(e) => setRangeEnd(e.target.value)}
                placeholder="10.177.103.99"
              />
            </label>
          </div>
          {rangeError && <span className="text-[11px] text-danger">{rangeError}</span>}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">DNS (через запятую)</span>
            <input
              className="input"
              value={dns}
              onChange={(e) => setDns(e.target.value)}
              placeholder="10.177.100.10, 8.8.8.8"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">
                Отдел {editing ? "" : "*"}
              </span>
              <input
                className="input"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                placeholder="core"
                disabled={editing}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Override-хаб (server_id)</span>
              <input
                className="input"
                value={serverId ?? ""}
                onChange={(e) => setServerId(e.target.value)}
                placeholder="опционально"
              />
            </label>
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={!valid || submitting}>
            {submitting ? "Сохраняем…" : editing ? "Сохранить" : "Создать пул"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── пресеты стандартных ВМ ──────────────────────────────────────────────────────

function PresetsPane({ mock }: { mock: boolean }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<VmPreset | null>(null);

  const presetsQ = useQuery<VmPreset[]>(
    async () => {
      if (mock) return MOCK_VM_PRESETS;
      const res = await listVmPresets();
      return res.items;
    },
    [mock],
    { keepPreviousDataOnError: true },
  );

  const [mockPresets, setMockPresets] = useState<VmPreset[] | null>(null);
  const presets = mock
    ? (mockPresets ?? presetsQ.data ?? [])
    : (presetsQ.data ?? []);

  async function handleCreate(body: VmPresetCreateRequest) {
    try {
      if (mock) {
        const next: VmPreset = {
          id: `preset-mock-${Date.now()}`,
          name: body.name,
          department_id: body.department_id,
          box: body.box,
          os_version: body.os_version ?? null,
          cpu: body.cpu,
          ram_mb: body.ram_mb,
          disk_gb: body.disk_gb,
          network_mode: body.network_mode,
          fixed_ip: body.fixed_ip ?? null,
          number: body.number ?? null,
        };
        setMockPresets([...(mockPresets ?? presetsQ.data ?? []), next]);
      } else {
        await createVmPreset(body);
        presetsQ.refetch();
      }
      toast.success(`Пресет ${body.name} создан`);
      setCreateOpen(false);
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание пресета не удалось"));
    }
  }

  async function handleUpdate(id: string, body: VmPresetUpdateRequest) {
    try {
      if (mock) {
        setMockPresets(
          (mockPresets ?? presetsQ.data ?? []).map((p) =>
            p.id === id ? { ...p, ...body } : p,
          ),
        );
      } else {
        await updateVmPreset(id, body);
        presetsQ.refetch();
      }
      toast.success("Пресет обновлён");
      setEditTarget(null);
    } catch (e) {
      toast.error(apiErrMsg(e, "Изменение пресета не удалось"));
    }
  }

  async function handleDelete(preset: VmPreset) {
    const ok = await confirm({
      title: "Удалить пресет",
      message: `Удалить пресет ${preset.name}? Уже развёрнутые из него ВМ не затрагиваются.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      if (mock) {
        setMockPresets(
          (mockPresets ?? presetsQ.data ?? []).filter((p) => p.id !== preset.id),
        );
      } else {
        await deleteVmPreset(preset.id);
        presetsQ.refetch();
      }
      toast.success(`Пресет ${preset.name} удалён`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление пресета не удалось"));
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <Layers className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold">Пресеты стандартных ВМ</h1>
          <div className="text-sm text-dim mt-1">
            Шаблоны для кнопки «Развернуть стандартные ВМ» на хабе. Bridge-пресет
            со статикой — один раз глобально, NAT-пресет — один раз на хаб.
          </div>
        </div>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={() => setCreateOpen(true)}
        >
          <Plus className="w-4 h-4" /> Создать пресет
        </button>
      </div>

      <div className="p-5">
        {presetsQ.loading ? (
          <div className="text-xs text-dim">Загрузка…</div>
        ) : presetsQ.error && presets.length === 0 ? (
          <div className="alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(presetsQ.error, "Список пресетов не загрузился")}</div>
              <button className="btn btn-ghost mt-2" onClick={() => presetsQ.refetch()}>
                Повторить
              </button>
            </div>
          </div>
        ) : presets.length === 0 ? (
          <div className="empty-card text-center text-sm text-dim">
            Пресетов пока нет. Создайте первый, чтобы разворачивать стандартные ВМ.
          </div>
        ) : (
          <div className="surface-2 border border-token rounded overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase text-dim border-b border-token">
                  <th className="text-left px-3 py-2 font-medium">Имя</th>
                  <th className="text-left px-3 py-2 font-medium">ОС / box</th>
                  <th className="text-left px-3 py-2 font-medium">Ресурсы</th>
                  <th className="text-left px-3 py-2 font-medium">Сеть</th>
                  <th className="text-left px-3 py-2 font-medium">Отдел</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {presets.map((p) => (
                  <tr key={p.id} className="border-b border-token last:border-b-0">
                    <td className="px-3 py-1.5">{p.name}</td>
                    <td className="px-3 py-1.5 text-xs">
                      {p.os_version ?? "—"} · <span className="text-dim">{p.box}</span>
                    </td>
                    <td className="px-3 py-1.5 text-xs mono text-dim">
                      {p.cpu} vCPU · {Math.round(p.ram_mb / 1024)} ГБ · {p.disk_gb} ГБ
                    </td>
                    <td className="px-3 py-1.5 text-xs">
                      <span className="badge">{p.network_mode}</span>{" "}
                      {p.network_mode === "bridge" && (
                        <span className="mono">{p.fixed_ip ?? "авто"}</span>
                      )}
                      {p.number != null && (
                        <span className="text-dim"> · №{p.number}</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-xs">{p.department_id}</td>
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1 justify-end">
                        <button
                          className="btn btn-sm flex items-center gap-1"
                          title="Изменить пресет"
                          onClick={() => setEditTarget(p)}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          className="btn btn-sm btn-danger flex items-center gap-1"
                          title="Удалить пресет"
                          onClick={() => handleDelete(p)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {createOpen && (
        <PresetModal onClose={() => setCreateOpen(false)} onSubmit={handleCreate} />
      )}
      {editTarget && (
        <PresetModal
          preset={editTarget}
          onClose={() => setEditTarget(null)}
          onSubmit={(body) => handleUpdate(editTarget.id, body)}
        />
      )}
    </section>
  );
}

/**
 * Модалка создания/редактирования пресета. Без пресета — режим создания (нужен
 * department_id); с пресетом — редактирование (department_id не меняем).
 */
function PresetModal({
  preset,
  onClose,
  onSubmit,
}: {
  preset?: VmPreset;
  onClose: () => void;
  onSubmit: (body: VmPresetCreateRequest) => void | Promise<void>;
}) {
  const editing = !!preset;
  const [name, setName] = useState(preset?.name ?? "");
  const [box, setBox] = useState(preset?.box ?? "vm_station");
  const [osVersion, setOsVersion] = useState(preset?.os_version ?? "");
  const [cpu, setCpu] = useState(String(preset?.cpu ?? 2));
  const [ramMb, setRamMb] = useState(String(preset?.ram_mb ?? 4096));
  const [diskGb, setDiskGb] = useState(String(preset?.disk_gb ?? 40));
  const [networkMode, setNetworkMode] = useState<VmNetworkMode>(
    preset?.network_mode ?? "bridge",
  );
  const [fixedIp, setFixedIp] = useState(preset?.fixed_ip ?? "");
  const [number, setNumber] = useState(
    preset?.number != null ? String(preset.number) : "",
  );
  const [departmentId, setDepartmentId] = useState(preset?.department_id ?? "");
  const [submitting, setSubmitting] = useState(false);

  const cpuN = Number.parseInt(cpu, 10);
  const ramN = Number.parseInt(ramMb, 10);
  const diskN = Number.parseInt(diskGb, 10);
  const numberN = number.trim() ? Number.parseInt(number.trim(), 10) : null;

  const nameError =
    name.trim() && !/^[a-zA-Z0-9._-]+$/.test(name.trim())
      ? "Имя: латиница, цифры, точка, дефис, подчёркивание"
      : null;
  const ipError =
    networkMode === "bridge" && fixedIp.trim() && !isLikelyIpv4(fixedIp.trim())
      ? "Ожидается IPv4-адрес"
      : null;
  const valid =
    !!name.trim() &&
    !nameError &&
    !!box.trim() &&
    Number.isFinite(cpuN) &&
    cpuN > 0 &&
    Number.isFinite(ramN) &&
    ramN >= 256 &&
    Number.isFinite(diskN) &&
    diskN > 0 &&
    !ipError &&
    (editing || !!departmentId.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    const bridge = networkMode === "bridge";
    const body: VmPresetCreateRequest = {
      name: name.trim(),
      department_id: departmentId.trim(),
      box: box.trim(),
      os_version: osVersion.trim() ? osVersion.trim() : null,
      cpu: cpuN,
      ram_mb: ramN,
      disk_gb: diskN,
      network_mode: networkMode,
      fixed_ip: bridge && fixedIp.trim() ? fixedIp.trim() : null,
      number: numberN,
    };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={editing ? `Пресет ${preset!.name}` : "Новый пресет ВМ"} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="core-rc-bridge"
              autoFocus
            />
            {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Box (образ) *</span>
              <input
                className="input"
                value={box}
                onChange={(e) => setBox(e.target.value)}
                placeholder="vm_station"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Версия ОС</span>
              <input
                className="input"
                value={osVersion}
                onChange={(e) => setOsVersion(e.target.value)}
                placeholder="1.8.1.6 (опц.)"
              />
            </label>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">vCPU *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={cpu}
                onChange={(e) => setCpu(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">RAM, МБ *</span>
              <input
                className="input"
                type="number"
                min={256}
                step={256}
                value={ramMb}
                onChange={(e) => setRamMb(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Диск, ГБ *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={diskGb}
                onChange={(e) => setDiskGb(e.target.value)}
              />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Сеть *</span>
            <select
              className="input"
              value={networkMode}
              onChange={(e) => setNetworkMode(e.target.value as VmNetworkMode)}
            >
              <option value="bridge">bridge (static IP)</option>
              <option value="nat">nat (libvirt)</option>
            </select>
          </label>
          {networkMode === "bridge" && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Статический IP (fixed_ip)</span>
              <input
                className="input"
                value={fixedIp}
                onChange={(e) => setFixedIp(e.target.value)}
                placeholder="10.177.103.60 (опц.)"
              />
              {ipError && <span className="text-[11px] text-danger">{ipError}</span>}
            </label>
          )}
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Номер (глоб. уникальный)</span>
              <input
                className="input"
                type="number"
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder="опционально"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Отдел {editing ? "" : "*"}</span>
              <input
                className="input"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                placeholder="core"
                disabled={editing}
              />
            </label>
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={!valid || submitting}>
            {submitting ? "Сохраняем…" : editing ? "Сохранить" : "Создать пресет"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── modals ────────────────────────────────────────────────────────────────────

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" aria-describedby={undefined}>
          <div className="modal-header">
            <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ResourcesModal({
  vm,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  onClose: () => void;
  onSubmit: (body: VmUpdateRequest) => void | Promise<void>;
}) {
  const [cpu, setCpu] = useState(String(vm.cpu));
  const [ramMb, setRamMb] = useState(String(vm.ram_mb));
  const [submitting, setSubmitting] = useState(false);

  const cpuN = Number.parseInt(cpu, 10);
  const ramN = Number.parseInt(ramMb, 10);
  const valid =
    Number.isFinite(cpuN) && cpuN > 0 && Number.isFinite(ramN) && ramN >= 256;
  const changed = cpuN !== vm.cpu || ramN !== vm.ram_mb;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || !changed || submitting) return;
    const body: VmUpdateRequest = {};
    if (cpuN !== vm.cpu) body.cpu = cpuN;
    if (ramN !== vm.ram_mb) body.ram_mb = ramN;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Ресурсы ВМ ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Изменение остановит ВМ, применит новые значения и запустит её заново.
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">vCPU *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={cpu}
                onChange={(e) => setCpu(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">RAM, МБ *</span>
              <input
                className="input"
                type="number"
                min={256}
                step={256}
                value={ramMb}
                onChange={(e) => setRamMb(e.target.value)}
              />
            </label>
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || !changed || submitting}
          >
            {submitting ? "Применяем…" : "Применить"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DiskCreateModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (body: VmDiskCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [sizeGb, setSizeGb] = useState("20");
  const [fs, setFs] = useState("ext4");
  const [mount, setMount] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const sizeN = Number.parseInt(sizeGb, 10);
  const nameError =
    name.trim() && !/^[a-zA-Z0-9._-]+$/.test(name.trim())
      ? "Имя: латиница, цифры, точка, дефис, подчёркивание"
      : null;
  const valid = !!name.trim() && !nameError && Number.isFinite(sizeN) && sizeN > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    const body: VmDiskCreateRequest = {
      name: name.trim(),
      size_gb: sizeN,
      fs: fs === "none" ? null : fs,
      mount: mount.trim() ? mount.trim() : null,
    };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Новый диск" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="data"
              autoFocus
            />
            {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Размер, ГБ *</span>
            <input
              className="input"
              type="number"
              min={1}
              value={sizeGb}
              onChange={(e) => setSizeGb(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Файловая система</span>
            <select className="input" value={fs} onChange={(e) => setFs(e.target.value)}>
              <option value="ext4">ext4</option>
              <option value="xfs">xfs</option>
              <option value="btrfs">btrfs</option>
              <option value="none">не форматировать</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Точка монтирования</span>
            <input
              className="input"
              value={mount}
              onChange={(e) => setMount(e.target.value)}
              placeholder="/data (опционально)"
            />
          </label>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Создаём…" : "Создать диск"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DiskResizeModal({
  disk,
  onClose,
  onSubmit,
}: {
  disk: VmDisk;
  onClose: () => void;
  onSubmit: (sizeGb: number) => void | Promise<void>;
}) {
  const [sizeGb, setSizeGb] = useState(String(disk.size_gb));
  const [submitting, setSubmitting] = useState(false);

  const sizeN = Number.parseInt(sizeGb, 10);
  const valid = Number.isFinite(sizeN) && sizeN > disk.size_gb;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(sizeN));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Resize диска ${disk.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Текущий размер: <b className="mono">{disk.size_gb} ГБ</b>. Диск можно
            только увеличить (`qemu-img resize` + growpart/resize2fs в госте).
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Новый размер, ГБ *</span>
            <input
              className="input"
              type="number"
              min={disk.size_gb + 1}
              value={sizeGb}
              onChange={(e) => setSizeGb(e.target.value)}
              autoFocus
            />
            {!valid && sizeGb.trim() !== "" && (
              <span className="text-[11px] text-danger">
                Должно быть больше {disk.size_gb} ГБ
              </span>
            )}
          </label>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Применяем…" : "Увеличить"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SnapshotCreateModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (body: VmSnapshotCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<VmSnapshotKind>("disk_only");
  const [submitting, setSubmitting] = useState(false);

  const nameError =
    name.trim() && !/^[a-zA-Z0-9._-]+$/.test(name.trim())
      ? "Имя: латиница, цифры, точка, дефис, подчёркивание"
      : null;
  // `_build`-суффикс зарезервирован под системные снимки — не даём его занять.
  const reservedError = /_build$/.test(name.trim())
    ? "Суффикс _build зарезервирован под системные снимки"
    : null;
  const valid = !!name.trim() && !nameError && !reservedError;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    const body: VmSnapshotCreateRequest = {
      name: name.trim(),
      description: description.trim() ? description.trim() : null,
      kind,
    };
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Новый снимок" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="pre-regress"
              autoFocus
            />
            {(nameError || reservedError) && (
              <span className="text-[11px] text-danger">
                {nameError ?? reservedError}
              </span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Описание</span>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="опционально"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Тип</span>
            <select
              className="input"
              value={kind}
              onChange={(e) => setKind(e.target.value as VmSnapshotKind)}
            >
              <option value="disk_only">disk-only (только диск)</option>
              <option value="full">full (диск + память/состояние)</option>
            </select>
          </label>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Создаём…" : "Создать снимок"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AstraUpdateModal({
  vm,
  mock,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  mock: boolean;
  onClose: () => void;
  onSubmit: (osVersionId: string, label: string) => void | Promise<void>;
}) {
  const versionsQ = useQuery<{ id: string; name: string }[]>(
    async () => {
      if (mock) return MOCK_VM_OS_VERSIONS;
      const res = await listOsVersions({ limit: 200 });
      return res.items.map((v: OsVersion) => ({ id: v.id, name: v.name }));
    },
    [mock],
    { keepPreviousDataOnError: true },
  );
  const versions = versionsQ.data ?? [];
  const [selected, setSelected] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const selectedLabel = versions.find((v) => v.id === selected)?.name ?? selected;
  const valid = !!selected;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(selected, selectedLabel));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Обновление ОС · ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Выберите целевую версию ОС (RC). Worker откатится на нужный{" "}
            <span className="mono">_build</span>-снимок, перезапишет sources.list
            репозиториями версии, выполнит{" "}
            <span className="mono">astra-update -A -T -r</span> и переснимет
            снимок под новую версию.
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Версия ОС *</span>
            {versionsQ.loading ? (
              <div className="text-xs text-dim">Загрузка каталога…</div>
            ) : (
              <select
                className="input"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                autoFocus
              >
                <option value="">— выберите версию —</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
          </label>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Запускаем…" : "Обновить ОС"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PasswdModal({
  vm,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  onClose: () => void;
  onSubmit: (password: string) => void | Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const tooShort = password.length > 0 && password.length < 8;
  const mismatch = confirmPw.length > 0 && confirmPw !== password;
  const valid = password.length >= 8 && confirmPw === password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(password));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Смена пароля · ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Новый пароль учётки <span className="mono">u</span>. По режиму{" "}
            <b>{vm.cred_strategy}</b> worker либо сохранит его в снимковых кредах,
            либо перекатает по всем не-«_build» снимкам.
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Новый пароль *</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
            {tooShort && (
              <span className="text-[11px] text-danger">Минимум 8 символов</span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Повтор пароля *</span>
            <input
              className="input"
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
            />
            {mismatch && (
              <span className="text-[11px] text-danger">Пароли не совпадают</span>
            )}
          </label>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Применяем…" : "Сменить пароль"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function isLikelyIpv4(value: string): boolean {
  return /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(value);
}
