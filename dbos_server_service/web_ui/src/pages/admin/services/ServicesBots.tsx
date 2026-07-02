import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  Edit3,
  RotateCw,
  Trash2,
  KeyRound,
  Copy,
  Power,
  ShieldCheck,
  AlertTriangle,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { BOTS } from "@/mocks/permissions";
import { DEPTS } from "@/mocks/auth";
import { InlineEditor, FormRow, StatRow, useInlineState } from "./_inline";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import * as botsApi from "@/api/auth/bots";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";
import { ApiError, apiErrMsg } from "@/api/client";
import type {
  Bot as BotResource,
  BotTokenCreateResponse,
} from "@/api/auth/types";
import {
  botMutationCaps,
  isDepAdmin,
  isMultiAdmin,
  isPlatformWideAdmin,
  isSecretAdmin,
} from "@/lib/rbac";
import { useDeptLabel, useUserLabel } from "@/lib/labels";
import { formatMskDate, formatMskShort, mskDateOffset } from "@/lib/datetime";
import { BotRolesPanel } from "@/components/bot/BotRolesPanel";

export function ServicesBots() {
  const mock = useMockMode();
  if (mock) return <ServicesBotsMock />;
  return <ServicesBotsLive />;
}

// ---------------------------------------------------------------------------
// Live mode — talks to auth_service. Pagination handled by limit/offset; we
// fetch a fat page (200) by default because the list pane is short anyway.
// ---------------------------------------------------------------------------

