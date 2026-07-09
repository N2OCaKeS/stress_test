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
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  MonitorPlay,
  Pencil,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Layers,
  Users,
  XCircle,
  Trash2,
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
import { EntityHeader } from "@/components/entity/EntityHeader";
import { ReachSignal, PowerStateBadge } from "@/components/entity/signals";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { listServers } from "@/api/server/servers";
import {
  createDefaultVms,
  createVmsBulk,
  createVmIpPool,
  createVmPreset,
  deleteVmIpPool,
  deleteVmPreset,
  getAvailableIps,
  getVmByNumber,
  getServerByNumber,
  listVmImages,
  listVmIpPools,
  listVmPresets,
  listVms,
  refreshVmImages,
  releaseVm,
  reserveVm,
  serversToVmHubs,
  updateVmIpPool,
  updateVmPreset,
  type Vm,
  type VmBulkCreateResponse,
  type VmBulkItemResult,
  type VmCreateRequest,
  type VmCredStrategy,
  type VmHub,
  type VmImage,
  type VmIpPool,
  type VmIpPoolCreateRequest,
  type VmIpPoolUpdateRequest,
  type VmNetworkMode,
  type VmPreset,
  type VmPresetCreateRequest,
  type VmPresetUpdateRequest,
} from "@/api/server/vms";
import { listAccounts } from "@/api/server/accounts";
import type { ServerAccount, TaskDispatchResponse } from "@/api/server/types";
import {
  MOCK_AVAILABLE_IPS,
  MOCK_VM_ACCOUNTS,
  MOCK_VM_HUBS,
  MOCK_VM_IMAGES,
  MOCK_VM_IP_POOLS,
  MOCK_VM_PRESETS,
  MOCK_VMS,
} from "@/mocks/vm";
import { TAB_ICON } from "@/pages/server/tabs/_tabMeta";
import type { EntityRef } from "@/pages/server/tabs/_entity";
import { OverviewTab } from "@/pages/server/tabs/overview";
import { HardwareTab } from "@/pages/server/tabs/hardware";
import { PowerTab } from "@/pages/server/tabs/power";
import { AccountsTab } from "@/pages/server/tabs/accounts";
import { ConsoleTab } from "@/pages/server/tabs/console";
import { PackagesTab } from "@/pages/server/tabs/packages";
import { ManageTab } from "@/pages/server/tabs/manage";
import { DisksTab } from "@/pages/server/tabs/disks";
import { SnapshotsTab } from "@/pages/server/tabs/snapshots";

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
      const hubs = serversToVmHubs(srv.items, vmsPage.items);
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

  async function handleCreateBulk(
    items: VmCreateRequest[],
  ): Promise<VmBulkCreateResponse> {
    if (mock) {
      return {
        results: items.map((it) => ({
          name: it.name,
          status: "queued" as const,
          task_id: `task-mock-${Math.random().toString(36).slice(2, 8)}`,
        })),
      };
    }
    return createVmsBulk({ items });
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
          onSubmit={handleCreateBulk}
          onChanged={() => hubsAndVmsQ.refetch()}
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
  | "hardware"
  | "power"
  | "accounts"
  | "console"
  | "packages"
  | "manage"
  | "disks"
  | "snapshots";

const VM_TABS: { id: VmTabId; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "hardware", label: "Железо" },
  { id: "power", label: "Питание" },
  { id: "accounts", label: "Аккаунты" },
  { id: "console", label: "Консоль" },
  { id: "packages", label: "Пакеты" },
  { id: "manage", label: "Управление" },
  { id: "disks", label: "Диски" },
  { id: "snapshots", label: "Снимки" },
];

