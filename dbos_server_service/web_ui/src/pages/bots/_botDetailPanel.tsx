import { useCallback, useMemo, useState } from "react";
import {
  Bot,
  KeyRound,
  RotateCw,
  EyeOff,
  Power,
  Trash2,
  ShieldCheck,
  User,
  Clock,
  Edit3,
  Copy,
  AlertTriangle,
} from "lucide-react";
import * as botsApi from "@/api/auth/bots";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { botMutationCaps } from "@/lib/rbac";
import { useServiceLabel } from "@/lib/labels";
import { BotRoleAssign } from "@/pages/users/_botRoleAssign";
import type {
  Bot as BotItem,
  BotTokenCreateResponse,
} from "@/api/auth/types";

/**
 * Полная карточка бота для разделов /bots (account_admin / dep_admin).
 * Покрывает:
 *  - идентификацию + allowed_services с inline-edit,
 *  - таблицу выпущенных токенов с revoke,
 *  - issue / rotate / disable / enable,
 *  - таблицу service-ролей с revoke + назначение через BotRoleAssign.
 *
 * Delete намеренно отсутствует — auth_service не предоставляет
 * DELETE /bots/{id}; вместо этого используется disable + revoke.
 */
export function BotDetailFullPanel({
  bot,
  deptLabel,
  onChanged,
}: {
  bot: BotItem;
  deptLabel: string;
  onChanged: () => void;
}) {
  const { persona } = usePersona();
  const caps = botMutationCaps(persona, bot.department_id ?? null);
  const canEdit = caps.rotateToken || caps.revokeToken || caps.manageRoles;

  const tokensQ = useQuery(() => botsApi.listBotTokens(bot.id), [bot.id]);
  const rolesQ = useQuery(() => botsApi.listBotRoles(bot.id), [bot.id]);

  const tokens = tokensQ.data ?? [];
  const activeTokens = useMemo(
    () => tokens.filter((t) => !t.revoked_at),
    [tokens],
  );
  const roles = rolesQ.data ?? [];

  const [issued, setIssued] = useState<BotTokenCreateResponse | null>(null);
  const [tokenName, setTokenName] = useState("");
  const expBounds = useMemo(() => tokenExpBounds(), []);
  const [tokenExpires, setTokenExpires] = useState(expBounds.default);
  const expInvalid =
    !tokenExpires ||
    tokenExpires < expBounds.min ||
    tokenExpires > expBounds.max;
  const expIso = () => new Date(`${tokenExpires}T23:59:59Z`).toISOString();

  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const refetchAll = useCallback(() => {
    tokensQ.refetch();
    rolesQ.refetch();
    onChanged();
  }, [tokensQ, rolesQ, onChanged]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setErr(null);
      setInfo(null);
      setPending(true);
      try {
        await fn();
        refetchAll();
      } catch (e) {
        if (e instanceof ApiError) setErr(`${e.errorCode}: ${e.message}`);
        else if (e instanceof Error) setErr(e.message);
        else setErr(String(e));
      } finally {
        setPending(false);
      }
    },
    [refetchAll],
  );

  const copy = (token: string) => {
    try {
      void navigator.clipboard.writeText(token);
    } catch {
      // ignore
    }
  };

  return (
    <>
      {/* Header */}
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded surface-2 border border-token flex items-center justify-center">
          <Bot className="w-6 h-6 text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">{bot.name}</h1>
            <span
              className={`badge ${bot.status === "active" ? "badge-ok" : "badge-warn"}`}
            >
              {bot.status}
            </span>
            <span className="badge">bot</span>
            {!canEdit && (
              <span className="badge badge-warn" title={caps.reason}>
                read-only
              </span>
            )}
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span>{deptLabel}</span>
            <span>·</span>
            <span className="mono">{bot.id}</span>
            {bot.description && (
              <>
                <span>·</span>
                <span>{bot.description}</span>
              </>
            )}
          </div>
          <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
            {bot.created_by && (
              <span className="flex items-center gap-1">
                <User className="w-3 h-3" /> created_by{" "}
                <span className="mono">{bot.created_by}</span>
              </span>
            )}
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" /> создан{" "}
              {bot.created_at.slice(0, 10)}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <button
            className="btn flex items-center gap-1"
            disabled={!caps.manageRoles || pending}
            title={caps.manageRoles ? undefined : caps.reason}
            onClick={() => {
              const next = window.prompt(
                "Новое description:",
                bot.description ?? "",
              );
              if (next === null) return;
              run(() => botsApi.patchBot(bot.id, { description: next }));
            }}
          >
            <Edit3 className="w-4 h-4" /> Edit
          </button>
          <button
            className="btn flex items-center gap-1"
            disabled={!caps.manageRoles || pending}
            title={caps.manageRoles ? undefined : caps.reason}
            onClick={() =>
              run(() =>
                bot.status === "active"
                  ? botsApi.disableBot(bot.id)
                  : botsApi.enableBot(bot.id),
              )
            }
          >
            {bot.status === "active" ? (
              <EyeOff className="w-4 h-4" />
            ) : (
              <Power className="w-4 h-4" />
            )}
            {bot.status === "active" ? "Disable" : "Enable"}
          </button>
          <button
            className="btn btn-danger flex items-center gap-1"
            disabled={!caps.delete || pending}
            title={caps.delete ? undefined : caps.reason}
            onClick={() =>
              setInfo(
                "Полное удаление бота не предусмотрено auth_service. " +
                  "Используйте Disable + revoke всех токенов и service-ролей; " +
                  "снос вместе с отделом — через DELETE /departments/{id}.",
              )
            }
          >
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        </div>
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        {err && <div className="col-span-2 alert-danger">{err}</div>}
        {info && (
          <div className="col-span-2 alert-block">
            <div className="flex items-start gap-2">
              <span className="text-sm flex-1">{info}</span>
              <button className="btn btn-sm" onClick={() => setInfo(null)}>
                закрыть
              </button>
            </div>
          </div>
        )}
        {issued && (
          <div className="col-span-2 alert-block">
            <div className="text-xs uppercase mb-1">
              Новый bot-token (показывается один раз)
            </div>
            <div className="flex items-center gap-2">
              <span className="mono text-xs break-all flex-1">
                {issued.token}
              </span>
              <button
                className="btn btn-sm flex items-center gap-1"
                onClick={() => copy(issued.token)}
              >
                <Copy className="w-3 h-3" /> Copy
              </button>
              <button className="btn btn-sm" onClick={() => setIssued(null)}>
                закрыть
              </button>
            </div>
            <div className="text-[11px] text-dim mt-1">
              Hash хранится в БД, чистый токен — только сейчас.
            </div>
          </div>
        )}

        {/* Identification */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Bot className="w-4 h-4" /> Идентификация
          </div>
          <div className="grid grid-cols-2 gap-x-6 text-sm">
            <div>
              <StatRow k="name" v={<span className="mono">{bot.name}</span>} />
              <StatRow k="id" v={<span className="mono">{bot.id}</span>} />
              <StatRow k="identity_type" v={<span className="mono">bot</span>} />
            </div>
            <div>
              <StatRow k="dept" v={deptLabel} />
              <StatRow
                k="status"
                v={
                  <span
                    className={`badge ${bot.status === "active" ? "badge-ok" : "badge-warn"}`}
                  >
                    {bot.status}
                  </span>
                }
              />
              <StatRow
                k="created_by"
                v={<span className="mono">{bot.created_by ?? "—"}</span>}
              />
            </div>
          </div>
        </div>

        {/* Allowed services */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2 justify-between">
            <span className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" /> Allowed services
            </span>
            <button
              className="btn btn-sm flex items-center gap-1"
              disabled={!caps.manageRoles || pending}
              title={caps.manageRoles ? undefined : caps.reason}
              onClick={() => {
                const next = window.prompt(
                  "allowed_services (csv):",
                  bot.allowed_services.join(","),
                );
                if (next === null) return;
                const list = next
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean);
                run(() =>
                  botsApi.patchBot(bot.id, { allowed_services: list }),
                );
              }}
            >
              <Edit3 className="w-3 h-3" /> Edit
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {bot.allowed_services.length === 0 ? (
              <span className="text-xs text-dim italic">нет</span>
            ) : (
              bot.allowed_services.map((s) => (
                <span key={s} className="badge badge-accent mono">
                  {s}
                </span>
              ))
            )}
          </div>
        </div>

        {/* Tokens */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <KeyRound className="w-4 h-4" /> Tokens
            {activeTokens.length > 0 && (
              <span className="text-dim">· {activeTokens.length} active</span>
            )}
            {tokensQ.loading && <span className="text-dim">…</span>}
          </div>
          {tokensQ.error && (
            <div className="alert-danger mb-2">
              {tokensQ.error instanceof ApiError
                ? `${tokensQ.error.errorCode}: ${tokensQ.error.message}`
                : tokensQ.error.message}
            </div>
          )}

          {!tokensQ.loading && tokens.length === 0 && (
            <div className="empty-card text-sm">
              Токены не выпускались.
            </div>
          )}

          {tokens.length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">name</th>
                  <th className="pb-2 pr-3">created</th>
                  <th className="pb-2 pr-3">expires</th>
                  <th className="pb-2 pr-3">last used</th>
                  <th className="pb-2 pr-3">status</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {tokens.map((t) => {
                  const revoked = !!t.revoked_at;
                  return (
                    <tr key={t.token_id} className="border-t border-token">
                      <td className="py-2">
                        <div className="mono">{t.name}</div>
                        <div className="text-[10px] text-dim mono">
                          {t.token_id}
                        </div>
                      </td>
                      <td className="text-xs text-dim mono">
                        {t.created_at.slice(0, 10)}
                      </td>
                      <td className="text-xs text-dim mono">
                        {t.expires_at ? t.expires_at.slice(0, 10) : "—"}
                      </td>
                      <td className="text-xs text-dim mono">
                        {t.last_used_at ? t.last_used_at.slice(0, 10) : "—"}
                      </td>
                      <td>
                        {revoked ? (
                          <span className="badge badge-warn">revoked</span>
                        ) : (
                          <span className="badge badge-ok">active</span>
                        )}
                      </td>
                      <td>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={!caps.revokeToken || pending || revoked}
                          title={caps.revokeToken ? undefined : caps.reason}
                          onClick={() =>
                            run(() =>
                              botsApi.revokeBotToken(bot.id, t.token_id),
                            )
                          }
                        >
                          revoke
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {activeTokens.length > 1 && (
            <div className="alert-danger mt-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-[2px]" />
              <div className="text-sm">
                У бота {activeTokens.length} активных токенов. По инварианту должен
                быть один — отзовите лишние или выпустите новый (issue/rotate
                автоматически закроют активные).
              </div>
            </div>
          )}

          {canEdit && (
            <div className="mt-3 flex gap-2 flex-wrap items-center">
              <input
                className="input flex-1"
                placeholder={
                  activeTokens.length === 0
                    ? "имя нового токена"
                    : "имя нового токена (опционально)"
                }
                value={tokenName}
                onChange={(e) => setTokenName(e.target.value)}
              />
              <input
                className="input"
                type="date"
                title="Срок действия (до) — max 6 месяцев"
                value={tokenExpires}
                min={expBounds.min}
                max={expBounds.max}
                onChange={(e) => setTokenExpires(e.target.value)}
              />
              <button
                className="btn btn-primary flex items-center gap-1"
                disabled={
                  !caps.rotateToken ||
                  pending ||
                  expInvalid ||
                  (activeTokens.length === 0 && !tokenName.trim())
                }
                title={
                  expInvalid
                    ? "Срок действия обязателен и не должен превышать 6 месяцев"
                    : activeTokens.length === 0
                      ? "Выпустить токен"
                      : "Выпустить новый — старые активные будут отозваны"
                }
                onClick={() =>
                  run(async () => {
                    const name =
                      tokenName.trim() ||
                      (activeTokens[0]?.name ?? "");
                    if (!name) return;
                    const res =
                      activeTokens.length === 0
                        ? await botsApi.issueBotToken(bot.id, {
                            name,
                            expires_at: expIso(),
                          })
                        : await botsApi.rotateBotToken(bot.id, {
                            name,
                            expires_at: expIso(),
                          });
                    setIssued(res);
                    setTokenName("");
                  })
                }
              >
                {activeTokens.length === 0 ? (
                  <>
                    <KeyRound className="w-4 h-4" /> Issue
                  </>
                ) : (
                  <>
                    <RotateCw className="w-4 h-4" /> Rotate
                  </>
                )}
              </button>
            </div>
          )}
        </div>

        {/* Service roles */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4" /> Service-роли · {roles.length}
            {rolesQ.loading && <span className="text-dim">…</span>}
          </div>
          {rolesQ.error && (
            <div className="alert-danger mb-2">
              {rolesQ.error instanceof ApiError
                ? `${rolesQ.error.errorCode}: ${rolesQ.error.message}`
                : rolesQ.error.message}
            </div>
          )}
          {!rolesQ.loading && roles.length === 0 && (
            <div className="empty-card text-sm">Роли боту не выданы.</div>
          )}
          {roles.length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">service</th>
                  <th className="pb-2 pr-3">roles</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {roles.map((r) => (
                  <tr
                    key={r.service_name}
                    className="border-t border-token align-top"
                  >
                    <td className="py-2 text-xs">
                      <ServiceInline name={r.service_name} />
                    </td>
                    <td className="text-xs">
                      <div className="flex flex-wrap gap-1">
                        {r.roles.map((role) => (
                          <span key={role} className="badge badge-accent">
                            {role}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="text-right">
                      <button
                        className="btn btn-sm btn-danger"
                        disabled={!caps.manageRoles || pending}
                        title={caps.manageRoles ? undefined : caps.reason}
                        onClick={() =>
                          run(() =>
                            botsApi.revokeBotRoles(bot.id, r.service_name),
                          )
                        }
                      >
                        revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {canEdit && (
            <BotRoleAssign
              departmentId={bot.department_id ?? null}
              allowedServices={bot.allowed_services}
              alreadyAssigned={new Set(roles.map((r) => r.service_name))}
              disabled={!caps.manageRoles || pending}
              reason={caps.manageRoles ? undefined : caps.reason}
              onAssign={(service, list) =>
                run(() =>
                  botsApi.assignBotRoles(bot.id, {
                    service_name: service,
                    roles: list,
                  }),
                )
              }
            />
          )}
        </div>
      </div>
    </>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span>{v}</span>
    </div>
  );
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}

function tokenExpBounds(): { min: string; max: string; default: string } {
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const today = new Date();
  const min = new Date(today);
  min.setDate(min.getDate() + 1);
  const max = new Date(today);
  max.setDate(max.getDate() + 180);
  const def = new Date(today);
  def.setDate(def.getDate() + 90);
  return { min: fmt(min), max: fmt(max), default: fmt(def) };
}
