/**
 * Модалка «Привязать существующий аккаунт к этому серверу».
 *
 * Один `server_account` живёт на нескольких серверах (общий пул отдела). Список
 * всех учёток отдела отдаёт `GET /server-accounts` без `server_id`, поэтому
 * выбираем нужную напрямую (с фильтром по логину), исключая уже привязанные к
 * текущему серверу, и привязываем через `POST /server-accounts/{id}/servers`
 * ({server_ids}). Заводить на боксе — отдельным Provision после привязки.
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link2, Search, User } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import * as accountsApi from "@/api/server/accounts";
import type { ServerAccount } from "@/api/server/types";

export function LinkAccountModal({
  serverId,
  serverLabel: currentLabel,
  onClose,
  onLinked,
}: {
  serverId: string;
  serverLabel: string;
  onClose: () => void;
  onLinked: (account: ServerAccount) => void;
}) {
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Все учётки отдела вызывающего (без server_id). Исключаем уже привязанные к
  // текущему серверу и фильтруем по логину.
  const accountsQ = useQuery(
    () => accountsApi.listAccounts({ limit: 200 }),
    [],
  );
  const candidates = useMemo(() => {
    const all = accountsQ.data?.items ?? [];
    const q = query.trim().toLowerCase();
    return all
      .filter((a) => !a.server_ids.includes(serverId))
      .filter((a) => !q || a.login.toLowerCase().includes(q));
  }, [accountsQ.data, serverId, query]);

  async function bind(account: ServerAccount) {
    if (pending) return;
    setErr(null);
    setPending(true);
    try {
      const updated = await accountsApi.bindAccountServers(account.id, {
        server_ids: [serverId],
      });
      onLinked(updated);
    } catch (e) {
      setErr(apiErrMsg(e, "Не удалось привязать аккаунт"));
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
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Link2 className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Привязать существующий аккаунт
            </Dialog.Title>
          </div>

          <div className="modal-body">
            <Dialog.Description className="text-sm text-dim mb-3">
              Учётка отдела будет привязана к{" "}
              <span className="mono">{currentLabel}</span>. На боксе он сам не
              заводится — после привязки запустите Provision на вкладке.
            </Dialog.Description>

            {err && <div className="alert-danger mb-3 text-sm">{err}</div>}

            <label className="field-label flex items-center gap-1">
              <User className="w-3.5 h-3.5" /> Аккаунт для привязки
            </label>

            <div className="relative mb-1">
              <Search className="w-3.5 h-3.5 text-dim absolute left-2 top-1/2 -translate-y-1/2" />
              <input
                className="field-input mono pl-7"
                placeholder="фильтр по логину"
                value={query}
                disabled={pending}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>

            {accountsQ.loading && (
              <div className="text-xs text-dim py-2">Загрузка аккаунтов…</div>
            )}
            {accountsQ.error && (
              <div className="alert-danger text-xs">
                {apiErrMsg(accountsQ.error, "Аккаунты не загрузились")}
                <button
                  className="btn btn-sm ml-2"
                  onClick={() => accountsQ.refetch()}
                >
                  Повторить
                </button>
              </div>
            )}
            {!accountsQ.loading &&
              !accountsQ.error &&
              candidates.length === 0 && (
                <div className="text-xs text-dim py-2">
                  {query.trim()
                    ? "Нет учёток по фильтру."
                    : "В отделе нет учёток, которые ещё не привязаны к этому серверу."}
                </div>
              )}
            {candidates.length > 0 && (
              <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto mt-1">
                {candidates.map((a) => (
                  <div
                    key={a.id}
                    className="cred-row flex items-center gap-2"
                  >
                    <User className="w-4 h-4 text-dim shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate mono">{a.login}</div>
                      <div className="text-[11px] text-dim truncate">
                        {a.server_ids.length} сервер(ов) · {a.source}
                      </div>
                    </div>
                    <button
                      className="btn btn-sm btn-primary flex items-center gap-1 shrink-0"
                      disabled={pending}
                      onClick={() => bind(a)}
                    >
                      <Link2 className="w-3.5 h-3.5" /> Привязать
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={pending}
            >
              Закрыть
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
