import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Bot as BotIcon,
  KeyRound,
  ShieldCheck,
  Cog,
  ListTree,
  GitCompareArrows,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
  Copy,
  Plug,
  Trash2,
  Power,
  Edit3,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { BOTS, BOT_ASSIGNMENTS, ROLES, DIRECT_GRANTS } from "@/mocks/permissions";
import { DEPTS, userById } from "@/mocks/auth";
import { usePersona } from "@/contexts/PersonaContext";
import { botMutationCaps } from "@/lib/rbac";
import {
  computeEffectiveBot,
  computeBotDrift,
  computeDiff,
  applyMutation,
  defaultStore,
  type EffectiveEntry,
  type Mutation,
} from "./permissionGraph";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import * as botsApi from "@/api/auth/bots";
import { ApiError } from "@/api/client";
import type { BotTokenCreateResponse } from "@/api/auth/types";

export function BotDetail() {
  const { id } = useParams<{ id: string }>();
  const { persona } = usePersona();
  const bot = BOTS.find((b) => b.id === id || b.name === id);
  const caps = botMutationCaps(persona, bot?.owner_dept ?? null);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [diffMutation, setDiffMutation] = useState<Mutation | null>(null);

  const botId = bot?.id ?? "";
  const ba = botId ? BOT_ASSIGNMENTS[botId] : undefined;
  const effective = useMemo(() => (botId ? computeEffectiveBot(botId) : []), [botId]);
  const drift = useMemo(() => (botId ? computeBotDrift(botId) : { added: [], removed: [] }), [botId]);

  const removeRoleDiff = useMemo(() => {
    if (!ba || ba.roles.length === 0 || !botId) return null;
    const roleId = (diffMutation && diffMutation.kind === "remove_role" ? diffMutation.role_id : ba.roles[0].role_id);
    const after = computeEffectiveBot(
      botId,
      applyMutation(defaultStore, {
        kind: "remove_role",
        subject: "bot",
        user_id: botId,
        role_id: roleId,
      }),
    );
    return { roleId, diff: computeDiff(effective, after) };
  }, [ba, botId, effective, diffMutation]);

  const toggleExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!bot) {
    return (
      <Shell breadcrumb="auth_service / users / bot">
        <section className="flex-1 overflow-y-auto p-8">
          <div className="empty-card danger">
            Bot <span className="mono">{id}</span> не найден.
            <div className="mt-3">
              <Link to="/users" className="btn">
                <ArrowLeft className="w-4 h-4 inline mr-1" /> К пользователям
              </Link>
            </div>
          </div>
        </section>
      </Shell>
    );
  }

  const dept = DEPTS.find((d) => d.id === bot.owner_dept);
  const grants = DIRECT_GRANTS.filter((g) => g.subject_kind === "bot" && g.subject_id === bot.id);
  const createdByUser = userById(bot.created_by);
  const createdByLabel = createdByUser ? createdByUser.username : `${bot.created_by} (удалён)`;

  return (
    <Shell breadcrumb={`auth_service / users / bot / ${bot.name}`}>
      <section className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* HEADER */}
        <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
          <Link to="/users" className="btn btn-ghost mt-1">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="w-12 h-12 rounded-full surface-2 border border-token flex items-center justify-center">
            <BotIcon className="w-6 h-6 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate mono">{bot.name}</h1>
              <span className={`badge badge-${bot.token_status === "active" ? "ok" : bot.token_status === "rotated" ? "warn" : "danger"}`}>
                token: {bot.token_status}
              </span>
              <span className="badge">dept · {dept?.name ?? bot.owner_dept}</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="mono">{bot.id}</span>
              <span>·</span>
              <span>создан {createdByUser ? (
                <Link to={`/users/${createdByUser.id}`} className="mono hover-bg">{createdByLabel}</Link>
              ) : (
                <span className="mono text-dim">{createdByLabel}</span>
              )} · {bot.created_at.slice(0, 10)}</span>
              <span>·</span>
              <span>last used <span className="mono">{bot.last_used.replace("T", " ").slice(0, 16)}</span></span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {!caps.rotateToken && !caps.revokeToken && !caps.delete && (
              <span className="badge badge-warn" title={caps.reason}>
                read-only
              </span>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 grid grid-cols-2 gap-5 auto-rows-min">
          <BotLiveData botId={bot.id} caps={caps} />
          {/* Identification */}
          <Section icon={<BotIcon className="w-4 h-4" />} title="Идентификация" className="col-span-2">
            <div className="grid grid-cols-2 gap-x-6 text-sm">
              <div>
                <StatRow k="name" v={<span className="mono">{bot.name}</span>} />
                <StatRow k="id" v={<span className="mono">{bot.id}</span>} />
                <StatRow k="owner_dept" v={dept?.name ?? bot.owner_dept} />
              </div>
              <div>
                <StatRow k="token_status" v={
                  <span className={`badge badge-${bot.token_status === "active" ? "ok" : bot.token_status === "rotated" ? "warn" : "danger"}`}>
                    {bot.token_status}
                  </span>
                } />
                <StatRow k="created_by" v={createdByUser ? (
                  <Link to={`/users/${createdByUser.id}`} className="mono hover-bg">{createdByLabel}</Link>
                ) : (
                  <span className="mono text-dim" title="user removed or unknown">{createdByLabel}</span>
                )} />
                <StatRow k="last_used" v={<span className="mono">{bot.last_used}</span>} />
              </div>
            </div>
          </Section>

          {/* Service roles */}
          <Section icon={<Cog className="w-4 h-4" />} title="Service-роли" className="col-span-2">
            {!ba || ba.roles.length === 0 ? (
              <div className="text-sm text-dim italic">Роли не выданы.</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">Сервис</th>
                    <th className="pb-2 pr-3">Роль</th>
                    <th className="pb-2 pr-3">Scope</th>
                    <th className="pb-2 pr-3">Granted by</th>
                    <th className="pb-2 pr-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {ba.roles.map((ra) => {
                    const r = ROLES.find((x) => x.id === ra.role_id)!;
                    return (
                      <tr key={ra.role_id} className="border-t border-token">
                        <td className="py-2 mono text-xs">{r.service}</td>
                        <td><span className="badge badge-accent">{r.name}</span></td>
                        <td className="text-xs">
                          {ra.scope_kind === "platform" && <span className="badge">платформа</span>}
                          {ra.scope_kind === "dept" && <span className="badge">отдел · {ra.scope_ref}</span>}
                          {ra.scope_kind === "resource" && <span className="badge">{ra.scope_ref}</span>}
                        </td>
                        <td className="text-xs text-dim"><BotGrantedBy id={ra.granted_by} /></td>
                        <td>
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() => setDiffMutation({ kind: "remove_role", subject: "bot", user_id: bot.id, role_id: ra.role_id })}
                            title="Прикинуть diff"
                          >
                            <GitCompareArrows className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Section>

          {/* Direct grants */}
          <Section icon={<KeyRound className="w-4 h-4" />} title={`Прямые grants · ${grants.length}`} className="col-span-2">
            {grants.length === 0 ? (
              <div className="text-sm text-dim italic">Прямых grants нет.</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">Permission</th>
                    <th className="pb-2 pr-3">Resource</th>
                    <th className="pb-2 pr-3">Granted by</th>
                    <th className="pb-2 pr-3">When</th>
                  </tr>
                </thead>
                <tbody>
                  {grants.map((g) => (
                    <tr key={g.id} className="border-t border-token">
                      <td className="py-2 mono text-xs">{g.permission}</td>
                      <td className="text-xs">
                        <span className={`domain-tag ${g.resource_kind === "secret" ? "tag-secret" : "tag-server"}`}>
                          {g.resource_kind}
                        </span>
                        <span className="mono">{g.resource_id}</span>
                      </td>
                      <td className="text-xs text-dim"><BotGrantedBy id={g.granted_by} /></td>
                      <td className="text-xs text-dim">{g.granted_at.slice(0, 10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          {/* Effective */}
          <Section icon={<ListTree className="w-4 h-4" />} title={`Effective permissions · ${effective.length}`} className="col-span-2">
            <div className="border border-token rounded overflow-hidden">
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase surface-2">
                  <tr>
                    <th className="px-3 py-2 w-8"></th>
                    <th className="px-3 py-2">Permission</th>
                    <th className="px-3 py-2">Service</th>
                    <th className="px-3 py-2">Scope</th>
                    <th className="px-3 py-2">Источники</th>
                  </tr>
                </thead>
                <tbody>
                  {effective.map((e) => {
                    const key = `${e.permission}|${e.scope_ref ?? ""}`;
                    const open = expanded.has(key);
                    return <PermRow key={key} entry={e} open={open} onToggle={() => toggleExpand(key)} />;
                  })}
                </tbody>
              </table>
            </div>
          </Section>

          {/* Drift detection */}
          <Section icon={<AlertTriangle className="w-4 h-4" />} title="Drift vs. spec на момент выдачи" className="col-span-2">
            <div className="text-xs text-dim mb-3">
              Initial spec (на момент выпуска токена): {bot.initial_spec.map((p) => (
                <span key={p} className="mono badge mr-1">{p}</span>
              ))}
            </div>
            {drift.added.length === 0 && drift.removed.length === 0 ? (
              <div className="text-sm text-ok">No drift — текущие права совпадают со spec.</div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <div className="border border-token rounded p-3" style={{ background: "rgba(106,176,76,0.05)" }}>
                  <div className="text-xs uppercase tracking-wider text-ok mb-2">+ добавлено сверх spec · {drift.added.length}</div>
                  {drift.added.length === 0 ? (
                    <div className="text-xs text-dim italic">ничего</div>
                  ) : (
                    <ul className="text-xs">
                      {drift.added.map((p) => (
                        <li key={p} className="mono py-0.5"><span className="text-ok">+</span> {p}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="border border-token rounded p-3" style={{ background: "rgba(244,135,113,0.05)" }}>
                  <div className="text-xs uppercase tracking-wider text-danger mb-2">− из spec пропало · {drift.removed.length}</div>
                  {drift.removed.length === 0 ? (
                    <div className="text-xs text-dim italic">ничего</div>
                  ) : (
                    <ul className="text-xs">
                      {drift.removed.map((p) => (
                        <li key={p} className="mono py-0.5"><span className="text-danger">−</span> {p}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </Section>

          {/* Diff: remove a role */}
          <Section icon={<GitCompareArrows className="w-4 h-4" />} title="Diff: simulated removal of role" className="col-span-2">
            {!removeRoleDiff ? (
              <div className="text-sm text-dim italic">Нет ролей для симуляции.</div>
            ) : (
              <div>
                <div className="text-xs text-dim mb-2">
                  Сценарий: убрать роль <span className="mono">{ROLES.find((r) => r.id === removeRoleDiff.roleId)?.name}</span>
                </div>
                {(() => {
                  const removed = removeRoleDiff.diff.filter((d) => d.change === "removed");
                  if (removed.length === 0) {
                    return <div className="text-xs text-dim italic">Effective не изменится.</div>;
                  }
                  return (
                    <ul className="text-xs">
                      {removed.map((d) => (
                        <li key={`${d.permission}-${d.scope_ref}`} className="mono py-0.5">
                          <span className="text-danger">−</span> {d.permission} <span className="text-dim">({d.scope_kind})</span>
                        </li>
                      ))}
                    </ul>
                  );
                })()}
              </div>
            )}
          </Section>

        </div>
      </section>
    </Shell>
  );
}

function PermRow({ entry, open, onToggle }: { entry: EffectiveEntry; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-t border-token hover-bg cursor-pointer" onClick={onToggle}>
        <td className="px-3 py-2">{open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}</td>
        <td className="px-3 py-2 mono text-xs">{entry.permission}</td>
        <td className="px-3 py-2 text-xs">{entry.service}</td>
        <td className="px-3 py-2 text-xs">
          {entry.scope_kind === "platform" && <span className="badge">платформа</span>}
          {entry.scope_kind === "dept" && <span className="badge">отдел · {entry.scope_ref}</span>}
          {entry.scope_kind === "resource" && <span className="badge">{entry.scope_ref}</span>}
        </td>
        <td className="px-3 py-2">
          <div className="flex flex-wrap gap-1">
            {entry.sources.map((s) => (
              <span key={`${s.kind}-${s.id}`} className={`badge badge-${s.kind === "direct" ? "warn" : "accent"}`}>
                {s.label}
              </span>
            ))}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="border-t border-token surface-2">
          <td></td>
          <td colSpan={4} className="px-3 py-2">
            <ul className="text-xs ml-3">
              {entry.sources.map((s) => (
                <li key={`tr-${s.kind}-${s.id}`} className="my-1 flex items-center gap-2">
                  <span className="text-dim">→</span>
                  <span className="mono">{s.kind}</span>
                  <span>·</span>
                  <span>{s.label}</span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Live data panel — queries auth_service for the real bot via `listBots`
 * (there is no `GET /bots/{id}` in the API), shows tokens, lists service
 * roles and surfaces the one-time issue/rotate flow.
 *
 * The new bot-token (`token` field) is returned ONCE by the backend; we
 * hold it in local state, render a copy button and a warning banner, and
 * never resend it across navigation.
 */
function BotLiveData({
  botId,
  caps,
}: {
  botId: string;
  caps: ReturnType<typeof botMutationCaps>;
}) {
  const mock = useMockMode();

  const botList = useQuery(
    () => botsApi.listBots({ limit: 200 }),
    [botId],
    { enabled: !mock },
  );
  const tokens = useQuery(
    () => botsApi.listBotTokens(botId),
    [botId],
    { enabled: !mock },
  );
  const rolesQ = useQuery(
    () => botsApi.listBotRoles(botId),
    [botId],
    { enabled: !mock },
  );

  const [issued, setIssued] = useState<BotTokenCreateResponse | null>(null);
  const [newName, setNewName] = useState("");
  const tokenExpBounds = useMemo(() => tokenExpiresBounds(), []);
  const [newExpires, setNewExpires] = useState(tokenExpBounds.default);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [actionInfo, setActionInfo] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const newExpInvalid =
    !newExpires ||
    newExpires < tokenExpBounds.min ||
    newExpires > tokenExpBounds.max;
  const newExpIso = () =>
    new Date(`${newExpires}T23:59:59Z`).toISOString();

  const live = useMemo(
    () => (botList.data ?? []).find((b) => b.id === botId),
    [botList.data, botId],
  );

  const refetchAll = useCallback(() => {
    botList.refetch();
    tokens.refetch();
    rolesQ.refetch();
  }, [botList, tokens, rolesQ]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActionErr(null);
      setActionInfo(null);
      setPending(true);
      try {
        await fn();
        refetchAll();
      } catch (e) {
        if (e instanceof ApiError) {
          setActionErr(`${e.errorCode}: ${e.message}`);
        } else if (e instanceof Error) {
          setActionErr(e.message);
        } else {
          setActionErr(String(e));
        }
      } finally {
        setPending(false);
      }
    },
    [refetchAll],
  );

  const copyToken = useCallback((token: string) => {
    try {
      void navigator.clipboard.writeText(token);
    } catch {
      // ignore — clipboard may be unavailable in dev
    }
  }, []);

  if (mock) return null;

  const loading = botList.loading || tokens.loading || rolesQ.loading;
  const firstErr = [botList.error, tokens.error, rolesQ.error].find(Boolean);

  return (
    <Section
      icon={<Plug className="w-4 h-4" />}
      title="Live data · auth_service"
      className="col-span-2"
    >
      {loading && <div className="spinner">Загрузка…</div>}
      {!loading && firstErr instanceof ApiError && (
        <div className="alert-danger">
          {firstErr.errorCode}: {firstErr.message}
          <button className="btn btn-sm ml-2" onClick={refetchAll}>
            Повторить
          </button>
        </div>
      )}
      {!loading && firstErr && !(firstErr instanceof ApiError) && (
        <div className="alert-danger">{firstErr.message}</div>
      )}

      {!loading && !firstErr && (
        <div className="flex flex-col gap-4">
          {actionErr && <div className="alert-danger">{actionErr}</div>}
          {actionInfo && (
            <div
              className="alert-block"
              style={{ background: "rgba(244,205,113,0.1)" }}
            >
              <div className="flex items-start gap-2">
                <span className="text-warn text-sm flex-1">{actionInfo}</span>
                <button
                  className="btn btn-sm"
                  onClick={() => setActionInfo(null)}
                  title="Скрыть"
                >
                  закрыть
                </button>
              </div>
            </div>
          )}

          {issued && (
            <div
              className="alert-block"
              style={{ background: "rgba(244,205,113,0.1)" }}
            >
              <div className="text-xs uppercase text-dim mb-1">
                Новый bot-token
              </div>
              <div className="flex items-center gap-2">
                <span className="mono text-xs break-all flex-1">
                  {issued.token}
                </span>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  onClick={() => copyToken(issued.token)}
                >
                  <Copy className="w-3 h-3" /> Copy
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => setIssued(null)}
                  title="Скрыть"
                >
                  закрыть
                </button>
              </div>
              <div className="text-xs text-dim mt-1">
                Сохраните токен сейчас — он показывается один раз и в БД хранится только хэш.
              </div>
            </div>
          )}

          {/* Bot meta */}
          {live ? (
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-xs text-dim">name</div>
                <div className="mono">{live.name}</div>
              </div>
              <div>
                <div className="text-xs text-dim">status</div>
                <span
                  className={`badge badge-${live.status === "active" ? "ok" : "warn"}`}
                >
                  {live.status}
                </span>
              </div>
              <div>
                <div className="text-xs text-dim">department_id</div>
                <div className="mono">{live.department_id}</div>
              </div>
              <div>
                <div className="text-xs text-dim">allowed_services</div>
                <div className="flex flex-wrap gap-1">
                  {live.allowed_services.map((s) => (
                    <span key={s} className="badge mono text-xs">
                      {s}
                    </span>
                  ))}
                </div>
              </div>
              <div className="col-span-2 flex gap-2 flex-wrap">
                <button
                  className="btn flex items-center gap-1"
                  disabled={!caps.manageRoles || pending}
                  title={caps.manageRoles ? undefined : caps.reason}
                  onClick={() => {
                    const next = window.prompt(
                      "Новое description:",
                      live.description ?? "",
                    );
                    if (next === null) return;
                    run(() =>
                      botsApi.patchBot(botId, { description: next }),
                    );
                  }}
                >
                  <Edit3 className="w-4 h-4" /> Edit description
                </button>
                <button
                  className="btn flex items-center gap-1"
                  disabled={!caps.manageRoles || pending}
                  title={caps.manageRoles ? undefined : caps.reason}
                  onClick={() =>
                    run(() =>
                      live.status === "active"
                        ? botsApi.disableBot(botId)
                        : botsApi.enableBot(botId),
                    )
                  }
                >
                  <Power className="w-4 h-4" />
                  {live.status === "active" ? "Disable" : "Enable"}
                </button>
                <button
                  className="btn btn-danger flex items-center gap-1"
                  disabled={!caps.delete || pending}
                  title={caps.delete ? undefined : caps.reason}
                  onClick={() => {
                    // auth_service не предоставляет DELETE /bots/{id}: бот
                    // живёт пока существует отдел, а сам бот «гасится» через
                    // disable (status=disabled) + revoke всех токенов и ролей.
                    setActionErr(null);
                    setActionInfo(
                      "Полное удаление бота не предусмотрено auth_service. " +
                        "Используйте Disable + revoke всех токенов и service-ролей; " +
                        "снос вместе с отделом — через DELETE /departments/{id}.",
                    );
                  }}
                >
                  <Trash2 className="w-4 h-4" /> Delete bot
                </button>
              </div>
            </div>
          ) : (
            <div className="empty-card">
              Бот <span className="mono">{botId}</span> не найден в auth_service
              (возможно создан только в моке).
            </div>
          )}

          {/* Token — инвариант: у бота 0 или 1 active. */}
          {(() => {
            const allTokens = tokens.data ?? [];
            const active = allTokens.filter((t) => !t.revoked_at);
            return (
              <div>
                <div className="text-xs uppercase text-dim mb-2">
                  Token{active.length > 0 && ` · ${active.length} active`}
                </div>

                {active.length === 0 && (
                  <>
                    <div className="empty-card">Активного токена нет.</div>
                    <div className="flex gap-2 mt-2 flex-wrap items-center">
                      <input
                        className="input"
                        placeholder="token name"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                      />
                      <input
                        className="input"
                        type="date"
                        title="Срок действия (до) — max 6 месяцев"
                        value={newExpires}
                        min={tokenExpBounds.min}
                        max={tokenExpBounds.max}
                        onChange={(e) => setNewExpires(e.target.value)}
                      />
                      <button
                        className="btn btn-primary flex items-center gap-1"
                        disabled={
                          !caps.rotateToken ||
                          pending ||
                          !newName.trim() ||
                          newExpInvalid
                        }
                        title={
                          newExpInvalid
                            ? "Срок действия обязателен и не должен превышать 6 месяцев"
                            : undefined
                        }
                        onClick={() =>
                          run(async () => {
                            const res = await botsApi.issueBotToken(botId, {
                              name: newName.trim(),
                              expires_at: newExpIso(),
                            });
                            setIssued(res);
                            setNewName("");
                          })
                        }
                      >
                        <KeyRound className="w-4 h-4" /> Issue token
                      </button>
                    </div>
                  </>
                )}

                {active.length === 1 && (
                  <>
                    <table className="w-full text-sm">
                      <tbody>
                        <tr>
                          <td className="text-xs text-dim pr-3 py-1">name</td>
                          <td className="mono text-sm">{active[0].name}</td>
                        </tr>
                        <tr>
                          <td className="text-xs text-dim pr-3 py-1">token_id</td>
                          <td className="mono text-xs">{active[0].token_id}</td>
                        </tr>
                        <tr>
                          <td className="text-xs text-dim pr-3 py-1">created</td>
                          <td className="mono text-xs">
                            {active[0].created_at.replace("T", " ").slice(0, 16)}
                          </td>
                        </tr>
                        <tr>
                          <td className="text-xs text-dim pr-3 py-1">expires</td>
                          <td className="mono text-xs">
                            {active[0].expires_at
                              ? active[0].expires_at.replace("T", " ").slice(0, 16)
                              : "—"}
                          </td>
                        </tr>
                        <tr>
                          <td className="text-xs text-dim pr-3 py-1">last used</td>
                          <td className="mono text-xs">
                            {active[0].last_used_at
                              ? active[0].last_used_at.replace("T", " ").slice(0, 16)
                              : "—"}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                    <div className="flex gap-2 mt-2 flex-wrap items-center">
                      <input
                        className="input"
                        placeholder="новое имя (опционально)"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                      />
                      <input
                        className="input"
                        type="date"
                        title="Срок действия нового токена (до) — max 6 месяцев"
                        value={newExpires}
                        min={tokenExpBounds.min}
                        max={tokenExpBounds.max}
                        onChange={(e) => setNewExpires(e.target.value)}
                      />
                      <button
                        className="btn flex items-center gap-1"
                        disabled={!caps.rotateToken || pending || newExpInvalid}
                        title={
                          newExpInvalid
                            ? "Срок действия обязателен и не должен превышать 6 месяцев"
                            : "Выпустить новый токен — старый будет автоматически отозван"
                        }
                        onClick={() =>
                          run(async () => {
                            const name = newName.trim() || active[0].name;
                            const res = await botsApi.rotateBotToken(botId, {
                              name,
                              expires_at: newExpIso(),
                            });
                            setIssued(res);
                            setNewName("");
                          })
                        }
                      >
                        <KeyRound className="w-4 h-4" /> Rotate
                      </button>
                      <button
                        className="btn btn-danger flex items-center gap-1"
                        disabled={!caps.revokeToken || pending}
                        title="Отозвать токен без замены"
                        onClick={() =>
                          run(() =>
                            botsApi.revokeBotToken(botId, active[0].token_id),
                          )
                        }
                      >
                        <Trash2 className="w-4 h-4" /> Revoke
                      </button>
                    </div>
                  </>
                )}

                {active.length > 1 && (
                  <>
                    <div className="alert-danger mb-2 flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 mt-[2px]" />
                      <div className="text-sm">
                        У бота {active.length} активных токенов. Должен быть только
                        один — отзовите лишние. Новые issue/rotate автоматически
                        закроют всё активное.
                      </div>
                    </div>
                    <table className="w-full text-sm">
                      <thead className="text-left text-dim text-xs uppercase">
                        <tr>
                          <th className="pb-2 pr-3">name</th>
                          <th className="pb-2 pr-3">created</th>
                          <th className="pb-2 pr-3">expires</th>
                          <th className="pb-2 pr-3">last used</th>
                          <th className="pb-2"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {active.map((t) => (
                          <tr key={t.token_id} className="border-t border-token">
                            <td className="py-2">{t.name}</td>
                            <td className="text-xs text-dim">
                              {t.created_at.slice(0, 10)}
                            </td>
                            <td className="text-xs text-dim">
                              {t.expires_at ? t.expires_at.slice(0, 10) : "—"}
                            </td>
                            <td className="text-xs text-dim">
                              {t.last_used_at ? t.last_used_at.slice(0, 10) : "—"}
                            </td>
                            <td>
                              <button
                                className="btn btn-sm btn-danger"
                                disabled={!caps.revokeToken || pending}
                                onClick={() =>
                                  run(() =>
                                    botsApi.revokeBotToken(botId, t.token_id),
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
                  </>
                )}
              </div>
            );
          })()}

          {/* Roles */}
          <div>
            <div className="text-xs uppercase text-dim mb-2">
              Service-роли · {(rolesQ.data ?? []).length}
            </div>
            {(rolesQ.data ?? []).length === 0 ? (
              <div className="empty-card">Роли боту не выданы.</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">service</th>
                    <th className="pb-2 pr-3">roles</th>
                    <th className="pb-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {(rolesQ.data ?? []).map((r) => (
                    <tr
                      key={r.service_name}
                      className="border-t border-token"
                    >
                      <td className="py-2 mono text-xs">{r.service_name}</td>
                      <td className="text-xs">{r.roles.join(", ")}</td>
                      <td>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={!caps.manageRoles || pending}
                          onClick={() =>
                            run(() =>
                              botsApi.revokeBotRoles(botId, r.service_name),
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
            <BotRoleAssignRow
              disabled={!caps.manageRoles || pending}
              onAssign={(service, list) =>
                run(() =>
                  botsApi.assignBotRoles(botId, {
                    service_name: service,
                    roles: list,
                  }),
                )
              }
            />
          </div>
        </div>
      )}
    </Section>
  );
}

function BotRoleAssignRow({
  disabled,
  onAssign,
}: {
  disabled: boolean;
  onAssign: (service: string, roles: string[]) => void;
}) {
  const [service, setService] = useState("");
  const [rolesCsv, setRolesCsv] = useState("");
  return (
    <div className="flex gap-2 mt-2 flex-wrap">
      <input
        className="input"
        placeholder="service_name"
        value={service}
        onChange={(e) => setService(e.target.value)}
      />
      <input
        className="input flex-1"
        placeholder="roles csv"
        value={rolesCsv}
        onChange={(e) => setRolesCsv(e.target.value)}
      />
      <button
        className="btn btn-primary flex items-center gap-1"
        disabled={disabled || !service.trim()}
        onClick={() => {
          const list = rolesCsv
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
          onAssign(service.trim(), list);
          setService("");
          setRolesCsv("");
        }}
      >
        <ShieldCheck className="w-4 h-4" /> Assign (replace)
      </button>
    </div>
  );
}

function Section({ icon, title, children, className = "" }: { icon: React.ReactNode; title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`surface border border-token rounded-lg p-4 flex flex-col min-h-0 ${className}`}>
      <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
        {icon} {title}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span>{v}</span>
    </div>
  );
}

/**
 * Bounds для UI-поля «Срок действия» bot-токена. Backend режет expires_at
 * < now или > now + 180 дней одним INVALID_EXPIRATION (см.
 * `core/constants.MAX_TOKEN_TTL_DAYS`). Default = +90 дней.
 */
function tokenExpiresBounds(): { min: string; max: string; default: string } {
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

function BotGrantedBy({ id }: { id: string }) {
  if (!id || id === "system" || id === "<simulated>") {
    return <span className="mono">{id || "—"}</span>;
  }
  const u = userById(id);
  if (!u) {
    return (
      <span className="mono text-dim" title="user removed or unknown">
        {id} (удалён)
      </span>
    );
  }
  return (
    <Link to={`/users/${u.id}`} className="mono hover-bg">
      {u.username}
    </Link>
  );
}
