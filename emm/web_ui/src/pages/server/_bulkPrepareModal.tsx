/**
 * Модалка массового prepare выбранных серверов (`POST /servers/prepare-batch`).
 *
 * На каждый сервер — своя строка с выбором режима bootstrap-кред: по умолчанию
 * «привязанная учётка» (выбор из привязанных к серверу и доступных оператору
 * server_account'ов), либо «ручной ввод» (login+password, опц. SSH-ключ) — те
 * же поля, что у single-prepare (переиспользуем `BootstrapCredsFields`). Если у
 * сервера нет привязанных доступных учёток — режим стартует на ручном и строка
 * подсвечивается.
 *
 * Привязанные учётки достаём одним dept-wide запросом и раскладываем по серверам
 * через `server_ids` карточки аккаунта (тот же client-side фильтр доступности,
 * что в single-prepare; финальную проверку прав делает backend). На отправке
 * формы сворачиваются в `items` и уходят batch'ем; ответ показывается per-server
 * (`dispatched` со ссылкой на задачу / `failed` с RU-причиной).
 */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link } from "react-router-dom";
import {
  Play,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  XCircle,
  X,
} from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { prepareServersBatch } from "@/api/server/servers";
import { listAccounts } from "@/api/server/accounts";
import {
  filterAccessibleAccounts,
  prepareBatchReasonRu,
} from "@/pages/server/_serverShared";
import {
  BootstrapCredsFields,
  bootstrapFormToBody,
  bootstrapFormValid,
  emptyBootstrapForm,
  type BootstrapCredsForm,
} from "@/pages/server/tabs/_bootstrapCredsModal";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
  ServerPrepareBatchItem,
  ServerPrepareBatchResponse,
} from "@/api/server/types";

