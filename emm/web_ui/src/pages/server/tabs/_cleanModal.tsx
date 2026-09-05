/**
 * Модалка очистки сервера после переустановки ОС (`POST /servers/{id}/clean`).
 *
 * Четыре независимых флага:
 *  ☐ Отвязать все учётки   ☐ Обновить версию ОС (+ селект os_version)
 *  ☐ Заново prepare (+ блок bootstrap-кред)   ☐ Выполнить inventory.sync
 *
 * Блок bootstrap-кред для `rerun_prepare` — те же поля, что у single-prepare
 * (переиспользуем `BootstrapCredsFields`): привязанная учётка по умолчанию либо
 * ручной ввод (сервер «голый» после переустановки). Submit задизейблен, пока
 * не выбран ни один флаг; перед отправкой — danger-подтверждение. Ответ
 * показывается пер-действие (`done | dispatched | skipped | failed` + RU).
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link } from "react-router-dom";
import {
  Eraser,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  XCircle,
  MinusCircle,
  X,
} from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { cleanServer } from "@/api/server/servers";
import { listOsVersions } from "@/api/server/osVersions";
import { cleanReasonRu } from "@/pages/server/_serverShared";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  BootstrapCredsFields,
  bootstrapFormToBody,
  bootstrapFormValid,
  emptyBootstrapForm,
  type BootstrapCredsForm,
} from "./_bootstrapCredsModal";
import type {
  OffsetPaginatedResponse,
  OsVersion,
  ServerAccount,
  ServerCleanActionResult,
  ServerCleanResponse,
} from "@/api/server/types";

const ACTION_ORDER: {
  key: keyof Omit<ServerCleanResponse, "server_id">;
  label: string;
}[] = [
  { key: "unbind_accounts", label: "Отвязка учёток" },
  { key: "rerun_prepare", label: "Повторный prepare" },
  { key: "update_os_version", label: "Обновление версии ОС" },
  { key: "run_inventory_sync", label: "Inventory sync" },
  { key: "delete_vms", label: "Удаление ВМ хаба" },
];

function statusBadge(status: string) {
  if (status === "done")
    return (
      <span className="badge badge-ok text-[10px] flex items-center gap-1">
        <CheckCircle2 className="w-3 h-3" /> выполнено
      </span>
    );
  if (status === "dispatched")
    return (
      <span className="badge badge-accent text-[10px] flex items-center gap-1">
        <ArrowRight className="w-3 h-3" /> задача поставлена
      </span>
    );
  if (status === "skipped")
    return (
      <span className="badge text-[10px] flex items-center gap-1 text-dim">
        <MinusCircle className="w-3 h-3" /> пропущено
      </span>
    );
  return (
    <span className="badge badge-warn text-[10px] flex items-center gap-1">
      <XCircle className="w-3 h-3" /> ошибка
    </span>
  );
}

function ActionResultRow({
  label,
  result,
}: {
  label: string;
  result: ServerCleanActionResult;
}) {
  return (
    <div className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1.5">
      <span className="flex-1 min-w-0 truncate">{label}</span>
      {statusBadge(result.status)}
      {result.status === "dispatched" && result.task_id && (
        <Link
          to={`/tasks/${result.task_id}`}
          className="btn btn-sm flex items-center gap-1"
          title="Открыть страницу задачи"
        >
          <span className="mono text-[11px]">{result.task_id}</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      )}
      {result.reason && result.status !== "dispatched" && (
        <span className="text-xs text-dim text-right">
          {cleanReasonRu(result.reason)}
        </span>
      )}
    </div>
  );
}

export function CleanModal({
  serverId,
  hostname,
  accounts,
  accountsLoading,
  currentOsVersionId,
  onClose,
  onDone,
}: {
  serverId: string;
  hostname: string;
  accounts: ServerAccount[];
  accountsLoading: boolean;
  currentOsVersionId: string | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { confirm } = useConfirm();
  const [unbind, setUnbind] = useState(false);
  const [updateOs, setUpdateOs] = useState(false);
  const [rerun, setRerun] = useState(false);
  const [inventory, setInventory] = useState(false);
  const [delVms, setDelVms] = useState(false);
  const [osVersionId, setOsVersionId] = useState<string>(
    currentOsVersionId ?? "",
  );
  const [form, setForm] = useState<BootstrapCredsForm>(() =>
    emptyBootstrapForm(accounts),
  );
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<ServerCleanResponse | null>(null);

  const osQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 200 }),
    [],
    { enabled: updateOs },
  );
  const osVersions = useMemo(() => osQ.data?.items ?? [], [osQ.data]);

  const anyFlag = unbind || updateOs || rerun || inventory || delVms;
  const prepareValid = !rerun || bootstrapFormValid(form);
  const canSubmit = anyFlag && prepareValid;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !canSubmit) return;

    const actions = [
      unbind && "отвязка всех учёток",
      rerun && "повторный prepare",
      updateOs && "смена версии ОС",
      inventory && "inventory sync",
      delVms && "удаление всех ВМ хаба",
    ].filter(Boolean) as string[];
    const ok = await confirm({
      title: "Очистить сервер",
      message: `Выполнить очистку ${hostname}? Будут запущены: ${actions.join(", ")}. Отвязка учёток снимает все связки и удаляет OS-пользователей с боксов.`,
      confirmLabel: "Выполнить очистку",
      danger: true,
    });
    if (!ok) return;

    setErr(null);
    setPending(true);
    try {
      const res = await cleanServer(serverId, {
        unbind_accounts: unbind,
        update_os_version: updateOs,
        rerun_prepare: rerun,
        run_inventory_sync: inventory,
        delete_vms: delVms,
        ...(updateOs ? { os_version_id: osVersionId || null } : {}),
        ...(rerun ? { prepare: bootstrapFormToBody(form) } : {}),
      });
      setResult(res);
    } catch (e) {
      setErr(apiErrMsg(e, "Очистка не удалась"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: 600 }}
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Eraser className="w-5 h-5 text-danger" />
            <Dialog.Title className="text-base font-semibold">
              Очистка сервера <span className="mono">{hostname}</span>
            </Dialog.Title>
          </div>

          {result ? (
            <>
              <div className="modal-body flex flex-col gap-2">
                <div className="text-sm text-dim mb-1">
                  Итог по каждому действию:
                </div>
                {ACTION_ORDER.map(({ key, label }) => (
                  <ActionResultRow key={key} label={label} result={result[key]} />
                ))}
                <div className="text-[11px] text-dim mt-1">
                  Поставленные задачи (prepare / inventory) отслеживайте на
                  странице задачи.
                </div>
              </div>
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-primary flex items-center gap-1"
                  onClick={() => {
                    onDone();
                    onClose();
                  }}
                >
                  <X className="w-4 h-4" /> Готово
                </button>
              </div>
            </>
          ) : (
            <form onSubmit={submit}>
              <div className="modal-body flex flex-col gap-3">
                <Dialog.Description className="text-sm text-dim">
                  Оркестрация очистки после переустановки ОС. Выберите действия —
                  они выполнятся в порядке: отвязка → prepare → версия ОС →
                  inventory → удаление ВМ.
                </Dialog.Description>

                {err && <div className="alert-danger text-sm">{err}</div>}

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={unbind}
                    onChange={(e) => setUnbind(e.target.checked)}
                    disabled={pending}
                  />
                  Отвязать все учётки (снять связки + userdel на боксах)
                </label>

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={updateOs}
                    onChange={(e) => setUpdateOs(e.target.checked)}
                    disabled={pending}
                  />
                  Обновить версию ОС
                </label>
                {updateOs && (
                  <div className="ml-6">
                    <label className="field-label">os_version</label>
                    {osQ.loading ? (
                      <div className="text-xs text-dim">Загрузка каталога…</div>
                    ) : (
                      <Dropdown
                        mode="single"
                        placeholder="— сбросить версию —"
                        disabled={pending}
                        options={[
                          { value: "", label: "— сбросить версию —" },
                          ...osVersions.map((v) => ({ value: v.id, label: v.name })),
                        ]}
                        value={osVersionId}
                        onChange={setOsVersionId}
                      />
                    )}
                  </div>
                )}

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={rerun}
                    onChange={(e) => setRerun(e.target.checked)}
                    disabled={pending}
                  />
                  Заново prepare (bootstrap management-цикла)
                </label>
                {rerun && (
                  <div className="ml-6 surface-2 border border-token rounded p-3">
                    <BootstrapCredsFields
                      value={form}
                      onChange={setForm}
                      accounts={accounts}
                      accountsLoading={accountsLoading}
                      disabled={pending}
                    />
                  </div>
                )}

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={inventory}
                    onChange={(e) => setInventory(e.target.checked)}
                    disabled={pending}
                  />
                  Выполнить inventory.sync (SSH-probe)
                </label>

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={delVms}
                    onChange={(e) => setDelVms(e.target.checked)}
                    disabled={pending}
                  />
                  Удалить все ВМ хаба (переустановка ОС стёрла их диски)
                </label>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn flex items-center gap-1"
                  onClick={onClose}
                  disabled={pending}
                >
                  <X className="w-4 h-4" /> Отмена
                </button>
                <button
                  type="submit"
                  className="btn btn-danger flex items-center gap-1"
                  disabled={pending || !canSubmit}
                >
                  <Eraser className="w-4 h-4" />
                  {pending ? "Выполняем…" : "Очистить"}
                </button>
              </div>
              {!anyFlag && (
                <div className="px-4 pb-3 text-[11px] text-dim flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  Выберите хотя бы одно действие.
                </div>
              )}
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
