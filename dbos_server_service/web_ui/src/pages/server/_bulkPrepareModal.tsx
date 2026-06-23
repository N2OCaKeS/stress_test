/**
 * Модалка массового prepare выбранных серверов.
 *
 * Backend (`POST /servers/prepare/bulk`) ждёт bootstrap-креды per-server:
 * `username_b64` / `password_b64` (base64 plaintext) и опционально приватный
 * SSH-ключ. Чтобы не заполнять каждую строку руками, модалка предлагает общий
 * набор кред с переключателем «применить ко всем» — оператор заполняет один
 * раз, при необходимости переопределяя отдельные серверы. На отправке креды
 * кодируются `toBase64` и уходят единым batch'ем; ответ показывается per-server
 * (queued / skipped + reason).
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Play, AlertCircle, CheckCircle2, X } from "lucide-react";
import { toBase64 } from "@/lib/base64";
import { apiErrMsg } from "@/api/client";
import { prepareServersBulk } from "@/api/server/servers";
import type {
  BulkPrepareItem,
  BulkPrepareResponse,
  Server,
} from "@/api/server/types";

interface Creds {
  username: string;
  password: string;
  sshPrivateKey: string;
}

const EMPTY: Creds = { username: "", password: "", sshPrivateKey: "" };

export function BulkPrepareModal({
  servers,
  onClose,
  onDone,
}: {
  servers: Server[];
  onClose: () => void;
  onDone: () => void;
}) {
  // Общие креды для всех серверов; per-server переопределение — в `overrides`.
  const [shared, setShared] = useState<Creds>(EMPTY);
  const [overrides, setOverrides] = useState<Record<string, Creds>>({});
  // Какие серверы используют собственные креды вместо общих.
  const [perServer, setPerServer] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<BulkPrepareResponse | null>(null);

  function credsFor(id: string): Creds {
    return perServer.has(id) ? overrides[id] ?? EMPTY : shared;
  }

  function setOverride(id: string, patch: Partial<Creds>) {
    setOverrides((prev) => ({
      ...prev,
      [id]: { ...(prev[id] ?? EMPTY), ...patch },
    }));
  }

  function togglePerServer(id: string) {
    setPerServer((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else {
        next.add(id);
        // Засеваем override общими кредами, чтобы не начинать с пустого.
        setOverrides((o) => ({ ...o, [id]: o[id] ?? { ...shared } }));
      }
      return next;
    });
  }

  // Все серверы должны иметь непустые username/password (общие или свои).
  const valid = useMemo(() => {
    return servers.every((s) => {
      const c = credsFor(s.id);
      return c.username.trim().length > 0 && c.password.length > 0;
    });
    // credsFor зависит от shared/overrides/perServer — пересчитываем при их смене.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servers, shared, overrides, perServer]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setErr(null);
    setPending(true);
    try {
      const items: BulkPrepareItem[] = servers.map((s) => {
        const c = credsFor(s.id);
        return {
          server_id: s.id,
          username_b64: toBase64(c.username.trim()),
          password_b64: toBase64(c.password),
          ...(c.sshPrivateKey.trim()
            ? { ssh_private_key_b64: toBase64(c.sshPrivateKey) }
            : {}),
        };
      });
      const res = await prepareServersBulk({ items });
      setResult(res);
    } catch (e) {
      setErr(apiErrMsg(e, "Массовый prepare не удался"));
    } finally {
      setPending(false);
    }
  }

  const serverName = (s: Server) => s.display_name ?? s.hostname;
  const byId = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of servers) m.set(s.id, s);
    return m;
  }, [servers]);

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: 640 }}
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
                <div className="flex items-center gap-3 text-sm">
                  <span className="badge badge-ok flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" /> queued:{" "}
                    {result.queued_count}
                  </span>
                  <span className="badge badge-warn">
                    skipped: {result.skipped_count}
                  </span>
                </div>
                <div className="surface-2 border border-token rounded overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[11px] uppercase text-dim border-b border-token">
                        <th className="text-left px-3 py-2 font-medium">сервер</th>
                        <th className="text-left px-3 py-2 font-medium">статус</th>
                        <th className="text-left px-3 py-2 font-medium">причина / task</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.results.map((r) => {
                        const s = byId.get(r.server_id);
                        const ok = r.status === "queued";
                        return (
                          <tr
                            key={r.server_id}
                            className="border-b border-token last:border-b-0"
                          >
                            <td className="px-3 py-1.5 mono text-xs">
                              {s ? serverName(s) : r.server_id}
                            </td>
                            <td className="px-3 py-1.5">
                              <span
                                className={`badge ${ok ? "badge-ok" : "badge-warn"}`}
                              >
                                {r.status}
                              </span>
                            </td>
                            <td className="px-3 py-1.5 text-xs text-dim">
                              {r.reason ? (
                                r.reason
                              ) : r.task_id ? (
                                <span className="mono">{r.task_id}</span>
                              ) : (
                                "—"
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="text-[11px] text-dim">
                  Прогресс самих задач смотрите в разделе «Задачи» под Серверами.
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
                  Bootstrap-креды, под которыми worker зайдёт на серверы для
                  management-цикла. По умолчанию общие для всех; отдельные серверы
                  можно переопределить ниже.
                </Dialog.Description>

                {err && <div className="alert-danger text-sm">{err}</div>}

                <div className="surface-2 border border-token rounded p-3 flex flex-col gap-2">
                  <div className="text-xs font-medium text-dim">
                    Общие креды (для серверов без своих)
                  </div>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="field-label">username</span>
                    <input
                      className="field-input mono"
                      value={shared.username}
                      onChange={(e) =>
                        setShared((c) => ({ ...c, username: e.target.value }))
                      }
                      disabled={pending}
                      autoComplete="off"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="field-label">password</span>
                    <input
                      className="field-input mono"
                      type="password"
                      value={shared.password}
                      onChange={(e) =>
                        setShared((c) => ({ ...c, password: e.target.value }))
                      }
                      disabled={pending}
                      autoComplete="off"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="field-label">
                      SSH private key (опционально)
                    </span>
                    <textarea
                      className="field-input mono text-xs"
                      rows={2}
                      value={shared.sshPrivateKey}
                      onChange={(e) =>
                        setShared((c) => ({
                          ...c,
                          sshPrivateKey: e.target.value,
                        }))
                      }
                      disabled={pending}
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----…"
                    />
                  </label>
                </div>

                <div className="flex flex-col gap-1">
                  <div className="text-xs font-medium text-dim">Серверы</div>
                  <div className="flex flex-col gap-1 max-h-56 overflow-y-auto border border-token rounded p-2">
                    {servers.map((s) => {
                      const own = perServer.has(s.id);
                      const c = credsFor(s.id);
                      const missing =
                        c.username.trim().length === 0 || c.password.length === 0;
                      return (
                        <div
                          key={s.id}
                          className="flex flex-col gap-1 py-1 border-b border-token last:border-b-0"
                        >
                          <div className="flex items-center gap-2 text-sm">
                            <span className="mono truncate flex-1">
                              {serverName(s)}
                            </span>
                            {missing && (
                              <span
                                className="text-danger text-[11px]"
                                title="Не заданы username/password"
                              >
                                нет кред
                              </span>
                            )}
                            <label className="inline-flex items-center gap-1 text-[11px] text-dim">
                              <input
                                type="checkbox"
                                checked={own}
                                onChange={() => togglePerServer(s.id)}
                                disabled={pending}
                              />
                              свои креды
                            </label>
                          </div>
                          {own && (
                            <div className="grid grid-cols-2 gap-1">
                              <input
                                className="field-input mono text-xs"
                                placeholder="username"
                                value={overrides[s.id]?.username ?? ""}
                                onChange={(e) =>
                                  setOverride(s.id, { username: e.target.value })
                                }
                                disabled={pending}
                                autoComplete="off"
                              />
                              <input
                                className="field-input mono text-xs"
                                type="password"
                                placeholder="password"
                                value={overrides[s.id]?.password ?? ""}
                                onChange={(e) =>
                                  setOverride(s.id, { password: e.target.value })
                                }
                                disabled={pending}
                                autoComplete="off"
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
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
              {!valid && (
                <div className="px-4 pb-3 text-[11px] text-dim flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" />
                  Заполните username и password для всех серверов (общие или свои).
                </div>
              )}
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
