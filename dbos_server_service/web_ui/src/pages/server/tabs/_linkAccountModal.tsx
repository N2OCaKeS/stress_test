/**
 * Модалка «Привязать существующий аккаунт к этому серверу».
 *
 * Один `server_account` может жить на нескольких серверах, но вкладка Accounts
 * показывает только привязанные к текущему (`listAccounts` фильтрует по
 * `server_id`). Глобального списка аккаунтов нет, поэтому привязка идёт в два
 * шага: выбрать сервер-источник из `listServers()`, затем выбрать его аккаунт
 * и привязать к текущему серверу через
 * `POST /server-accounts/{id}/servers` ({server_ids}).
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link2, Server as ServerIcon, User } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import * as accountsApi from "@/api/server/accounts";
import { listServers } from "@/api/server/servers";
import type { ServerAccount } from "@/api/server/types";

function serverLabel(s: { display_name: string | null; hostname: string }): string {
  return s.display_name ?? s.hostname;
}

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
  const [sourceServerId, setSourceServerId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Шаг 1 — серверы-источники. Текущий исключаем: брать чужие аккаунты
  // имеет смысл только с другого сервера.
  const serversQ = useQuery(() => listServers({ limit: 200 }), []);
  const sourceServers = useMemo(() => {
    const all = serversQ.data?.items ?? [];
    return all.filter((s) => s.id !== serverId);
  }, [serversQ.data, serverId]);

  // Шаг 2 — аккаунты выбранного источника. Грузим только когда источник
  // выбран; исключаем те, что уже привязаны к текущему серверу.
  const accountsQ = useQuery(
    () => accountsApi.listAccounts({ server_id: sourceServerId!, limit: 200 }),
    [sourceServerId],
    { enabled: sourceServerId != null },
  );
  const candidates = useMemo(() => {
    const all = accountsQ.data?.items ?? [];
    return all.filter((a) => !a.server_ids.includes(serverId));
  }, [accountsQ.data, serverId]);

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
              Аккаунт с другого сервера будет привязан к{" "}
              <span className="mono">{currentLabel}</span>. На боксе он сам не
              заводится — после привязки запустите Provision на вкладке.
            </Dialog.Description>

            {err && <div className="alert-danger mb-3 text-sm">{err}</div>}

            {/* ── Шаг 1: сервер-источник ── */}
            <div className="mb-4">
              <label className="field-label flex items-center gap-1">
                <ServerIcon className="w-3.5 h-3.5" /> Сервер-источник
              </label>
              {serversQ.loading && (
                <div className="text-xs text-dim py-2">Загрузка серверов…</div>
              )}
              {serversQ.error && (
                <div className="alert-danger text-xs">
                  {apiErrMsg(serversQ.error, "Серверы не загрузились")}
                  <button
                    className="btn btn-sm ml-2"
                    onClick={() => serversQ.refetch()}
                  >
                    Повторить
                  </button>
                </div>
              )}
              {!serversQ.loading &&
                !serversQ.error &&
                sourceServers.length === 0 && (
                  <div className="text-xs text-dim py-2">
                    Других серверов нет — привязывать аккаунт неоткуда.
                  </div>
                )}
              {!serversQ.loading && sourceServers.length > 0 && (
                <select
                  className="field-input mono"
                  value={sourceServerId ?? ""}
                  disabled={pending}
                  onChange={(e) => {
                    setErr(null);
                    setSourceServerId(e.target.value || null);
                  }}
                >
                  <option value="">— выберите сервер —</option>
                  {sourceServers.map((s) => (
                    <option key={s.id} value={s.id}>
                      {serverLabel(s)}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* ── Шаг 2: аккаунт источника ── */}
            {sourceServerId && (
              <div>
                <label className="field-label flex items-center gap-1">
                  <User className="w-3.5 h-3.5" /> Аккаунт для привязки
                </label>
                {accountsQ.loading && (
                  <div className="text-xs text-dim py-2">
                    Загрузка аккаунтов…
                  </div>
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
                      У этого сервера нет аккаунтов, которые ещё не привязаны к
                      текущему.
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