export function BulkPrepareModal({
  servers,
  onClose,
  onDone,
}: {
  servers: Server[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { persona } = usePersona();
  const [forms, setForms] = useState<Record<string, BootstrapCredsForm>>({});
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<ServerPrepareBatchResponse | null>(null);

  // Привязанные учётки тянем одним dept-wide запросом; раскладку по серверам
  // делаем через `server_ids` карточки аккаунта.
  const accountsQ = useQuery(() => listAccounts({ limit: 200 }), []);
  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    const items = data?.items ?? [];
    return filterAccessibleAccounts(items, persona);
  }, [accountsQ.data, persona]);

  const linkedByServer = useMemo(() => {
    const m = new Map<string, ServerAccount[]>();
    for (const s of servers) {
      m.set(
        s.id,
        accounts.filter((a) => a.server_ids.includes(s.id)),
      );
    }
    return m;
  }, [servers, accounts]);

  // После загрузки учёток инициализируем форму каждого сервера: account-режим,
  // если у сервера есть привязанные доступные учётки, иначе ручной ввод.
  useEffect(() => {
    if (accountsQ.loading) return;
    setForms((prev) => {
      const next = { ...prev };
      for (const s of servers) {
        if (!next[s.id]) {
          next[s.id] = emptyBootstrapForm(linkedByServer.get(s.id) ?? []);
        }
      }
      return next;
    });
  }, [servers, linkedByServer, accountsQ.loading]);

  const serverName = (s: Server) => s.display_name ?? s.hostname;
  const byId = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of servers) m.set(s.id, s);
    return m;
  }, [servers]);

  const ready = !accountsQ.loading && Object.keys(forms).length === servers.length;
  const valid = useMemo(
    () => ready && servers.every((s) => bootstrapFormValid(forms[s.id])),
    [ready, servers, forms],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setErr(null);
    setPending(true);
    try {
      const items: ServerPrepareBatchItem[] = servers.map((s) => ({
        server_id: s.id,
        ...bootstrapFormToBody(forms[s.id]),
      }));
      const res = await prepareServersBatch({ items });
      setResult(res);
    } catch (e) {
      setErr(apiErrMsg(e, "Массовый prepare не удался"));
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
          style={{ maxWidth: 680 }}
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Play className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Подготовить выбранные ({servers.length})
            </Dialog.Title>
          </div>

          {result ? (
            <>
              <div className="modal-body flex flex-col gap-3">
                <div className="flex items-center gap-3 text-sm flex-wrap">
                  <span className="badge badge-accent mono">
                    batch {result.batch_id}
                  </span>
                  <span className="flex items-center gap-1 text-ok">
                    <CheckCircle2 className="w-4 h-4" /> поставлено:{" "}
                    {result.dispatched.length}
                  </span>
                  {result.failed.length > 0 && (
                    <span className="flex items-center gap-1 text-warn">
                      <XCircle className="w-4 h-4" /> пропущено:{" "}
                      {result.failed.length}
                    </span>
                  )}
                </div>

                {result.dispatched.length > 0 && (
                  <div className="flex flex-col gap-1">
                    {result.dispatched.map((d) => (
                      <div
                        key={d.server_id}
                        className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5 text-ok shrink-0" />
                        <span className="flex-1 min-w-0 truncate">
                          {d.server_name ??
                            (byId.get(d.server_id) &&
                              serverName(byId.get(d.server_id)!)) ??
                            d.server_id}
                        </span>
                        <span className="badge badge-ok text-[10px]">
                          {d.status}
                        </span>
                        <Link
                          to={`/tasks/${d.task_id}`}
                          className="btn btn-sm flex items-center gap-1"
                          title="Открыть страницу задачи"
                        >
                          <span className="mono text-[11px]">{d.task_id}</span>
                          <ArrowRight className="w-3.5 h-3.5" />
                        </Link>
                      </div>
                    ))}
                  </div>
                )}

                {result.failed.length > 0 && (
                  <div className="flex flex-col gap-1">
                    {result.failed.map((f) => (
                      <div
                        key={f.server_id}
                        className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
                      >
                        <XCircle className="w-3.5 h-3.5 text-warn shrink-0" />
                        <span className="flex-1 min-w-0 truncate">
                          {f.server_name ??
                            (byId.get(f.server_id) &&
                              serverName(byId.get(f.server_id)!)) ??
                            f.server_id}
                        </span>
                        <span className="text-xs text-dim text-right">
                          {prepareBatchReasonRu(f.reason)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="text-[11px] text-dim">
                  Прогресс самих задач смотрите на странице задачи или в разделе
                  «Задачи» под Серверами.
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
                  На каждый сервер — свой режим bootstrap-кред: привязанная
                  учётка (server_service сам расшифрует её пароль) либо ручной
                  ввод логина и пароля.
                </Dialog.Description>

                {err && <div className="alert-danger text-sm">{err}</div>}

                {accountsQ.loading ? (
                  <div className="text-xs text-dim">
                    Загружаем привязанные учётки…
                  </div>
                ) : (
                  <div className="flex flex-col gap-2 max-h-[22rem] overflow-y-auto border border-token rounded p-2">
                    {servers.map((s) => {
                      const form = forms[s.id];
                      if (!form) return null;
                      const linked = linkedByServer.get(s.id) ?? [];
                      const incomplete = !bootstrapFormValid(form);
                      return (
                        <div
                          key={s.id}
                          className="flex flex-col gap-2 py-2 border-b border-token last:border-b-0"
                        >
                          <div className="flex items-center gap-2 text-sm">
                            <span className="mono truncate flex-1">
                              {serverName(s)}
                            </span>
                            {linked.length === 0 && (
                              <span
                                className="badge badge-warn text-[10px]"
                                title="Нет привязанных доступных учёток — заполните креды вручную"
                              >
                                нет привязанных учёток
                              </span>
                            )}
                            {incomplete && (
                              <span className="text-danger text-[11px]">
                                заполните креды
                              </span>
                            )}
                          </div>
                          <BootstrapCredsFields
                            value={form}
                            onChange={(next) =>
                              setForms((prev) => ({ ...prev, [s.id]: next }))
                            }
                            accounts={linked}
                            disabled={pending}
                            compact
                          />
                        </div>
                      );
                    })}
                  </div>
                )}
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
                  className="btn btn-primary flex items-center gap-1"
                  disabled={pending || !valid}
                >
                  <Play className="w-4 h-4" />
                  {pending ? "Запускаем…" : "Подготовить"}
                </button>
              </div>
              {ready && !valid && (
                <div className="px-4 pb-3 text-[11px] text-dim flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" />
                  Для каждого сервера выберите учётку или заполните логин и
                  пароль.
                </div>
              )}
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
