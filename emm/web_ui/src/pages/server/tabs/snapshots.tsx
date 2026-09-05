/**
 * Вкладка «Снимки» — только для ВМ.
 *
 * Карточка ВМ рендерится теми же файлами-вкладками, что и сервер; эта вкладка
 * VM-специфична и на серверной сущности не показывается (возвращает null). Собрана
 * из трёх карточек в порядке раскладки карточки ВМ: список снимков (две группы —
 * чистые версии ОС и пользовательские), обновление ОС/allta и режим управляющих
 * кред. Системные `_build`-снимки в UI скрыты.
 */
import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowUpCircle,
  Boxes,
  Camera,
  KeyRound,
  Plus,
  Search,
  Trash2,
  Undo2,
} from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { listOsVersions } from "@/api/server/osVersions";
import type { OsVersion, TaskDispatchResponse } from "@/api/server/types";
import {
  alltaUpdateVm,
  astraUpdateVm,
  createVmSnapshot,
  deleteVmSnapshot,
  listVmSnapshots,
  revertVmSnapshot,
  setVmCredStrategy,
  type Vm,
  type VmCredStrategy,
  type VmSnapshot,
  type VmSnapshotCategory,
  type VmSnapshotCreateRequest,
  type VmSnapshotMode,
  type VmSnapshotType,
} from "@/api/server/vms";
import { MOCK_VM_OS_VERSIONS, MOCK_VM_SNAPSHOTS } from "@/mocks/vm";
import type { EntityTabProps } from "./_entity";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal as UIModal } from "@/components/ui/Modal";

// ── data helpers (mock ↔ live) ──────────────────────────────────────────────

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

// ── tab entry ───────────────────────────────────────────────────────────────

