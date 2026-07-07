/**
 * Страница /vm — зона «Виртуализация».
 *
 * Раскладка как у /server: Shell + Aside (по-номеру lookup + список хабов +
 * кандидаты на prepare) + Workzone (список ВМ хаба или карточка ВМ).
 *
 * Волна 1 VM-менеджера: hub.prepare, список/создание/питание/бронь ВМ,
 * lookup по номеру, статус задач. Backend домена `vm` в разработке — в
 * mock-режиме данные берутся из `@/mocks/vm`, живой режим ходит в
 * `@/api/server/vms`. Тонкая матрица прав `vm.*` (дизайн §2) ещё не приходит в
 * persona — гейтим серверной ролью через `@/lib/rbac`.
 */
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  MonitorPlay,
  Play,
  Plus,
  Power,
  RotateCcw,
  Search,
  Server as ServerIcon,
  Square,
  Lock,
  Unlock,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useDeptLabel } from "@/lib/labels";
import {
  canManageVms,
  canPrepareVmsHub,
  hasVmZoneAccess,
} from "@/lib/rbac";
import { formatLatencyMs } from "@/pages/server/_serverShared";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { listServers } from "@/api/server/servers";
import {
  createVm,
  deleteVm,
  getVmByNumber,
  getServerByNumber,
  listVms,
  prepareVmsHub,
  releaseVm,
  reserveVm,
  vmPower,
  type Vm,
  type VmCreateRequest,
  type VmHub,
  type VmNetworkMode,
  type VmPowerAction,
} from "@/api/server/vms";
import type { TaskDispatchResponse } from "@/api/server/types";
import {
  MOCK_HUB_CANDIDATES,
  MOCK_VM_BOXES,
  MOCK_VM_HUBS,
  MOCK_VMS,
  type MockHubCandidate,
} from "@/mocks/vm";

// ── data helpers (mock ↔ live) ──────────────────────────────────────────────

