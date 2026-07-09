/**
 * Вкладка «Диски» — только для карточки ВМ.
 *
 * Карточка сервера и карточка ВМ рендерятся одними и теми же файлами-вкладками,
 * но диски есть только у ВМ: список блочных устройств + создание, resize и
 * удаление. Для сервера вкладка не показывается (диспетчер её не подставляет),
 * а на всякий случай при `entity.kind !== "vm"` компонент возвращает null.
 *
 * Контекст (mock-режим, право управления, колбэк обновления списка) берём из
 * `entity` — у ВМ нет persona-матрицы прав на каждую операцию, право приходит
 * одним флагом сверху.
 */
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, HardDrive, Maximize2, Plus, Trash2 } from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { MOCK_VM_DISKS } from "@/mocks/vm";
import {
  createVmDisk,
  deleteVmDisk,
  listVmDisks,
  resizeVmDisk,
  type Vm,
  type VmDisk,
  type VmDiskCreateRequest,
  type VmDiskResizeRequest,
} from "@/api/server/vms";
import type { TaskDispatchResponse } from "@/api/server/types";
import type { EntityTabProps } from "./_entity";

// В mock-режиме операции никуда не уходят — подсовываем правдоподобный ответ
// диспетчера, чтобы UI отработал постановку задачи.
function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

export function DisksTab({ entity }: EntityTabProps) {
  if (entity?.kind !== "vm") return null;
  return (
    <div className="p-5">
      <DisksSection
        vm={entity.vm}
        mock={entity.mock}
        canManage={entity.canManage}
        onChanged={entity.onChanged}
      />
    </div>
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
      const body: VmDiskResizeRequest = { size_gb: sizeGb };
      const res = mock
        ? fakeDispatch()
        : await resizeVmDisk(vm.id, disk.id, body);
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