/**
 * Карточка ВМ — тонкий диспетчер вкладок по образцу `ServerDetail`. Рендерит те
 * же файлы-компоненты вкладок, что и карточка сервера, передавая им сущность-ВМ
 * через `EntityRef`. Своё здесь — только общая шапка (имя/питание/бронь) и
 * локальная копия карточки, чтобы мутации во вкладках поднимались в header и в
 * соседние вкладки без перезагрузки. Набор вкладок фиксирован; гейтинг по праву
 * управления живёт внутри самих вкладок (power/manage), как у сервера.
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
  const deptLabel = useDeptLabel(vm.department_id);
  // Свежая копия карточки: vm-проп меняется при refetch списка, локальные
  // мутации во вкладках (PATCH, бронь) поднимаются сюда через setLocal.
  const [local, setLocal] = useState<Vm>(vm);
  const view = local.id === vm.id ? local : vm;

  const entity: EntityRef = { kind: "vm", vm: view, mock, canManage, onChanged };
  const onVmUpdated = (next: import("@/api/server/types").Server | Vm) =>
    setLocal(next as Vm);

  const activeTab = VM_TABS.some((t) => t.id === tab) ? tab : "overview";

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <EntityHeader
        icon={<MonitorPlay className="w-7 h-7" />}
        onBack={onBack}
        backLabel="К хабу"
        name={view.name}
        badges={
          <>
            <span className="badge">ВМ</span>
            <ReachSignal
              label="ping"
              reachable={view.ping_reachable}
              latencyMs={view.ping_latency_ms}
            />
            {/* Состояние питания домена снимается из virsh. */}
            <PowerStateBadge state={view.power_state} />
          </>
        }
        meta={
          <>
            <span className="mono">{view.id}</span>
            <span>·</span>
            <span>
              № <b className="mono">{view.number ?? "—"}</b>
            </span>
            <span>·</span>
            <span>
              dept: <b>{deptLabel}</b>
            </span>
          </>
        }
      >
        <VmReserveControl
          vm={view}
          mock={mock}
          canManage={canManage}
          onLocal={setLocal}
          onChanged={onChanged}
        />
      </EntityHeader>

      <Tabs
        active={activeTab}
        onChange={(id) => setTab(id as VmTabId)}
        wrap
        tabs={VM_TABS.map((t) => ({
          id: t.id,
          label: t.label,
          icon: TAB_ICON[t.id],
        }))}
      />

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-auto">
        {activeTab === "overview" && (
          <OverviewTab entity={entity} onEntityUpdated={onVmUpdated} />
        )}
        {activeTab === "hardware" && (
          <HardwareTab serverId="" entity={entity} />
        )}
        {activeTab === "power" && (
          <PowerTab entity={entity} onEntityUpdated={onVmUpdated} />
        )}
        {activeTab === "accounts" && (
          <AccountsTab serverId="" entity={entity} />
        )}
        {activeTab === "console" && <ConsoleTab entity={entity} />}
        {activeTab === "packages" && (
          <PackagesTab serverId="" entity={entity} />
        )}
        {activeTab === "manage" && (
          <ManageTab
            serverId=""
            entity={entity}
            onEntityUpdated={onVmUpdated}
            onDeleted={onBack}
          />
        )}
        {activeTab === "disks" && <DisksTab entity={entity} />}
        {activeTab === "snapshots" && (
          <SnapshotsTab entity={entity} onEntityUpdated={onVmUpdated} />
        )}
      </div>
    </section>
  );
}

/**
 * Бронь ВМ в шапке карточки — индикатор «Забронировано» и кнопки
 * «Забронировать»/«Снять бронь». Оптимистично правит локальную копию и дёргает
 * `onChanged`, чтобы список хаба обновил индикатор. Право на бронь приходит
 * сверху одним флагом: у ВМ нет per-операционной матрицы прав, как у сервера.
 */