interface HubCandidate {
  id: string;
  hostname: string;
  display_name: string | null;
  ip_address: string;
  department_id: string;
}

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
  const canPrepare = canPrepareVmsHub(persona);

  const selectedHubId = params.get("hub");
  const selectedVmId = params.get("id");
  const action = params.get("action"); // "new" | null

  // Хабы: derived из серверов с virtualization=true (+ счётчик ВМ). В
  // mock-режиме — фикстуры. Backend флага ещё не отдаёт (домен vm в работе).
  const hubsAndVmsQ = useQuery<{ hubs: VmHub[]; vms: Vm[]; candidates: HubCandidate[] }>(
    async () => {
      if (mock) {
        return {
          hubs: MOCK_VM_HUBS,
          vms: MOCK_VMS,
          candidates: MOCK_HUB_CANDIDATES as MockHubCandidate[],
        };
      }
      const [srv, vmsPage] = await Promise.all([
        listServers({ limit: 200 }),
        listVms({ limit: 500 }),
      ]);
      const counts = new Map<string, number>();
      for (const v of vmsPage.items) {
        counts.set(v.hub_server_id, (counts.get(v.hub_server_id) ?? 0) + 1);
      }
      const isHub = (s: (typeof srv.items)[number]) =>
        (s as { virtualization?: boolean }).virtualization === true;
      const hubs: VmHub[] = srv.items.filter(isHub).map((s) => ({
        id: s.id,
        hostname: s.hostname,
        display_name: s.display_name,
        ip_address: s.ip_address,
        department_id: s.department_id,
        vm_count: counts.get(s.id) ?? 0,
      }));
      const candidates: HubCandidate[] = srv.items
        .filter((s) => !isHub(s))
        .map((s) => ({
          id: s.id,
          hostname: s.hostname,
          display_name: s.display_name,
          ip_address: s.ip_address,
          department_id: s.department_id,
        }));
      return { hubs, vms: vmsPage.items, candidates };
    },
    [mock],
    { enabled: !zoneBlocked, keepPreviousDataOnError: true },
  );

  const hubs = useMemo(() => hubsAndVmsQ.data?.hubs ?? [], [hubsAndVmsQ.data]);
  const allVms = useMemo(() => hubsAndVmsQ.data?.vms ?? [], [hubsAndVmsQ.data]);
  const candidates = useMemo(
    () => hubsAndVmsQ.data?.candidates ?? [],
    [hubsAndVmsQ.data],
  );

  const selectedHub = hubs.find((h) => h.id === selectedHubId) ?? null;
  const selectedVm = allVms.find((v) => v.id === selectedVmId) ?? null;
  const hubVms = useMemo(
    () => (selectedHubId ? allVms.filter((v) => v.hub_server_id === selectedHubId) : []),
    [allVms, selectedHubId],
  );

  const prepareOutcome = useTaskOutcome();

  function selectHub(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("hub", id);
    else next.delete("hub");
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

  async function handlePrepare(serverId: string, hostname: string) {
    prepareOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await prepareVmsHub(serverId);
      prepareOutcome.track(`prepare-vms-hub · ${hostname}`, res.task_id, res.status);
      toast.success(`Подготовка ${hostname} как VMS-hub — задача поставлена`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось поставить подготовку хаба"));
    }
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
            <div className="px-3 py-3 text-xs text-dim">Нет подготовленных хабов.</div>
          )}
        </div>

        {canPrepare && candidates.length > 0 && (
          <>
            <div className="group-header px-3 mt-4 text-[11px] uppercase text-dim">
              Кандидаты в hub · {candidates.length}
            </div>
            <div className="px-2 flex flex-col gap-1">
              {candidates.map((c) => (
                <CandidateRow key={c.id} candidate={c} onPrepare={handlePrepare} />
              ))}
            </div>
          </>
        )}

        {prepareOutcome.tracked && (
          <TaskOutcomeBanner
            outcome={prepareOutcome.tracked}
            className="mx-3 mt-3"
            successText="Хаб подготовлен."
            onCancelled={prepareOutcome.reset}
          />
        )}
      </div>
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
      {action === "new" && selectedHub && canManage ? (
        <CreateVmPane
          hub={selectedHub}
          boxes={MOCK_VM_BOXES}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedVm ? (
        <VmCard
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
          canManage={canManage}
          onOpenVm={selectVm}
          onCreate={startCreate}
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

function CandidateRow({
  candidate,
  onPrepare,
}: {
  candidate: HubCandidate;
  onPrepare: (serverId: string, hostname: string) => void | Promise<void>;
}) {
  const { confirm } = useConfirm();
  const [pending, setPending] = useState(false);
  const name = candidate.display_name ?? candidate.hostname;
  return (
    <div className="surface-2 border border-token rounded px-2 py-1.5 flex items-center gap-2">
      <ServerIcon className="w-4 h-4 text-dim shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{name}</div>
        <div className="text-[11px] text-dim mono truncate">{candidate.ip_address}</div>
      </div>
      <button
        type="button"
        className="btn btn-sm flex items-center gap-1"
        disabled={pending}
        title="Подготовить сервер как VMS-hub"
        onClick={async () => {
          const ok = await confirm({
            title: "Подготовить как VMS-hub",
            message: `Подготовить ${candidate.hostname} как VMS-hub? Установит libvirt/kvm, настроит мост br0 и storage-pool, скачает образы каталога.`,
            confirmLabel: "Подготовить",
          });
          if (!ok) return;
          setPending(true);
          try {
            await onPrepare(candidate.id, candidate.hostname);
          } finally {
            setPending(false);
          }
        }}
      >
        <Play className="w-3.5 h-3.5" /> Hub
      </button>
    </div>
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
  canManage,
  onOpenVm,
  onCreate,
}: {
  hub: VmHub;
  vms: Vm[];
  canManage: boolean;
  onOpenVm: (vm: Vm) => void;
  onCreate: () => void;
}) {
  const deptLabel = useDeptLabel(hub.department_id);
  const name = hub.display_name ?? hub.hostname;
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
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={onCreate}
          >
            <Plus className="w-4 h-4" /> Создать ВМ
          </button>
        )}
      </div>

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

// ── VM card ─────────────────────────────────────────────────────────────────

function VmCard({
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
  const toast = useToast();
  const { confirm, prompt } = useConfirm();
  const deptLabel = useDeptLabel(vm.department_id);
  const powerOutcome = useTaskOutcome();
  const [local, setLocal] = useState<Vm>(vm);
  const [pending, setPending] = useState(false);

  // vm prop меняется при refetch — подхватываем свежую копию.
  const view = local.id === vm.id ? local : vm;
  const reserved = view.busy_state !== "free" || !!view.busy_note;

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

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
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

      <div className="p-5 flex flex-col gap-4">
        {canManage && (
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
            {powerOutcome.tracked && (
              <TaskOutcomeBanner
                outcome={powerOutcome.tracked}
                className="mt-3"
                successText="Питание применено."
                onCancelled={powerOutcome.reset}
              />
            )}
          </div>
        )}

        {canManage && (
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
        )}

        <div className="card">
          <h3 className="font-semibold text-base mb-3">Параметры</h3>
          <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm">
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
        </div>

        {canManage && (
          <div className="card" style={{ border: "1px solid var(--danger, #b91c1c)" }}>
            <div className="text-sm font-semibold flex items-center gap-2 text-danger mb-2">
              Опасная зона
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex-1 text-xs text-dim">
                Удаление ВМ сносит домен libvirt и все её диски. Действие необратимо.
              </div>
              <button
                className="btn btn-danger flex items-center gap-1"
                onClick={handleDelete}
              >
                Удалить ВМ
              </button>
            </div>
          </div>
        )}
      </div>
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
  boxes,
  onCancel,
  onSubmit,
}: {
  hub: VmHub;
  boxes: string[];
  onCancel: () => void;
  onSubmit: (body: VmCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [cpu, setCpu] = useState("2");
  const [ramMb, setRamMb] = useState("4096");
  const [diskGb, setDiskGb] = useState("40");
  const [box, setBox] = useState(boxes[0] ?? "vm_station");
  const [networkMode, setNetworkMode] = useState<VmNetworkMode>("bridge");
  const [ipMode, setIpMode] = useState<"auto" | "manual">("auto");
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
  const ipError =
    ipMode === "manual" && ip.trim() && !isLikelyIpv4(ip.trim())
      ? "Ожидается IPv4-адрес"
      : null;
  const valid =
    !!name.trim() &&
    !nameError &&
    Number.isFinite(cpuN) &&
    cpuN > 0 &&
    Number.isFinite(ramN) &&
    ramN > 0 &&
    Number.isFinite(diskN) &&
    diskN > 0 &&
    !ipError &&
    (ipMode === "auto" || !!ip.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const body: VmCreateRequest = {
      hub_server_id: hub.id,
      name: name.trim(),
      cpu: cpuN,
      ram_mb: ramN,
      disk_gb: diskN,
      box,
      network_mode: networkMode,
      ip_address: ipMode === "manual" ? ip.trim() : null,
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
            <span className="text-dim text-xs">box (образ) *</span>
            <select className="input" value={box} onChange={(e) => setBox(e.target.value)}>
              {boxes.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
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

          <fieldset className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">IP-адрес</span>
            <div className="flex items-center gap-3 flex-wrap">
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="radio"
                  checked={ipMode === "auto"}
                  onChange={() => setIpMode("auto")}
                />
                свободный из пула (авто)
              </label>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="radio"
                  checked={ipMode === "manual"}
                  onChange={() => setIpMode("manual")}
                />
                задать вручную
              </label>
            </div>
            {ipMode === "manual" && (
              <>
                <input
                  className="input mt-1"
                  value={ip}
                  onChange={(e) => setIp(e.target.value)}
                  placeholder="10.177.103.51"
                />
                {ipError && <span className="text-[11px] text-danger">{ipError}</span>}
              </>
            )}
          </fieldset>

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
                Прочие параметры (снимки, диски, autostart) — в следующих волнах.
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

function isLikelyIpv4(value: string): boolean {
  return /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(value);
}