function ServicesBotsLive() {
  const { persona } = usePersona();
  // «Can create a new bot» is a section-level gate — account_admin always,
  // dep_admin within own dept, or anyone holding a service-level admin
  // grant on secret/server/worker. Per-bot edit is computed below from
  // botMutationCaps so cross-dept bots stay read-only for non-platform admins.
  const canCreate =
    isPlatformWideAdmin(persona) ||
    isDepAdmin(persona) ||
    isMultiAdmin(persona) ||
    isSecretAdmin(persona);

  const [refreshTick, setRefreshTick] = useState(0);
  const list = useQuery(
    () => botsApi.listBotsWithTotal({ limit: 200 }),
    [refreshTick],
  );

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? items.length;

  if (list.loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="spinner">Загрузка ботов…</div>
      </div>
    );
  }
  if (list.error) {
    const msg =
      list.error instanceof ApiError
        ? `${list.error.errorCode}: ${list.error.message}`
        : list.error.message;
    return (
      <div className="flex-1 p-8">
        <div className="alert-danger">
          {msg}
          <button
            className="btn btn-sm ml-2"
            onClick={() => setRefreshTick((t) => t + 1)}
          >
            Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <InlineEditor
      title="Боты · auth_service"
      icon={Bot}
      hint={`live · ${total} ботов`}
      items={items}
      getId={(b) => b.id}
      canEdit={canCreate}
      readonlyNote={!canCreate ? "Просмотр без права изменения" : undefined}
      listHeader={
        <TruncationNotice shown={items.length} total={total} />
      }
      renderRow={({ item, active, onSelect }) => (
        <button
          className={`cred-row text-left ${active ? "active" : ""}`}
          onClick={onSelect}
        >
          <div className="flex items-center gap-2">
            <Bot className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">
                <RowDeptLabel deptId={item.department_id} />
              </div>
            </div>
            <span
              className={`badge badge-${item.status === "active" ? "ok" : "warn"}`}
            >
              {item.status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(b, { editing, onClose }) => {
        const caps = botMutationCaps(persona, b.department_id ?? null);
        const canEditBot = caps.rotateToken || caps.revokeToken || caps.manageRoles;
        if (editing) {
          return (
            <BotEditForm
              initial={b}
              onDone={() => {
                onClose();
                setRefreshTick((t) => t + 1);
              }}
            />
          );
        }
        return (
          <BotLiveView
            bot={b}
            canEdit={canEditBot}
            onChange={() => setRefreshTick((t) => t + 1)}
          />
        );
      }}
      renderCreate={
        canCreate
          ? (onClose) => (
              <BotCreateForm
                onDone={() => {
                  onClose();
                  setRefreshTick((t) => t + 1);
                }}
              />
            )
          : undefined
      }
    />
  );
}

function BotLiveView({
  bot,
  canEdit,
  onChange,
}: {
  bot: BotResource;
  canEdit: boolean;
  onChange: () => void;
}) {
  const { persona } = usePersona();
  const { startEdit } = useInlineState();
  const caps = botMutationCaps(persona, bot.department_id ?? null);
  const createdByLabel = useUserLabel(bot.created_by);

  // tokens / roles — sub-resources, fetched per selected bot.
  const tokensQ = useQuery(() => botsApi.listBotTokens(bot.id), [bot.id]);
  const rolesQ = useQuery(() => botsApi.listBotRoles(bot.id), [bot.id]);

  const tokens = tokensQ.data ?? [];
  const activeTokens = useMemo(
    () => tokens.filter((t) => !t.revoked_at),
    [tokens],
  );
  const roles = rolesQ.data ?? [];

  // One-time displayed token (issue / rotate) — never persists across navigation.
  const [issued, setIssued] = useState<BotTokenCreateResponse | null>(null);
  const [tokenName, setTokenName] = useState("");
  const tokenExpBounds = useMemo(() => tokenExpiresBounds(), []);
  const [tokenExpires, setTokenExpires] = useState(tokenExpBounds.default);
  const tokenExpInvalid =
    !tokenExpires ||
    tokenExpires < tokenExpBounds.min ||
    tokenExpires > tokenExpBounds.max;
  const tokenExpIso = () =>
    new Date(`${tokenExpires}T23:59:59Z`).toISOString();
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const refetchAll = useCallback(() => {
    tokensQ.refetch();
    rolesQ.refetch();
    onChange();
  }, [tokensQ, rolesQ, onChange]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setErr(null);
      setInfo(null);
      setPending(true);
      try {
        await fn();
        refetchAll();
      } catch (e) {
        setErr(apiErrMsg(e));
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
      // ignore — clipboard may be unavailable in dev
    }
  };

  const liveDeptLabel = useDeptLabel(bot.department_id ?? null);
  const deptName = liveDeptLabel || null;

  // Activity timestamps surfaced from the token sub-resource: backend does not
  // expose them on the bot itself, but the latest issue/revoke timestamps
  // here are functionally the same thing.
  const lastTokenCreated = useMemo(() => {
    if (tokens.length === 0) return null;
    return tokens
      .map((t) => t.created_at)
      .sort()
      .slice(-1)[0];
  }, [tokens]);
  const lastTokenUsed = useMemo(() => {
    const used = tokens
      .map((t) => t.last_used_at)
      .filter((s): s is string => !!s)
      .sort();
    return used.length ? used[used.length - 1] : null;
  }, [tokens]);

  return (
    <div className="flex flex-col gap-4 w-full">
      {/* ============== Header / status / quick actions ============== */}
      <div className="card">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-semibold flex items-center gap-2 mono">
            <Bot className="w-4 h-4 text-accent" /> {bot.name}
            <span
              className={`badge badge-${bot.status === "active" ? "ok" : "warn"}`}
            >
              {bot.status}
            </span>
            {!canEdit && (
              <span className="badge badge-warn" title={caps.reason}>
                read-only
              </span>
            )}
          </h3>
          {canEdit && (
            <div className="flex items-center gap-2 flex-wrap">
              <button
                className="btn flex items-center gap-1"
                onClick={() => startEdit(bot.id)}
              >
                <Edit3 className="w-4 h-4" /> Edit
              </button>
              <button
                className="btn flex items-center gap-1"
                disabled={pending}
                onClick={() =>
                  run(() =>
                    bot.status === "active"
                      ? botsApi.disableBot(bot.id)
                      : botsApi.enableBot(bot.id),
                  )
                }
              >
                <Power className="w-4 h-4" />
                {bot.status === "active" ? "Disable" : "Enable"}
              </button>
            </div>
          )}
        </div>

        {err && <div className="alert-danger mb-2">{err}</div>}
        {info && (
          <div className="alert-block mb-2">
            <div className="flex items-start gap-2">
              <span className="text-sm flex-1">{info}</span>
              <button className="btn btn-sm" onClick={() => setInfo(null)}>
                закрыть
              </button>
            </div>
          </div>
        )}

        {issued && (
          <div className="alert-block mb-2">
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
              Hash хранится в БД, чистый токен — только сейчас. Скопируйте до закрытия.
            </div>
          </div>
        )}

        {/* ----- Profile ----- */}
        <div className="text-xs uppercase text-dim mb-2">Профиль</div>
        <StatRow k="bot_id" v={<span className="mono">{bot.id}</span>} />
        <StatRow k="name" v={<span className="mono">{bot.name}</span>} />
        <StatRow k="description" v={bot.description ?? "—"} />
        <StatRow
          k="department"
          v={<BotDeptLabel mockDeptName={deptName} deptId={bot.department_id} />}
        />
        <StatRow
          k="status"
          v={
            <span
              className={`badge badge-${bot.status === "active" ? "ok" : "warn"}`}
            >
              {bot.status}
            </span>
          }
        />
        <StatRow
          k="allowed_services"
          v={
            bot.allowed_services.length === 0 ? (
              <span className="text-dim italic">—</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {bot.allowed_services.map((s) => (
                  <span key={s} className="badge mono">
                    {s}
                  </span>
                ))}
              </div>
            )
          }
        />
        <StatRow
          k="created_at"
          v={<span className="mono">{formatMskShort(bot.created_at)}</span>}
        />
        {bot.updated_at && (
          <StatRow
            k="updated_at"
            v={<span className="mono">{formatMskShort(bot.updated_at)}</span>}
          />
        )}
        <StatRow
          k="created_by"
          v={
            bot.created_by ? (
              <span title={bot.created_by}>{createdByLabel}</span>
            ) : (
              <span className="text-dim italic">system</span>
            )
          }
        />
        {lastTokenCreated && (
          <StatRow
            k="last_token_created"
            v={
              <span className="mono">
                {formatMskShort(lastTokenCreated)}
              </span>
            }
          />
        )}
        {lastTokenUsed && (
          <StatRow
            k="last_token_used"
            v={
              <span className="mono">
                {formatMskShort(lastTokenUsed)}
              </span>
            }
          />
        )}
      </div>

      {/* ============== Token ============== */}
      {/*
        Инвариант: у бота 0 или 1 active token. UI отражает три состояния:
        - 0 active → форма Issue token (singular)
        - 1 active → metadata + Rotate / Revoke
        - >1 active (legacy data) → fallback таблица + warning
      */}
      <div className="card">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <div className="text-xs uppercase text-dim flex items-center gap-2">
            <KeyRound className="w-3 h-3" /> Token
            {activeTokens.length > 0 && (
              <span className="text-dim">· {activeTokens.length} active</span>
            )}
          </div>
          {tokensQ.loading && <span className="text-xs text-dim">…</span>}
        </div>

        {tokensQ.error && (
          <div className="alert-danger mb-2">
            {tokensQ.error instanceof ApiError
              ? `${tokensQ.error.errorCode}: ${tokensQ.error.message}`
              : tokensQ.error.message}
          </div>
        )}

        {!tokensQ.loading && activeTokens.length === 0 && (
          <>
            <div className="empty-card text-sm">Активного токена нет.</div>
            {canEdit && (
              <div className="mt-3 flex gap-2 flex-wrap items-center">
                <input
                  className="input flex-1"
                  placeholder="token name"
                  value={tokenName}
                  onChange={(e) => setTokenName(e.target.value)}
                />
                <input
                  className="input"
                  type="date"
                  title="Срок действия (до) — max 6 месяцев"
                  value={tokenExpires}
                  min={tokenExpBounds.min}
                  max={tokenExpBounds.max}
                  onChange={(e) => setTokenExpires(e.target.value)}
                />
                <button
                  className="btn btn-primary flex items-center gap-1"
                  disabled={pending || !tokenName.trim() || tokenExpInvalid}
                  title={
                    tokenExpInvalid
                      ? "Срок действия обязателен и не должен превышать 6 месяцев"
                      : undefined
                  }
                  onClick={() =>
                    run(async () => {
                      const res = await botsApi.issueBotToken(bot.id, {
                        name: tokenName.trim(),
                        expires_at: tokenExpIso(),
                      });
                      setIssued(res);
                      setTokenName("");
                    })
                  }
                >
                  <KeyRound className="w-4 h-4" /> Issue token
                </button>
              </div>
            )}
          </>
        )}

        {!tokensQ.loading && activeTokens.length === 1 && (
          <>
            <StatRow
              k="name"
              v={<span className="mono">{activeTokens[0].name}</span>}
            />
            <StatRow
              k="token_id"
              v={<span className="mono text-xs">{activeTokens[0].token_id}</span>}
            />
            <StatRow
              k="created_at"
              v={
                <span className="mono">
                  {formatMskShort(activeTokens[0].created_at)}
                </span>
              }
            />
            <StatRow
              k="expires_at"
              v={
                <span className="mono">
                  {formatMskShort(activeTokens[0].expires_at)}
                </span>
              }
            />
            <StatRow
              k="last_used_at"
              v={
                <span className="mono">
                  {formatMskShort(activeTokens[0].last_used_at)}
                </span>
              }
            />
            {canEdit && (
              <div className="mt-3 flex gap-2 flex-wrap items-center">
                <input
                  className="input flex-1"
                  placeholder="новое имя (опционально)"
                  value={tokenName}
                  onChange={(e) => setTokenName(e.target.value)}
                />
                <input
                  className="input"
                  type="date"
                  title="Срок действия нового токена (до) — max 6 месяцев"
                  value={tokenExpires}
                  min={tokenExpBounds.min}
                  max={tokenExpBounds.max}
                  onChange={(e) => setTokenExpires(e.target.value)}
                />
                <button
                  className="btn flex items-center gap-1"
                  disabled={pending || tokenExpInvalid}
                  title={
                    tokenExpInvalid
                      ? "Срок действия обязателен и не должен превышать 6 месяцев"
                      : "Выпустить новый токен — старый будет автоматически отозван"
                  }
                  onClick={() =>
                    run(async () => {
                      const name = tokenName.trim() || activeTokens[0].name;
                      const res = await botsApi.rotateBotToken(bot.id, {
                        name,
                        expires_at: tokenExpIso(),
                      });
                      setIssued(res);
                      setTokenName("");
                    })
                  }
                >
                  <RotateCw className="w-4 h-4" /> Rotate
                </button>
                <button
                  className="btn btn-danger flex items-center gap-1"
                  disabled={!caps.revokeToken || pending}
                  title={caps.revokeToken ? "Отозвать токен без замены" : caps.reason}
                  onClick={() =>
                    run(() =>
                      botsApi.revokeBotToken(
                        bot.id,
                        activeTokens[0].token_id,
                      ),
                    )
                  }
                >
                  <Trash2 className="w-4 h-4" /> Revoke
                </button>
              </div>
            )}
          </>
        )}

        {!tokensQ.loading && activeTokens.length > 1 && (
          <>
            <div className="alert-danger mb-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-[2px]" />
              <div className="text-sm">
                У бота {activeTokens.length} активных токенов. Должен быть только
                один — отзовите лишние. Новые issue/rotate автоматически закроют
                всё активное.
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
                {activeTokens.map((t) => (
                  <tr key={t.token_id} className="border-t border-token">
                    <td className="py-2">{t.name}</td>
                    <td className="text-xs text-dim mono">
                      {formatMskDate(t.created_at)}
                    </td>
                    <td className="text-xs text-dim mono">
                      {formatMskDate(t.expires_at)}
                    </td>
                    <td className="text-xs text-dim mono">
                      {formatMskDate(t.last_used_at)}
                    </td>
                    <td className="text-right">
                      <button
                        className="btn btn-sm btn-danger"
                        disabled={!caps.revokeToken || pending}
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
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      {/* ============== Service-роли ============== */}
      <div className="card">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <div className="text-xs uppercase text-dim flex items-center gap-2">
            <ShieldCheck className="w-3 h-3" /> Service-роли · {roles.length}
          </div>
          {rolesQ.loading && <span className="text-xs text-dim">…</span>}
        </div>

        <BotRolesPanel
          roles={roles}
          allowedServices={bot.allowed_services}
          departmentId={bot.department_id ?? null}
          loading={rolesQ.loading}
          error={rolesQ.error}
          canEdit={canEdit}
          canManage={caps.manageRoles}
          reason={caps.reason}
          pending={pending}
          onRevoke={(service) =>
            run(() => botsApi.revokeBotRoles(bot.id, service))
          }
          onAssign={(service, list) =>
            run(() =>
              botsApi.assignBotRoles(bot.id, {
                service_name: service,
                roles: list,
              }),
            )
          }
        />
      </div>

      {/* ============== Danger zone ============== */}
      {canEdit && (
        <div className="card" style={{ borderColor: "rgba(244,135,113,0.3)" }}>
          <div className="text-xs uppercase text-danger mb-2 flex items-center gap-2">
            <AlertTriangle className="w-3 h-3" /> Danger zone
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="text-sm">
                <div className="font-medium">
                  {bot.status === "active" ? "Disable bot" : "Enable bot"}
                </div>
                <div className="text-xs text-dim">
                  Disable выключает все аутентификации этого бота, не трогая токены и роли.
                </div>
              </div>
              <button
                className="btn flex items-center gap-1"
                disabled={pending}
                onClick={() =>
                  run(() =>
                    bot.status === "active"
                      ? botsApi.disableBot(bot.id)
                      : botsApi.enableBot(bot.id),
                  )
                }
              >
                <Power className="w-4 h-4" />
                {bot.status === "active" ? "Disable" : "Enable"}
              </button>
            </div>
            <div className="flex items-center justify-between gap-3 flex-wrap border-t border-token pt-2">
              <div className="text-sm">
                <div className="font-medium">Delete bot</div>
                <div className="text-xs text-dim">
                  auth_service не предоставляет DELETE /bots/{"{id}"} —
                  используйте Disable + revoke токенов / ролей.
                </div>
              </div>
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
        </div>
      )}
    </div>
  );
}

function BotCreateForm({ onDone }: { onDone: () => void }) {
  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);
  const depts = deptsQ.data ?? [];
  const [name, setName] = useState("");
  const [dept, setDept] = useState("");
  useEffect(() => {
    if (!dept && depts.length > 0) setDept(depts[0].id);
  }, [dept, depts]);
  const [services, setServices] = useState("");
  const [description, setDescription] = useState("");
  const tokenExpBounds = useMemo(() => tokenExpiresBounds(), []);
  const [tokenExpires, setTokenExpires] = useState(tokenExpBounds.default);
  const [issued, setIssued] = useState<BotTokenCreateResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const tokenExpInvalid =
    !tokenExpires ||
    tokenExpires < tokenExpBounds.min ||
    tokenExpires > tokenExpBounds.max;

  const submit = async () => {
    setErr(null);
    setPending(true);
    try {
      const allowed = services
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const created = await botsApi.createBot({
        name: name.trim(),
        department_id: dept,
        allowed_services: allowed,
        description: description.trim() || undefined,
      });
      // Immediately issue an initial token so the operator has something to
      // hand off. The bot is only useful once it has a token.
      const tok = await botsApi.issueBotToken(created.id, {
        name: "initial",
        expires_at: new Date(`${tokenExpires}T23:59:59Z`).toISOString(),
      });
      setIssued(tok);
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  const copy = (token: string) => {
    try {
      void navigator.clipboard.writeText(token);
    } catch {
      // ignore
    }
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Bot className="w-4 h-4 text-accent" /> Новый бот
      </h3>
      {err && <div className="alert-danger mb-2">{err}</div>}
      {issued ? (
        <div className="flex flex-col gap-3">
          <div className="alert-block">
            <div className="text-xs uppercase mb-1">
              Bot создан. Токен показывается один раз:
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
            </div>
          </div>
          <button className="btn btn-primary" onClick={onDone}>
            Готово
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <FormRow label="name">
            <input
              className="input mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </FormRow>
          <FormRow label="department_id">
            <select
              className="input"
              value={dept}
              onChange={(e) => setDept(e.target.value)}
              disabled={deptsQ.loading || depts.length === 0}
            >
              {deptsQ.loading && <option>загрузка…</option>}
              {!deptsQ.loading && depts.length === 0 && (
                <option value="">нет отделов</option>
              )}
              {depts.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </FormRow>
          <FormRow
            label="allowed_services"
            hint="csv: config_service,secret_service"
          >
            <input
              className="input mono"
              value={services}
              onChange={(e) => setServices(e.target.value)}
            />
          </FormRow>
          <FormRow label="description">
            <textarea
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </FormRow>
          <FormRow
            label="initial token: срок действия"
            hint="max 6 месяцев"
          >
            <input
              className="input"
              type="date"
              value={tokenExpires}
              min={tokenExpBounds.min}
              max={tokenExpBounds.max}
              onChange={(e) => setTokenExpires(e.target.value)}
            />
          </FormRow>
          <div className="mt-3 alert-block text-xs">
            После создания будет автоматически выпущен initial-токен — он показывается один раз.
          </div>
          <div className="mt-4 flex gap-2 justify-end">
            <button className="btn" onClick={onDone}>
              Отмена
            </button>
            <button
              className="btn btn-primary"
              disabled={pending || !name.trim() || !dept || tokenExpInvalid}
              title={
                tokenExpInvalid
                  ? "Срок действия обязателен и не должен превышать 6 месяцев"
                  : undefined
              }
              onClick={submit}
            >
              Создать и показать токен
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function BotEditForm({
  initial,
  onDone,
}: {
  initial: BotResource;
  onDone: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description ?? "");
  const [services, setServices] = useState(
    initial.allowed_services.join(","),
  );
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    setErr(null);
    setPending(true);
    try {
      await botsApi.patchBot(initial.id, {
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        allowed_services: services
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      onDone();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Bot className="w-4 h-4 text-accent" /> Edit · {initial.name}
      </h3>
      {err && <div className="alert-danger mb-2">{err}</div>}
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input
            className="input mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormRow>
        <FormRow label="description">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
        <FormRow label="allowed_services" hint="csv">
          <input
            className="input mono"
            value={services}
            onChange={(e) => setServices(e.target.value)}
          />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          disabled={pending}
          onClick={submit}
        >
          Сохранить
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mock mode — legacy view kept verbatim for offline dev. Untouched except
// inlined to keep the file self-contained.
// ---------------------------------------------------------------------------

function ServicesBotsMock() {
  const { persona } = usePersona();
  const canCreate =
    isPlatformWideAdmin(persona) ||
    isDepAdmin(persona) ||
    isMultiAdmin(persona) ||
    isSecretAdmin(persona);

  // account_admin видит все ботов; dep-scoped роли — только свой dept.
  const visible = isPlatformWideAdmin(persona)
    ? BOTS
    : persona.dept_id
      ? BOTS.filter((b) => b.owner_dept === persona.dept_id)
      : BOTS;

  return (
    <InlineEditor
      title="Боты · auth_service (mock)"
      icon={Bot}
      hint="Bot token TTL 6 мес · rotate / revoke / переоткрытие initial spec"
      items={visible}
      getId={(b) => b.id}
      canEdit={canCreate}
      readonlyNote={!canCreate ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button
          className={`cred-row text-left ${active ? "active" : ""}`}
          onClick={onSelect}
        >
          <div className="flex items-center gap-2">
            <Bot className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">
                {item.owner_dept} · last {formatMskDate(item.last_used)}
              </div>
            </div>
            <span
              className={`badge badge-${item.token_status === "active" ? "ok" : item.token_status === "rotated" ? "warn" : "danger"}`}
            >
              {item.token_status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(b, { editing, onClose }) => {
        const caps = botMutationCaps(persona, b.owner_dept ?? null);
        const canEditBot = caps.rotateToken || caps.revokeToken || caps.manageRoles;
        if (editing) return <MockBotForm initial={b} onDone={onClose} mode="edit" />;
        return <MockBotView bot={b} canEdit={canEditBot} />;
      }}
      renderCreate={
        canCreate ? (onClose) => <MockBotForm onDone={onClose} mode="new" /> : undefined
      }
    />
  );
}

function MockBotView({
  bot,
  canEdit,
}: {
  bot: (typeof BOTS)[number];
  canEdit: boolean;
}) {
  const { startEdit } = useInlineState();
  const toast = useToast();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Bot className="w-4 h-4 text-accent" /> {bot.name}
          <span
            className={`badge badge-${bot.token_status === "active" ? "ok" : bot.token_status === "rotated" ? "warn" : "danger"}`}
          >
            {bot.token_status}
          </span>
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button
              className="btn flex items-center gap-1"
              onClick={() => startEdit(bot.id)}
            >
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button
              className="btn flex items-center gap-1"
              onClick={() => toast.info("mock: rotate token")}
            >
              <RotateCw className="w-4 h-4" /> Rotate token
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={() => toast.info("mock: revoke")}
            >
              <Trash2 className="w-4 h-4" /> Revoke
            </button>
          </div>
        )}
      </div>
      <StatRow k="bot_id" v={<span className="mono">{bot.id}</span>} />
      <StatRow k="owner_dept" v={bot.owner_dept} />
      <StatRow k="created_by" v={<span className="mono">{bot.created_by}</span>} />
      <StatRow k="created_at" v={<span className="mono">{formatMskDate(bot.created_at)}</span>} />
      <StatRow k="last_used" v={<span className="mono">{formatMskShort(bot.last_used)}</span>} />
      <StatRow
        k="initial_spec"
        v={
          <div className="flex flex-wrap gap-1">
            {bot.initial_spec.map((p) => (
              <span key={p} className="badge mono">
                {p}
              </span>
            ))}
          </div>
        }
      />
    </div>
  );
}

function MockBotForm({
  initial,
  onDone,
  mode,
}: {
  initial?: (typeof BOTS)[number];
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [dept, setDept] = useState(initial?.owner_dept ?? DEPTS[0]?.id ?? "");
  const [spec, setSpec] = useState(initial?.initial_spec.join("\n") ?? "");
  const toast = useToast();
  const submit = () => {
    toast.info(mode === "new" ? "mock: создать бота" : "mock: сохранить");
    onDone();
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Bot className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый бот" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input
            className="input mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormRow>
        <FormRow label="owner_dept">
          <select
            className="input"
            value={dept}
            onChange={(e) => setDept(e.target.value)}
          >
            {DEPTS.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="initial_spec"
          hint="одна permission на строку, например read:server:dept"
        >
          <textarea
            className="input"
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
          />
        </FormRow>
      </div>
      <div className="mt-3 alert-block text-xs">
        После создания токен показывается один раз — скопируйте его до закрытия формы.
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>
          Отмена
        </button>
        <button className="btn btn-primary" onClick={submit}>
          {mode === "new" ? "Создать и показать токен" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

/**
 * Bounds для UI-поля «Срок действия» bot-токена. Backend режет expires_at
 * < now или > now + 180 дней одним INVALID_EXPIRATION (см.
 * `core/constants.MAX_TOKEN_TTL_DAYS`). Default = +90 дней.
 */
function tokenExpiresBounds(): { min: string; max: string; default: string } {
  return {
    min: mskDateOffset(1),
    max: mskDateOffset(180),
    default: mskDateOffset(90),
  };
}

function RowDeptLabel({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function BotDeptLabel({
  mockDeptName,
  deptId,
}: {
  mockDeptName: string | null;
  deptId: string | null | undefined;
}) {
  const apiLabel = useDeptLabel(deptId);
  if (mockDeptName) return <span>{mockDeptName}</span>;
  return <span>{apiLabel}</span>;
}

