/**
 * Вкладка «Снимки» карточки физического сервера — полные снимки диска через
 * ACS (Clonezilla-обёртка), не путать с VM-снимками (`./snapshots.tsx`, тот
 * таб — VM-only).
 *
 * Снимок группируется каталожной версией ОС (`os_version_id` при создании);
 * список снимков (`GET /acs-snapshots`) отдаёт только `name`/`version_name` —
 * чтобы восстановить снимок, версию из каталога резолвим обратно по имени
 * (`version_name === OsVersion.name`). Создание доступно операторам/админам
 * server-зоны; восстановление — необратимая перезапись диска — только
 * платформенному admin'у (`Action.ACS_SNAPSHOT_RESTORE` не грантуется
 * per-instance, см. `server_service/src/api/v1/endpoints/worker_dispatch.py`).
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, Camera, Plus, RotateCcw } from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import { isPlatformWideAdmin } from "@/lib/rbac";
import type { Persona } from "@/types/persona";
import {
  createAcsSnapshot,
  listAcsSnapshots,
  restoreAcsSnapshot,
} from "@/api/server/acsSnapshots";
import { listOsVersions } from "@/api/server/osVersions";
import type { AcsSnapshot, OsVersion, Server } from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
}

/** Кто может создавать снимки ACS: server.admin/operator своей зоны или dep_admin своего отдела. */
function canCreateAcs(persona: Persona, serverDeptId: string | null): boolean {
  const svc = persona.service_roles.server;
  if (persona.platform_role === "dep_admin" && serverDeptId && persona.dept_id === serverDeptId) {
    return true;
  }
  return svc === "admin" || svc === "operator";
}

export function AcsSnapshotsTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const { confirm } = useConfirm();
  const outcome = useTaskOutcome();
  const [createOpen, setCreateOpen] = useState(false);

  const canCreate = canCreateAcs(persona, server?.department_id ?? null);
  const canRestore = isPlatformWideAdmin(persona);

  const snapsQ = useQuery(
    async () => (await listAcsSnapshots(serverId)).snapshots,
    [serverId],
    { keepPreviousDataOnError: true },
  );
  const snapshots = snapsQ.data ?? [];

  const versionsQ = useQuery<OsVersion[]>(
    async () => (await listOsVersions({ limit: 200 })).items,
    [],
    { keepPreviousDataOnError: true },
  );
  const versions = versionsQ.data ?? [];
  const versionIdByName = useMemo(() => {
    const m = new Map<string, string>();
    for (const v of versionsQ.data ?? []) m.set(v.name, v.id);
    return m;
  }, [versionsQ.data]);

  async function handleCreate(osVersionId: string, versionLabel: string) {
    outcome.reset();
    try {
      const res = await createAcsSnapshot(serverId, osVersionId);
      outcome.track(`acs snapshot create · ${versionLabel}`, res.task_id, res.status);
      toast.success(`Создание снимка «${versionLabel}» — задача поставлена`);
      setCreateOpen(false);
      snapsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание снимка не удалось"));
    }
  }

  async function handleRestore(snap: AcsSnapshot) {
    const osVersionId = versionIdByName.get(snap.version_name);
    if (!osVersionId) {
      toast.error(
        `Версия каталога «${snap.version_name}» не найдена — восстановление недоступно`,
      );
      return;
    }
    const ok = await confirm({
      title: "Восстановить сервер из снимка",
      message: `Восстановить ${server?.hostname ?? serverId} из снимка «${snap.name}»? Операция ПОЛНОСТЬЮ ПЕРЕПИШЕТ ДИСК сервера и необратима. Сервер станет недоступен на время восстановления.`,
      confirmLabel: "Восстановить",
      danger: true,
    });
    if (!ok) return;
    outcome.reset();
    try {
      const res = await restoreAcsSnapshot(serverId, osVersionId);
      outcome.track(`acs snapshot restore · ${snap.name}`, res.task_id, res.status);
      toast.success(`Восстановление из «${snap.name}» — задача поставлена`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Восстановление не удалось"));
    }
  }

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-base flex items-center gap-2">
            <Camera className="w-4 h-4 text-accent" /> Снимки ACS
          </h3>
          {canCreate && (
            <button
              className="btn btn-sm btn-primary flex items-center gap-1"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="w-3.5 h-3.5" /> Создать снимок
            </button>
          )}
        </div>

        <p className="text-xs text-dim mb-3">
          Снимок — это полный образ диска сервера (не отдельные файлы), снятый
          через ACS. Восстановление полностью перезаписывает диск и доступно
          только платформенному администратору.
        </p>

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
          <div className="surface-2 border border-token rounded max-h-80 overflow-y-auto">
            {snapshots.map((snap) => (
              <SnapshotRow
                key={snap.name}
                snap={snap}
                canRestore={canRestore}
                onRestore={handleRestore}
              />
            ))}
          </div>
        )}

        {outcome.tracked && (
          <TaskOutcomeBanner
            outcome={outcome.tracked}
            className="mt-3"
            successText="Операция со снимком ACS завершилась успешно."
            onCancelled={outcome.reset}
          />
        )}

        {!canRestore && (
          <div className="text-[11px] text-dim mt-3">
            Восстановление доступно только платформенному администратору
            (account_admin).
          </div>
        )}
      </div>

      {createOpen && (
        <CreateSnapshotModal
          versions={versions}
          versionsLoading={versionsQ.loading}
          onClose={() => setCreateOpen(false)}
          onSubmit={handleCreate}
        />
      )}
    </div>
  );
}

function SnapshotRow({
  snap,
  canRestore,
  onRestore,
}: {
  snap: AcsSnapshot;
  canRestore: boolean;
  onRestore: (s: AcsSnapshot) => void;
}) {
  return (
    <div className="px-3 py-1.5 flex items-center gap-2 border-b border-token last:border-b-0">
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{snap.name}</div>
        <div className="text-[11px] text-dim truncate">
          версия: {snap.version_name}
        </div>
      </div>
      {canRestore && (
        <button
          className="btn btn-sm btn-danger flex items-center gap-1 shrink-0"
          title="Восстановить сервер из этого снимка (полная перезапись диска)"
          onClick={() => onRestore(snap)}
        >
          <RotateCcw className="w-3.5 h-3.5" /> Восстановить
        </button>
      )}
    </div>
  );
}

function CreateSnapshotModal({
  versions,
  versionsLoading,
  onClose,
  onSubmit,
}: {
  versions: OsVersion[];
  versionsLoading: boolean;
  onClose: () => void;
  onSubmit: (osVersionId: string, versionLabel: string) => void | Promise<void>;
}) {
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
    <Dialog.Root open onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" aria-describedby={undefined}>
          <div className="modal-header">
            <Camera className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Новый снимок ACS
            </Dialog.Title>
          </div>
          <form onSubmit={submit}>
            <div className="modal-body flex flex-col gap-3">
              <div className="text-xs text-dim">
                Снимок именуется по версии каталога ОС — она же используется
                при восстановлении, чтобы найти нужный образ.
              </div>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-dim text-xs">Версия ОС *</span>
                {versionsLoading ? (
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
                {submitting ? "Запускаем…" : "Создать снимок"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