export function SnapshotsTab({
  entity,
  onEntityUpdated,
}: EntityTabProps & { onEntityUpdated?: (next: Vm) => void }) {
  // Снимки — атрибут ВМ; для серверной сущности вкладки нет.
  if (entity?.kind !== "vm") return null;
  const { vm, mock, canManage, onChanged } = entity;

  return (
    <div className="p-5 flex flex-col gap-4">
      <SnapshotsSection
        vm={vm}
        mock={mock}
        canManage={canManage}
        onChanged={onChanged}
      />
      {canManage && <VmOsUpdateCard vm={vm} mock={mock} onChanged={onChanged} />}
      {canManage && (
        <CredStrategyCard
          vm={vm}
          mock={mock}
          onApplied={(next) => onEntityUpdated?.(next)}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}

// ── список снимков ──────────────────────────────────────────────────────────

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
          snapshot_type: body.snapshot_type,
          kind: "user",
          mode: null,
          os_version: null,
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

  // Две группы: чистые снимки версий ОС (сгруппированы по версии) и
  // пользовательские. Системные `_build` уже отфильтрованы выше.
  const baseline = snapshots.filter((s) => snapCategory(s) === "os_baseline");
  const userSnaps = snapshots.filter((s) => snapCategory(s) !== "os_baseline");

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <Camera className="w-4 h-4 text-accent" /> Снимки
        </h3>
        {canManage && (
          <Button variant="primary" size="sm"
            className="flex items-center gap-1"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="w-3.5 h-3.5" /> Создать снимок
          </Button>
        )}
      </div>

      {snapsQ.loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : snapsQ.error && snapshots.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(snapsQ.error, "Список снимков не загрузился")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => snapsQ.refetch()}>
              Повторить
            </Button>
          </div>
        </div>
      ) : snapshots.length === 0 ? (
        <div className="text-xs text-dim">Снимков нет.</div>
      ) : (
        <div className="flex flex-col gap-4">
          <SnapshotBaselineGroup
            snapshots={baseline}
            canManage={canManage}
            onRevert={handleRevert}
            onDelete={handleDelete}
          />
          <SnapshotUserGroup
            snapshots={userSnaps}
            canManage={canManage}
            onRevert={handleRevert}
            onDelete={handleDelete}
          />
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

/** Классификация снимка для группировки (см. `VmSnapshotCategory`). */
function snapCategory(s: VmSnapshot): VmSnapshotCategory {
  if (s.kind === "os_baseline" || s.kind === "user") return s.kind;
  return "user";
}

function snapModeLabel(mode?: VmSnapshotMode | null): string | null {
  // `orel` — текущее имя режима Орёл, `oryol` — прежнее (совместимость на чтение).
  if (mode === "orel" || mode === "oryol") return "Орёл";
  if (mode === "smolensk") return "Смоленск";
  return mode ? String(mode) : null;
}

function SnapshotRow({
  snap,
  canManage,
  allowDelete = true,
  onRevert,
  onDelete,
}: {
  snap: VmSnapshot;
  canManage: boolean;
  /**
   * Разрешено ли изменять/удалять снимок. Для чистых эталонных снимков версий
   * ОС (`os_baseline`) — false: их создаёт только сборка/astra-update, руками
   * не трогаем. Откат при этом остаётся доступен.
   */
  allowDelete?: boolean;
  onRevert: (s: VmSnapshot) => void;
  onDelete: (s: VmSnapshot) => void;
}) {
  const mode = snapModeLabel(snap.mode);
  return (
    <div className="px-3 py-1.5 flex items-center gap-2 border-b border-token last:border-b-0">
      <div className="flex-1 min-w-0">
        <div className="text-sm flex items-center gap-2 flex-wrap">
          <span className="truncate">{snap.name}</span>
          {mode && <Badge className="text-[11px]">{mode}</Badge>}
          {snap.is_current && (
            <Badge kind="ok" className="text-[11px]">текущий</Badge>
          )}
          {snap.state !== "ready" && (
            <Badge className="text-[11px]">{snap.state}</Badge>
          )}
        </div>
        <div className="text-[11px] text-dim truncate">
          {snap.description ?? "—"} · {formatSnapDate(snap.created_at)}
        </div>
      </div>
      {canManage && (
        <div className="flex items-center gap-1 shrink-0">
          <Button size="sm"
            className="flex items-center gap-1"
            title="Откатить ВМ на этот снимок"
            disabled={snap.is_current}
            onClick={() => onRevert(snap)}
          >
            <Undo2 className="w-3.5 h-3.5" /> Откат
          </Button>
          {allowDelete && (
            <Button variant="danger" size="sm"
              className="flex items-center gap-1"
              title="Удалить снимок"
              onClick={() => onDelete(snap)}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function SnapshotBaselineGroup({
  snapshots,
  canManage,
  onRevert,
  onDelete,
}: {
  snapshots: VmSnapshot[];
  canManage: boolean;
  onRevert: (s: VmSnapshot) => void;
  onDelete: (s: VmSnapshot) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = snapshots.filter((s) =>
    s.name.toLowerCase().includes(q.trim().toLowerCase()),
  );
  // Группируем по версии ОС, внутри — режимы.
  const versions = Array.from(
    new Set(filtered.map((s) => s.os_version ?? "—")),
  ).sort();

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h4 className="text-sm font-semibold">Версии ОС (чистые)</h4>
        <div className="flex items-center gap-1 surface border border-token rounded px-2 py-1">
          <Search className="w-3.5 h-3.5 text-dim" />
          <input
            className="bg-transparent outline-none text-xs w-36"
            placeholder="Поиск по имени…"
            aria-label="Поиск снимков версий ОС"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </div>
      {snapshots.length === 0 ? (
        <div className="text-xs text-dim">Чистых снимков версий ОС нет.</div>
      ) : filtered.length === 0 ? (
        <div className="text-xs text-dim">Ничего не найдено.</div>
      ) : (
        <div
          data-testid="snap-scroll-baseline"
          className="surface-2 border border-token rounded max-h-64 overflow-y-auto"
        >
          {versions.map((ver) => (
            <div key={ver}>
              <div className="px-3 py-1 text-[11px] uppercase text-dim surface sticky top-0">
                ОС {ver}
              </div>
              {filtered
                .filter((s) => (s.os_version ?? "—") === ver)
                .map((s) => (
                  <SnapshotRow
                    key={s.id}
                    snap={s}
                    canManage={canManage}
                    allowDelete={false}
                    onRevert={onRevert}
                    onDelete={onDelete}
                  />
                ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotUserGroup({
  snapshots,
  canManage,
  onRevert,
  onDelete,
}: {
  snapshots: VmSnapshot[];
  canManage: boolean;
  onRevert: (s: VmSnapshot) => void;
  onDelete: (s: VmSnapshot) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = snapshots.filter((s) =>
    s.name.toLowerCase().includes(q.trim().toLowerCase()),
  );

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h4 className="text-sm font-semibold">Пользовательские</h4>
        <div className="flex items-center gap-1 surface border border-token rounded px-2 py-1">
          <Search className="w-3.5 h-3.5 text-dim" />
          <input
            className="bg-transparent outline-none text-xs w-36"
            placeholder="Поиск по имени…"
            aria-label="Поиск пользовательских снимков"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </div>
      {snapshots.length === 0 ? (
        <div className="text-xs text-dim">Пользовательских снимков нет.</div>
      ) : filtered.length === 0 ? (
        <div className="text-xs text-dim">Ничего не найдено.</div>
      ) : (
        <div
          data-testid="snap-scroll-user"
          className="surface-2 border border-token rounded max-h-64 overflow-y-auto"
        >
          {filtered.map((s) => (
            <SnapshotRow
              key={s.id}
              snap={s}
              canManage={canManage}
              onRevert={onRevert}
              onDelete={onDelete}
            />
          ))}
        </div>
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

// ── обновление ОС и allta (astra-update / allta-update) ─────────────────────────

/**
 * Карточка обновления ОС и allta — живёт во вкладке «Снимки», рядом с
 * версионными снимками. astra-update откатывается на нужный `_build`-снимок и
 * пересобирает его под выбранную версию; allta-update прогоняет переустановку
 * guest-allta по всем не-«_build» снимкам.
 */
function VmOsUpdateCard({
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

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ArrowUpCircle className="w-4 h-4 text-accent" /> Обновление ОС и allta
      </h3>
      <div className="text-xs text-dim mb-3">
        Обновление версии ОС переснимает снимок под выбранную версию; обновление
        allta идёт по всем не-«_build» снимкам согласно режиму кред ВМ.
      </div>
      <div className="flex gap-2 flex-wrap">
        <Button
          className="flex items-center gap-1"
          onClick={() => setAstraOpen(true)}
        >
          <ArrowUpCircle className="w-4 h-4" /> Обновить ОС (astra-update)
        </Button>
        <Button className="flex items-center gap-1" onClick={handleAllta}>
          <Boxes className="w-4 h-4" /> Обновить allta
        </Button>
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
          <Dropdown
            mode="single"
            disabled={pending}
            options={[
              { value: "per_snapshot", label: "per_snapshot — креды на снимок" },
              { value: "reroll", label: "reroll — единый пароль (паритет)" },
            ]}
            value={strategy}
            onChange={(v) => setStrategy(v as VmCredStrategy)}
          />
        </label>
        <Button variant="primary"
          onClick={apply}
          disabled={!changed || pending}
        >
          {pending ? "Применяем…" : "Применить"}
        </Button>
      </div>
    </div>
  );
}

function credStrategyLabel(s: VmCredStrategy): string {
  return s === "reroll" ? "reroll" : "per_snapshot";
}

// ── модалки ──────────────────────────────────────────────────────────────────

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
    <UIModal open onOpenChange={(next) => !next && onClose()} title={title}>
      {children}
    </UIModal>
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
  const [snapshotType, setSnapshotType] = useState<VmSnapshotType>("disk_only");
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
      snapshot_type: snapshotType,
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
            <Dropdown
              mode="single"
              options={[
                { value: "disk_only", label: "disk-only (только диск)" },
                { value: "full", label: "full (диск + память/состояние)" },
              ]}
              value={snapshotType}
              onChange={(v) => setSnapshotType(v as VmSnapshotType)}
            />
          </label>
        </div>
        <div className="modal-footer">
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={!valid || submitting}
          >
            {submitting ? "Создаём…" : "Создать снимок"}
          </Button>
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
              <Dropdown
                mode="single"
                placeholder="— выберите версию —"
                options={[
                  { value: "", label: "— выберите версию —" },
                  ...versions.map((v) => ({ value: v.id, label: v.name })),
                ]}
                value={selected}
                onChange={setSelected}
              />
            )}
          </label>
        </div>
        <div className="modal-footer">
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={!valid || submitting}
          >
            {submitting ? "Запускаем…" : "Обновить ОС"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
