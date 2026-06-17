/**
 * Ignore-list отдела — модалка над страницей `Server.Пользователи`.
 *
 * Логины из этого списка ревизия не показывает как незнакомые. Список тянет
 * `listIgnoredLogins`, добавление — `addIgnoredLogin`, снятие —
 * `removeIgnoredLogin`. Гейтится правом `manage_ignored_logins`; точного
 * persona-флага в UI нет, поэтому кнопка-вход приближена к `canManage`/
 * `canOperate`, а финальный отказ (403) приходит с backend'а.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { EyeOff, Trash2, Plus, AlertCircle } from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useQuery } from "@/api/auth/useQuery";
import { useUserLabel } from "@/lib/labels";
import { formatMskShort } from "@/lib/datetime";
import {
  listIgnoredLogins,
  addIgnoredLogin,
  removeIgnoredLogin,
} from "@/api/server/accounts";
import type { IgnoredLogin } from "@/api/server/types";

function addError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен manage_ignored_logins).";
    if (e.status === 409) return "Логин уже в ignore-list.";
  }
  return apiErrMsg(e, "Не удалось добавить логин");
}

function removeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен manage_ignored_logins).";
    if (e.status === 404) return "Логин не найден в ignore-list.";
  }
  return apiErrMsg(e, "Не удалось снять логин");
}

export function IgnoredLoginsModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const listQ = useQuery(() => listIgnoredLogins(), []);
  const [login, setLogin] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);

  const items = listQ.data ?? [];

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    const value = login.trim();
    if (!value) {
      toast.error("Укажите login.");
      return;
    }
    setPending(true);
    try {
      await addIgnoredLogin({ login: value, reason: reason.trim() || null });
      toast.success(`${value} добавлен в ignore-list`);
      setLogin("");
      setReason("");
      listQ.refetch();
    } catch (err) {
      toast.error(addError(err));
    } finally {
      setPending(false);
    }
  }

  async function handleRemove(item: IgnoredLogin) {
    if (
      !(await confirm({
        title: "Снять игнор",
        message: `Снять ${item.login} с ignore-list? Дальше ревизия снова будет показывать его как незнакомого.`,
        confirmLabel: "Снять игнор",
      }))
    )
      return;
    try {
      await removeIgnoredLogin(item.login);
      toast.success(`${item.login} снят с ignore-list`);
      listQ.refetch();
    } catch (err) {
      toast.error(removeError(err));
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" style={{ maxWidth: 640 }}>
          <div className="modal-header">
            <EyeOff className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Ignore-лист отдела
            </Dialog.Title>
          </div>

          <div className="modal-body flex flex-col gap-4">
            <Dialog.Description className="text-xs text-dim">
              Логины из списка ревизия не считает незнакомыми (помимо системных
              по UID). Действует на весь отдел.
            </Dialog.Description>

            <form onSubmit={handleAdd} className="flex items-end gap-2 flex-wrap">
              <label className="flex flex-col gap-1 text-sm flex-1 min-w-[140px]">
                <span className="field-label">login</span>
                <input
                  className="field-input mono"
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                  placeholder="backup-svc"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm flex-1 min-w-[140px]">
                <span className="field-label">причина</span>
                <input
                  className="field-input"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="служебный, заводится вендором"
                />
              </label>
              <button
                type="submit"
                className="btn btn-sm btn-primary flex items-center gap-1"
                disabled={pending || !login.trim()}
              >
                <Plus className="w-3.5 h-3.5" />
                {pending ? "Добавляем…" : "Добавить"}
              </button>
            </form>

            {listQ.loading && (
              <div className="text-xs text-dim text-center py-4">Загрузка…</div>
            )}
            {!listQ.loading && listQ.error != null && (
              <div className="alert-danger text-sm flex items-start gap-2">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <div className="flex-1">
                  <div>{apiErrMsg(listQ.error, "Ignore-лист не загрузился")}</div>
                  <button
                    className="btn btn-ghost mt-2"
                    onClick={() => listQ.refetch()}
                    type="button"
                  >
                    Повторить
                  </button>
                </div>
              </div>
            )}
            {!listQ.loading && listQ.error == null && items.length === 0 && (
              <div className="text-sm text-dim text-center py-4">
                Ignore-лист пуст.
              </div>
            )}
            {!listQ.loading && listQ.error == null && items.length > 0 && (
              <div className="flex flex-col gap-1 max-h-72 overflow-y-auto">
                {items.map((item) => (
                  <IgnoredRow
                    key={item.id}
                    item={item}
                    onRemove={() => handleRemove(item)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose}>
              Закрыть
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function IgnoredRow({
  item,
  onRemove,
}: {
  item: IgnoredLogin;
  onRemove: () => void;
}) {
  const createdByLabel = useUserLabel(item.created_by);
  return (
    <div className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm mono">{item.login}</div>
        <div className="text-[11px] text-dim">
          {item.reason || "без причины"} · {item.created_by ? createdByLabel : "system"} ·{" "}
          {formatMskShort(item.created_at)}
        </div>
      </div>
      <button
        type="button"
        className="btn btn-sm btn-danger flex items-center gap-1"
        onClick={onRemove}
        title="Снять с ignore-list"
      >
        <Trash2 className="w-3.5 h-3.5" /> Снять игнор
      </button>
    </div>
  );
}