function VmReserveControl({
  vm,
  mock,
  canManage,
  onLocal,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onLocal: (next: Vm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { prompt, confirm } = useConfirm();
  const [pending, setPending] = useState(false);
  const reserved = vm.busy_state !== "free" || !!vm.busy_note;

  async function handleReserve() {
    if (pending || !canManage) return;
    const { ok, reason } = await prompt({
      title: "Забронировать ВМ",
      message: `Забронировать ${vm.name}? Бронь блокирует деструктивные операции других пользователей до её снятия.`,
      reason: true,
      reasonLabel: "Примечание (зачем бронь)",
      reasonPlaceholder: "например, ручной debug-цикл",
      reasonRequired: true,
      confirmLabel: "Забронировать",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? {
            ...vm,
            busy_state: "busy",
            busy_note: reason.trim(),
            status: reason.trim(),
          }
        : await reserveVm(vm.id, { reason: reason.trim() });
      onLocal(next as Vm);
      onChanged();
      toast.success(`ВМ ${vm.name} забронирована`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось забронировать"));
    } finally {
      setPending(false);
    }
  }

  async function handleRelease() {
    if (pending || !canManage) return;
    const ok = await confirm({
      title: "Снять бронь",
      message: `Снять бронь с ${vm.name}?`,
      confirmLabel: "Снять бронь",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? { ...vm, busy_state: "free", busy_note: null, status: "free" }
        : await releaseVm(vm.id);
      onLocal(next as Vm);
      onChanged();
      toast.success(`Бронь с ${vm.name} снята`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось снять бронь"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mt-3 flex items-center gap-3 flex-wrap">
      {reserved ? (
        <>
          <span className="badge badge-warn flex items-center gap-1">
            <Lock className="w-3.5 h-3.5" /> Забронировано
          </span>
          {vm.busy_note && (
            <span className="text-xs text-dim truncate max-w-[320px]">
              {vm.busy_note}
            </span>
          )}
          {canManage && (
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              onClick={handleRelease}
              disabled={pending}
              title="Снять бронь"
            >
              <Unlock className="w-3.5 h-3.5" /> Снять бронь
            </button>
          )}
        </>
      ) : (
        canManage && (
          <button
            type="button"
            className="btn btn-sm btn-primary flex items-center gap-1"
            onClick={handleReserve}
            disabled={pending}
            title="Забронировать ВМ"
          >
            <Lock className="w-3.5 h-3.5" /> Забронировать
          </button>
        )
      )}
    </div>
  );
}

// ── create VM (батч) ─────────────────────────────────────────────────────────

/** Состояние одного блока прогрессивной формы создания ВМ. */
interface VmBlockData {
  key: string;
  name: string;
  hostname: string;
  cpu: string;
  ramMb: string;
  diskGb: string;
  box: string;
  networkMode: VmNetworkMode;
  poolId: string;
  ipMode: "auto" | "pool" | "manual";
  ip: string;
  autostart: boolean;
  credStrategy: VmCredStrategy;
  accountIds: string[];
  collapsed: boolean;
}

let vmBlockSeq = 0;
function newVmBlock(box: string): VmBlockData {
  vmBlockSeq += 1;
  return {
    key: `vmblk-${vmBlockSeq}`,
    name: "",
    hostname: "",
    cpu: "2",
    ramMb: "4096",
    diskGb: "40",
    box,
    networkMode: "bridge",
    poolId: "",
    ipMode: "auto",
    ip: "",
    autostart: false,
    credStrategy: "per_snapshot",
    accountIds: [],
    collapsed: false,
  };
}

interface VmBlockValidation {
  cpuN: number;
  ramN: number;
  diskN: number;
  minDisk: number | null;
  nameError: string | null;
  diskWarning: string | null;
  ipValid: boolean;
  valid: boolean;
}

function validateVmBlock(
  b: VmBlockData,
  images: VmImage[],
): VmBlockValidation {
  const cpuN = Number.parseInt(b.cpu, 10);
  const ramN = Number.parseInt(b.ramMb, 10);
  const diskN = Number.parseInt(b.diskGb, 10);
  const nameError =
    b.name.trim() && !/^[a-zA-Z0-9._-]+$/.test(b.name.trim())
      ? "Имя: латиница, цифры, точка, дефис, подчёркивание"
      : null;
  const img = images.find((im) => im.name === b.box) ?? null;
  const minDisk = img?.min_disk_gb ?? null;
  // Меньше минимума бокса — образ физически не поместится (virt-resize упадёт):
  // предупреждаем в форме до отправки и блокируем сабмит этого блока.
  const diskWarning =
    minDisk != null && Number.isFinite(diskN) && diskN < minDisk
      ? `Меньше минимума бокса (${minDisk} ГБ) — образ не поместится`
      : null;
  const ipValid =
    b.networkMode === "nat" ||
    b.ipMode === "auto" ||
    (b.ipMode === "pool" && !!b.ip) ||
    (b.ipMode === "manual" && isLikelyIpv4(b.ip.trim()));
  const valid =
    !!b.name.trim() &&
    !nameError &&
    Number.isFinite(cpuN) &&
    cpuN > 0 &&
    Number.isFinite(ramN) &&
    ramN > 0 &&
    Number.isFinite(diskN) &&
    diskN > 0 &&
    !diskWarning &&
    ipValid;
  return { cpuN, ramN, diskN, minDisk, nameError, diskWarning, ipValid, valid };
}

function vmBlockToItem(
  b: VmBlockData,
  hubId: string,
  v: VmBlockValidation,
): VmCreateRequest {
  const bridge = b.networkMode === "bridge";
  return {
    hub_server_id: hubId,
    name: b.name.trim(),
    hostname: b.hostname.trim() ? b.hostname.trim() : null,
    cpu: v.cpuN,
    ram_mb: v.ramN,
    disk_gb: v.diskN,
    box: b.box,
    network_mode: b.networkMode,
    ip_address: !bridge
      ? null
      : b.ipMode === "manual"
        ? b.ip.trim()
        : b.ipMode === "pool"
          ? b.ip
          : null,
    pool_id: bridge && b.poolId ? b.poolId : null,
    autostart: b.autostart,
    cred_strategy: b.credStrategy,
    accounts: b.accountIds,
  };
}

export function CreateVmPane({
  hub,
  images,
  mock,
  onRefreshImages,
  onCancel,
  onSubmit,
  onChanged,
}: {
  hub: VmHub;
  images: VmImage[];
  mock: boolean;
  onRefreshImages: () => void | Promise<void>;
  onCancel: () => void;
  onSubmit: (items: VmCreateRequest[]) => Promise<VmBulkCreateResponse>;
  onChanged: () => void;
}) {
  const toast = useToast();
  const defaultBox = images[0]?.name ?? "vm_station";
  const [blocks, setBlocks] = useState<VmBlockData[]>(() => [
    newVmBlock(defaultBox),
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<VmBulkItemResult[] | null>(null);

  const validations = blocks.map((b) => validateVmBlock(b, images));
  const allValid = validations.every((v) => v.valid);

  function patchBlock(key: string, patch: Partial<VmBlockData>) {
    setBlocks((prev) =>
      prev.map((b) => (b.key === key ? { ...b, ...patch } : b)),
    );
  }
  function toggleCollapse(key: string) {
    setBlocks((prev) =>
      prev.map((b) => (b.key === key ? { ...b, collapsed: !b.collapsed } : b)),
    );
  }
  function removeBlock(key: string) {
    setBlocks((prev) =>
      prev.length > 1 ? prev.filter((b) => b.key !== key) : prev,
    );
  }
  function addBlock() {
    // Сворачиваем всё заполненное и открываем свежий блок.
    setBlocks((prev) => [
      ...prev.map((b) => ({ ...b, collapsed: true })),
      newVmBlock(defaultBox),
    ]);
  }

  async function submitAll() {
    if (!allValid || submitting) return;
    const items = blocks.map((b, i) => vmBlockToItem(b, hub.id, validations[i]));
    setSubmitting(true);
    try {
      const res = await onSubmit(items);
      setResults(res.results);
      const failed = res.results.filter((r) => r.status === "failed").length;
      if (failed === 0) {
        toast.success(
          `Создание ${res.results.length} ВМ — задачи поставлены`,
        );
      } else {
        toast.warn(
          `Часть ВМ не создана: ${failed} из ${res.results.length}`,
        );
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Батч-создание ВМ не удалось"));
    } finally {
      setSubmitting(false);
    }
  }

  if (results) {
    return (
      <section className="flex-1 min-w-0 overflow-y-auto">
        <div className="p-5 w-full max-w-2xl">
          <div className="flex items-center gap-2 mb-4">
            <button
              className="btn btn-ghost flex items-center gap-1"
              onClick={onCancel}
              type="button"
            >
              <ArrowLeft className="w-4 h-4" /> К хабу
            </button>
            <div className="text-sm text-dim">
              Результат создания {results.length} ВМ
            </div>
          </div>
          <div className="surface-2 border border-token rounded">
            {results.map((r) => {
              const failed = r.status === "failed";
              return (
                <div
                  key={r.name}
                  className="px-3 py-2 flex items-center gap-2 border-b border-token last:border-b-0"
                >
                  {failed ? (
                    <XCircle className="w-4 h-4 text-danger shrink-0" />
                  ) : (
                    <CheckCircle2 className="w-4 h-4 text-ok shrink-0" />
                  )}
                  <span className="text-sm font-medium">{r.name}</span>
                  <span
                    className={`text-xs flex-1 truncate ${failed ? "text-danger" : "text-dim"}`}
                  >
                    {failed
                      ? (r.error ?? "не удалось")
                      : `задача ${r.task_id ?? "поставлена"}`}
                  </span>
                  <span className={`badge ${failed ? "badge-danger" : "badge-ok"}`}>
                    {failed ? "ошибка" : "создана"}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex items-center gap-2 mt-4">
            <button className="btn btn-primary" onClick={onCancel}>
              Готово
            </button>
            <button
              className="btn"
              onClick={() => {
                setResults(null);
                setBlocks([newVmBlock(defaultBox)]);
              }}
            >
              Создать ещё
            </button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 w-full max-w-2xl">
        <div className="flex items-center gap-2 mb-4">
          <button
            className="btn btn-ghost flex items-center gap-1"
            onClick={onCancel}
            type="button"
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </button>
          <div className="text-sm text-dim">
            Создание ВМ на хабе <b>{hub.display_name ?? hub.hostname}</b> ·
            блоков: {blocks.length}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          {blocks.map((b, i) => (
            <VmBlockForm
              key={b.key}
              index={i}
              block={b}
              validation={validations[i]}
              images={images}
              mock={mock}
              hub={hub}
              canRemove={blocks.length > 1}
              onPatch={(patch) => patchBlock(b.key, patch)}
              onToggle={() => toggleCollapse(b.key)}
              onRemove={() => removeBlock(b.key)}
              onRefreshImages={onRefreshImages}
            />
          ))}
        </div>

        <div className="flex items-center gap-2 mt-4 flex-wrap">
          <button
            type="button"
            className="btn flex items-center gap-1"
            onClick={addBlock}
          >
            <Plus className="w-4 h-4" /> Добавить ВМ
          </button>
          <div className="flex-1" />
          <button type="button" className="btn" onClick={onCancel}>
            Отмена
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!allValid || submitting}
            onClick={submitAll}
          >
            {submitting
              ? "Создаём…"
              : blocks.length > 1
                ? `Создать все (${blocks.length})`
                : "Создать все"}
          </button>
        </div>
      </div>
    </section>
  );
}

function VmBlockForm({
  index,
  block,
  validation,
  images,
  mock,
  hub,
  canRemove,
  onPatch,
  onToggle,
  onRemove,
  onRefreshImages,
}: {
  index: number;
  block: VmBlockData;
  validation: VmBlockValidation;
  images: VmImage[];
  mock: boolean;
  hub: VmHub;
  canRemove: boolean;
  onPatch: (patch: Partial<VmBlockData>) => void;
  onToggle: () => void;
  onRemove: () => void;
  onRefreshImages: () => void | Promise<void>;
}) {
  const v = validation;
  const selectedImage = images.find((im) => im.name === block.box) ?? null;
  const ramGb = Math.round((Number.parseInt(block.ramMb, 10) || 0) / 1024);
  const summary = `${block.box} · ${block.cpu} vCPU · ${ramGb} ГБ · ${block.diskGb} ГБ · ${block.networkMode}`;

  if (block.collapsed) {
    return (
      <div className="surface-2 border border-token rounded">
        <button
          type="button"
          onClick={onToggle}
          className="w-full text-left px-3 py-2 flex items-center gap-2 hover-bg"
        >
          <ChevronRight className="w-4 h-4 text-dim shrink-0" />
          <span className="badge shrink-0">ВМ {index + 1}</span>
          <span className="text-sm font-medium truncate">
            {block.name.trim() || "(без имени)"}
          </span>
          <span className="text-[11px] text-dim truncate flex-1">{summary}</span>
          {v.valid ? (
            <CheckCircle2 className="w-4 h-4 text-ok shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-warn shrink-0" />
          )}
        </button>
      </div>
    );
  }

  return (
    <div className="surface-2 border border-token rounded">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-token">
        <button
          type="button"
          onClick={onToggle}
          className="btn btn-ghost btn-sm flex items-center"
          title="Свернуть блок"
        >
          <ChevronDown className="w-4 h-4" />
        </button>
        <span className="badge shrink-0">ВМ {index + 1}</span>
        <span className="text-sm font-medium truncate flex-1">
          {block.name.trim() || "Новая ВМ"}
        </span>
        {v.valid ? (
          <CheckCircle2 className="w-4 h-4 text-ok shrink-0" />
        ) : (
          <AlertTriangle className="w-4 h-4 text-warn shrink-0" />
        )}
        {canRemove && (
          <button
            type="button"
            className="btn btn-ghost btn-sm text-danger flex items-center"
            onClick={onRemove}
            title="Убрать блок"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <div className="p-3 flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={block.name}
              onChange={(e) => onPatch({ name: e.target.value })}
              placeholder="alse-1.8-rc"
            />
            {v.nameError && (
              <span className="text-[11px] text-danger">{v.nameError}</span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Hostname</span>
            <input
              className="input"
              value={block.hostname}
              onChange={(e) => onPatch({ hostname: e.target.value })}
              placeholder="= имя ВМ если пусто"
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
              value={block.cpu}
              onChange={(e) => onPatch({ cpu: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">RAM, МБ *</span>
            <input
              className="input"
              type="number"
              min={256}
              step={256}
              value={block.ramMb}
              onChange={(e) => onPatch({ ramMb: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">
              Диск, ГБ *
              {v.minDisk != null ? ` (мин. ${v.minDisk})` : ""}
            </span>
            <input
              className="input"
              type="number"
              min={1}
              value={block.diskGb}
              onChange={(e) => onPatch({ diskGb: e.target.value })}
            />
          </label>
        </div>
        {v.diskWarning && (
          <div className="alert alert-warn flex items-center gap-2 text-xs">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{v.diskWarning}</span>
          </div>
        )}

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
          <select
            className="input"
            value={block.box}
            onChange={(e) => onPatch({ box: e.target.value })}
          >
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
              {selectedImage.min_disk_gb != null
                ? ` · мин. диск ${selectedImage.min_disk_gb} ГБ`
                : ""}
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Сеть *</span>
          <select
            className="input"
            value={block.networkMode}
            onChange={(e) =>
              onPatch({ networkMode: e.target.value as VmNetworkMode })
            }
          >
            <option value="bridge">bridge (static IP из пула)</option>
            <option value="nat">nat (libvirt)</option>
          </select>
        </label>

        {block.networkMode === "bridge" ? (
          <PoolIpPicker
            mock={mock}
            departmentId={hub.department_id}
            serverId={hub.id}
            poolId={block.poolId}
            onPoolChange={(poolId) => onPatch({ poolId })}
            ipMode={block.ipMode}
            onIpModeChange={(ipMode) => onPatch({ ipMode })}
            ip={block.ip}
            onIpChange={(ip) => onPatch({ ip })}
          />
        ) : (
          <div className="text-xs text-dim">
            NAT: адрес назначит libvirt (DHCP), пул не используется.
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={block.autostart}
              onChange={(e) => onPatch({ autostart: e.target.checked })}
            />
            <span>Автозапуск при старте хаба</span>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Режим управляющих кред</span>
            <select
              className="input"
              value={block.credStrategy}
              onChange={(e) =>
                onPatch({ credStrategy: e.target.value as VmCredStrategy })
              }
            >
              <option value="per_snapshot">per_snapshot (креды на снимок)</option>
              <option value="reroll">reroll (единый пароль)</option>
            </select>
          </label>
        </div>

        <AccountMultiSelect
          mock={mock}
          departmentId={hub.department_id}
          selected={block.accountIds}
          onChange={(accountIds) => onPatch({ accountIds })}
        />
      </div>
    </div>
  );
}

function AccountMultiSelect({
  mock,
  departmentId,
  selected,
  onChange,
}: {
  mock: boolean;
  departmentId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const accountsQ = useQuery<ServerAccount[]>(
    async () => {
      if (mock) {
        return MOCK_VM_ACCOUNTS.filter((a) => a.department_id === departmentId);
      }
      const res = await listAccounts({});
      const items = "items" in res ? res.items : [];
      return items.filter(
        (a) => a.is_active && a.department_id === departmentId,
      );
    },
    [mock, departmentId],
    { keepPreviousDataOnError: true },
  );
  const accounts = accountsQ.data ?? [];

  function toggle(id: string) {
    onChange(
      selected.includes(id)
        ? selected.filter((x) => x !== id)
        : [...selected, id],
    );
  }

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="text-dim text-xs flex items-center gap-1">
        <Users className="w-3.5 h-3.5" /> Учётки отдела (OS-юзеры в госте)
      </span>
      {accountsQ.loading ? (
        <div className="text-xs text-dim">Загрузка учёток…</div>
      ) : accounts.length === 0 ? (
        <div className="text-xs text-dim">Нет доступных учёток отдела.</div>
      ) : (
        <div className="surface border border-token rounded max-h-40 overflow-y-auto flex flex-col">
          {accounts.map((a) => (
            <label
              key={a.id}
              className="flex items-center gap-2 px-2 py-1 text-sm hover-bg cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selected.includes(a.id)}
                onChange={() => toggle(a.id)}
              />
              <span className="font-medium">{a.login}</span>
              {a.has_sudo && (
                <span className="badge text-[11px]">sudo</span>
              )}
              <span className="text-[11px] text-dim truncate">
                {a.unix_groups.join(", ")}
              </span>
            </label>
          ))}
        </div>
      )}
      {selected.length > 0 && (
        <span className="text-[11px] text-dim">Выбрано: {selected.length}</span>
      )}
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


function isLikelyIpv4(value: string): boolean {
  return /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(value);
}
