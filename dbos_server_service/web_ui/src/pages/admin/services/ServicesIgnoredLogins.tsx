/**
 * Игнорируемые OS-логины отдела (`/admin/services.server.ignored_logins`).
 *
 * Логины из этого списка ревизия (`users/inventory`) не считает незнакомыми —
 * служебные аккаунты вендоров и demon'ов (postgres, backup-svc и т.п.) не
 * светятся как discovered. Список dept-scoped.
 *
 * Источник правды — `GET/POST/DELETE /api/server/v1/server-accounts/ignored-logins`.
 * Гейтится action `manage_ignored_logins`: dep_admin своего отдела или носитель
 * server.admin. Platform-роли (account_admin / loging_admin) backend режет на
 * 403, поэтому страница им не показывается (см. visibleFor в adminCatalog).
 */

import { useState } from "react";
import { EyeOff, Trash2, Plus, AlertCircle } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useQuery } from "@/api/auth/useQuery";
import { useUserLabel } from "@/lib/labels";
import { formatMskShort } from "@/lib/datetime";
import { usePersona } from "@/contexts/PersonaContext";
import {
  listIgnoredLogins,
  addIgnoredLogin,
  removeIgnoredLogin,
} from "@/api/server/accounts";
import type { IgnoredLogin } from "@/api/server/types";

// `manage_ignored_logins` — dep_admin своего отдела или server.admin. Зеркалит
// гейт canManage на странице Server.Пользователи; финальный отказ (403)
// приходит с backend'а, если право всё-таки не выдано.
function canManageIgnoredLogins(
  persona: ReturnType<typeof usePersona>["persona"],
): boolean {
  return (
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.server === "admin"
  );
}

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

export function ServicesIgnoredLogins() {
  const { persona } = usePersona();
  const canEdit = canManageIgnoredLogins(persona);
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
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <EyeOff className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Игнорируемые логины
          </h1>
          <div className="text-xs text-dim">
            OS-логины, которые ревизия не показывает как незнакомые · отдел
          </div>
        </div>
        <div className="text-xs text-dim mr-2">
          {listQ.loading && items.length === 0 ? "…" : `${items.length} записей`}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-4">
        <div className="text-xs text-dim">
          Логины из списка ревизия не считает незнакомыми (помимо системных по
          UID). Действует на весь отдел.
        </div>

        {canEdit && (
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
              className="btn btn-primary flex items-center gap-1"
              disabled={pending || !login.trim()}
            >
              <Plus className="w-4 h-4" />
              {pending ? "Добавляем…" : "Добавить"}
            </button>
          </form>
        )}

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
          <div className="flex flex-col gap-1">
            {items.map((item) => (
              <IgnoredRow
                key={item.id}
                item={item}
                canEdit={canEdit}
                onRemove={() => handleRemove(item)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function IgnoredRow({
  item,
  canEdit,
  onRemove,
}: {
  item: IgnoredLogin;
  canEdit: boolean;
  onRemove: () => void;
}) {
  const createdByLabel = useUserLabel(item.created_by);
  return (
    <div className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm mono">{item.login}</div>
        <div className="text-[11px] text-dim">
          {item.reason || "без причины"} ·{" "}
          {item.created_by ? createdByLabel : "system"} ·{" "}
          {formatMskShort(item.created_at)}
        </div>
      </div>
      {canEdit && (
        <button
          type="button"
          className="btn btn-sm btn-danger flex items-center gap-1"
          onClick={onRemove}
          title="Снять с ignore-list"
        >
          <Trash2 className="w-3.5 h-3.5" /> Снять игнор
        </button>
      )}
    </div>
  );
}
