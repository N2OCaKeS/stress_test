/**
 * Live-страница /secret — Shell + Aside (список кред) + Workzone (detail).
 *
 * Тянет `secret_service` напрямую через `@/api/secret/*`. Покрывает
 * минимальный рабочий цикл: list (cursor) + create + reveal + delete +
 * recover, плюс read-only просмотр ACL / dept-grants на detail-панели.
 *
 * Mock-режим (`VITE_USE_MOCK_AUTH=true`) обслуживают SecretAccountAdmin /
 * SecretDepAdmin — диспетчеризация в Secret.tsx.
 */
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  Key,
  Plus,
  ArrowLeft,
  AlertCircle,
  Eye,
  Copy,
  Trash2,
  RotateCcw,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import {
  createCredential,
  deleteCredential,
  getCredential,
  listCredentials,
  recoverCredential,
  revealCredential,
} from "@/api/secret/credentials";
import { listRoleAcls } from "@/api/secret/roleAcls";
import { listDeptGrants } from "@/api/secret/deptGrants";
import type {
  Credential,
  CredentialCreateRequest,
  CredentialScope,
} from "@/api/secret/types";

const SCOPE_LABEL: Record<CredentialScope, string> = {
  personal: "personal",
  department: "department",
  cross_department: "cross-dept",
};

const STATUS_KIND: Record<string, "ok" | "danger"> = {
  active: "ok",
  blocked: "danger",
};

function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) return `${e.errorCode}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return fallback;
}

export function SecretLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");
  const action = params.get("action"); // "new" | null

  const [search, setSearch] = useState("");
  const [filterScope, setFilterScope] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");

  const listQ = useQuery(
    () =>
      listCredentials({
        limit: 200,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
      }),
    [filterScope, filterStatus],
  );

  const canManage =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "dep_admin" ||
    persona.service_roles.secret === "admin" ||
    persona.service_roles.secret === "operator";

  // Список может прийти как полный (CredentialList) или guest-урезанный
  // (CredentialGuestList). Поля name/id/service/scope есть в обоих shape'ах —
  // их и показываем в aside.
  const rawItems = listQ.data?.items ?? [];
  const items = rawItems as Array<Credential & { status?: string }>;

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return items;
    return items.filter(
      (c) =>
        c.name.toLowerCase().includes(term) ||
        c.service.toLowerCase().includes(term) ||
        c.id.toLowerCase().includes(term),
    );
  }, [items, search]);

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function startCreate() {
    const next = new URLSearchParams(params);
    next.set("action", "new");
    next.delete("id");
    setParams(next, { replace: true });
  }
  function closeAction() {
    const next = new URLSearchParams(params);
    next.delete("action");
    setParams(next, { replace: true });
  }

  async function handleCreate(body: CredentialCreateRequest) {
    try {
      const created = await createCredential(body);
      toast.success(`Credential ${created.name} создан`);
      listQ.refetch();
      const next = new URLSearchParams(params);
      next.set("id", created.id);
      next.delete("action");
      setParams(next, { replace: true });
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание не удалось"));
    }
  }

  async function handleDelete(cred: Credential) {
    if (typeof window === "undefined") return;
    const reason = window.prompt(
      `Причина удаления "${cred.name}" (обязательна для admin-override):`,
      "",
    );
    if (reason === null) return;
    const confirmed = window.confirm(
      `Удалить credential ${cred.name}? Операция необратима.`,
    );
    if (!confirmed) return;
    try {
      await deleteCredential(cred.id, {
        reason: reason.trim() || undefined,
      });
      toast.success(`Credential ${cred.name} удалён`);
      selectId(null);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${items.length} credentials…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-dim">
          <select
            className="surface-2 border border-token rounded px-1 py-0.5"
            value={filterScope}
            onChange={(e) => setFilterScope(e.target.value)}
            title="Фильтр по scope"
          >
            <option value="">все scope</option>
            <option value="personal">personal</option>
            <option value="department">department</option>
            <option value="cross_department">cross_department</option>
          </select>
          <select
            className="surface-2 border border-token rounded px-1 py-0.5"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            title="Фильтр по статусу"
          >
            <option value="">все статусы</option>
            <option value="active">active</option>
            <option value="blocked">blocked</option>
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {listQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {listQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(listQ.error, "Список не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => listQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!listQ.loading && !listQ.error && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Список пуст.
          </div>
        )}
        <div className="px-2 flex flex-col gap-0.5">
          {filtered.map((c) => (
            <CredRow
              key={c.id}
              cred={c}
              active={selectedId === c.id}
              onSelect={() => selectId(c.id)}
            />
          ))}
        </div>
      </div>

      {canManage && (
        <div className="border-t border-token p-3 shrink-0">
          <button
            className="btn btn-primary w-full flex items-center justify-center gap-2"
            onClick={startCreate}
          >
            <Plus className="w-4 h-4" /> Создать credential
          </button>
        </div>
      )}
    </aside>
  );

  return (
    <Shell breadcrumb="secret_service / credentials" middle={aside}>
      {action === "new" && canManage ? (
        <CreatePane
          defaultDeptId={persona.dept_id}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedId ? (
        <DetailPane
          credId={selectedId}
          canManage={canManage}
          onDelete={handleDelete}
          onChanged={() => listQ.refetch()}
        />
      ) : (
        <EmptyPane canCreate={canManage} onCreate={startCreate} />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function CredRow({
  cred,
  active,
  onSelect,
}: {
  cred: Credential & { status?: string };
  active: boolean;
  onSelect: () => void;
}) {
  const statusKind = cred.status ? STATUS_KIND[cred.status] : undefined;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        <Key className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">{cred.name}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="mono truncate">{cred.service}</span>
            <span>·</span>
            <span>{SCOPE_LABEL[cred.scope]}</span>
          </div>
        </div>
        {cred.status && (
          <span className={`badge${statusKind ? ` badge-${statusKind}` : ""}`}>
            {cred.status}
          </span>
        )}
      </div>
    </button>
  );
}

function EmptyPane({
  canCreate,
  onCreate,
}: {
  canCreate: boolean;
  onCreate: () => void;
}) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <Key className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim mb-3">
          Выберите credential слева для просмотра деталей.
        </div>
        {canCreate && (
          <button
            className="btn btn-primary inline-flex items-center gap-1"
            onClick={onCreate}
          >
            <Plus className="w-4 h-4" /> Создать credential
          </button>
        )}
      </div>
    </section>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function DetailPane({
  credId,
  canManage,
  onDelete,
  onChanged,
}: {
  credId: string;
  canManage: boolean;
  onDelete: (c: Credential) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const credQ = useQuery(() => getCredential(credId), [credId]);
  const cred = credQ.data;

  const isCross = cred?.scope === "cross_department";
  const aclQ = useQuery(() => listRoleAcls(credId), [credId]);
  const grantsQ = useQuery(() => listDeptGrants(credId), [credId], {
    enabled: isCross,
  });

  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);

  async function handleReveal() {
    if (revealing) return;
    setRevealing(true);
    try {
      const res = await revealCredential(credId);
      // secret_b64 — base64(plaintext); декодируем для показа.
      let plain = res.secret_b64;
      try {
        plain = atob(res.secret_b64);
      } catch {
        // если не валидный base64 — показываем как есть
      }
      setRevealed(plain);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 429
          ? `Throttled: повторите через ${e.retryAfter ?? "несколько"} сек`
          : apiErrMsg(e, "Reveal не удался");
      toast.error(msg);
    } finally {
      setRevealing(false);
    }
  }

  async function handleRecover() {
    try {
      await recoverCredential(credId);
      toast.success("Credential разблокирован");
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Recover не удался"));
    }
  }

  async function handleCopy() {
    if (!revealed || typeof navigator === "undefined" || !navigator.clipboard)
      return;
    try {
      await navigator.clipboard.writeText(revealed);
      toast.success("Скопировано");
    } catch {
      toast.warn("Не удалось скопировать");
    }
  }

  if (credQ.loading) {
    return (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="text-sm text-dim">Загрузка…</div>
      </section>
    );
  }
  if (credQ.error || !cred) {
    return (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="alert alert-danger max-w-md flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(credQ.error, "Карточка не загрузилась")}</div>
            <button
              className="btn btn-ghost mt-2"
              onClick={() => credQ.refetch()}
            >
              Повторить
            </button>
          </div>
        </div>
      </section>
    );
  }

  const blocked = cred.status === "blocked";

  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center text-2xl">
          🔑
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{cred.name}</h1>
            <span className={`badge badge-${blocked ? "danger" : "ok"}`}>
              {cred.status}
            </span>
            <span className="text-xs text-dim">
              scope: <b>{SCOPE_LABEL[cred.scope]}</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{cred.id}</span>
            <span>·</span>
            <span>
              service: <b>{cred.service}</b>
            </span>
            {cred.login && (
              <>
                <span>·</span>
                <span>login: {cred.login}</span>
              </>
            )}
          </div>
        </div>
        {canManage && (
          <div className="flex items-center gap-2 shrink-0">
            {blocked && (
              <button className="btn" onClick={handleRecover} title="Recover">
                <RotateCcw className="w-4 h-4 inline-block" /> Recover
              </button>
            )}
            <button
              className="btn btn-danger"
              onClick={() => onDelete(cred)}
              title="Удалить"
            >
              <Trash2 className="w-4 h-4 inline-block" /> Revoke
            </button>
          </div>
        )}
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        {/* Secret value */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wider text-dim">
              Значение
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-warn">reveal: 5-min throttle</span>
              <button
                className="btn"
                onClick={handleReveal}
                disabled={revealing || blocked}
              >
                <Eye className="w-4 h-4 inline-block" />{" "}
                <span>{revealing ? "…" : "Reveal"}</span>
              </button>
              <button
                className="btn"
                onClick={handleCopy}
                disabled={!revealed}
              >
                <Copy className="w-4 h-4 inline-block" />
              </button>
            </div>
          </div>
          <div
            className={`mono text-lg p-3 surface-2 rounded border border-token ${
              revealed === null ? "secret-mask" : ""
            }`}
          >
            {revealed ?? "••••••••••••••••••••••••••"}
          </div>
          <div className="text-xs text-dim mt-2">
            Reveal эмитит CRITICAL audit-событие и гейтится 5-минутным throttle.
          </div>
        </div>

        {/* Meta */}
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            Метаданные
          </div>
          <div className="text-sm">
            <MetaRow label="Owner dept" value={cred.owner_dept_id ?? "—"} />
            <MetaRow label="Owner user" value={cred.owner_user_id ?? "—"} />
            <MetaRow label="Created by" value={cred.created_by} />
            <MetaRow label="Created" value={cred.created_at} />
            <MetaRow label="Updated" value={cred.updated_at} />
            <MetaRow label="visible_to_dept" value={String(cred.visible_to_dept)} />
            <MetaRow label="valid_from" value={cred.valid_from ?? "—"} />
            <MetaRow label="valid_to" value={cred.valid_to ?? "—"} />
            {blocked && (
              <>
                <MetaRow label="blocked_at" value={cred.blocked_at ?? "—"} />
                <MetaRow
                  label="blocked_reason"
                  value={cred.blocked_reason ?? "—"}
                />
              </>
            )}
          </div>
        </div>

        {/* Access — RoleACL */}
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            RoleACL ({aclQ.data?.items.length ?? 0})
          </div>
          {aclQ.loading && <div className="text-xs text-dim">Загрузка…</div>}
          {aclQ.error && (
            <div className="text-xs text-danger">
              {apiErrMsg(aclQ.error, "ACL не загрузился")}
            </div>
          )}
          {!aclQ.loading && !aclQ.error && (
            <div className="text-sm flex flex-col gap-1">
              {(aclQ.data?.items ?? []).length === 0 && (
                <div className="text-xs text-dim">Нет выданных ACL.</div>
              )}
              {(aclQ.data?.items ?? []).map((a) => (
                <div key={a.id} className="stat-row">
                  <span className="text-dim">
                    {a.dept_id} / {a.role_name}
                  </span>
                  <span className="mono text-xs">
                    {a.can_read ? "r" : "-"}
                    {a.can_write ? "w" : "-"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Dept grants — только cross_department */}
        {isCross && (
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3">
              Dept grants ({grantsQ.data?.items.length ?? 0})
            </div>
            {grantsQ.loading && (
              <div className="text-xs text-dim">Загрузка…</div>
            )}
            {grantsQ.error && (
              <div className="text-xs text-danger">
                {apiErrMsg(grantsQ.error, "Dept-grants не загрузились")}
              </div>
            )}
            {!grantsQ.loading && !grantsQ.error && (
              <div className="text-sm flex flex-col gap-1">
                {(grantsQ.data?.items ?? []).length === 0 && (
                  <div className="text-xs text-dim">Нет выданных grant'ов.</div>
                )}
                {(grantsQ.data?.items ?? []).map((g) => (
                  <div key={g.id} className="stat-row">
                    <span className="text-dim">{g.recipient_dept_id}</span>
                    <span className="mono text-xs">{g.granted_at}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat-row">
      <span className="text-dim">{label}</span>
      <span className="mono text-xs truncate max-w-[60%]">{value}</span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function CreatePane({
  defaultDeptId,
  onCancel,
  onSubmit,
}: {
  defaultDeptId: string | null;
  onCancel: () => void;
  onSubmit: (body: CredentialCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [service, setService] = useState("");
  const [scope, setScope] = useState<CredentialScope>("personal");
  const [login, setLogin] = useState("");
  const [secret, setSecret] = useState("");
  const [ownerDeptId, setOwnerDeptId] = useState(defaultDeptId ?? "");
  const [visibleToDept, setVisibleToDept] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const needsDept = scope === "department" || scope === "cross_department";
  const valid =
    name.trim() &&
    service.trim() &&
    secret.trim() &&
    (!needsDept || ownerDeptId.trim());

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const body: CredentialCreateRequest = {
      name: name.trim(),
      service: service.trim(),
      scope,
      secret,
      login: login.trim() || null,
      // owner_dept_id обязателен для dept-scope, запрещён для personal.
      owner_dept_id: needsDept ? ownerDeptId.trim() : null,
      visible_to_dept: needsDept ? visibleToDept : false,
    };
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 max-w-xl">
        <div className="flex items-center gap-2 mb-4">
          <button
            className="btn btn-ghost flex items-center gap-1"
            onClick={onCancel}
            type="button"
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </button>
          <div className="text-sm text-dim">Создание credential</div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">name *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={64}
              placeholder="core-postgres-replica"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">service *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={service}
              onChange={(e) => setService(e.target.value)}
              required
              maxLength={64}
              placeholder="postgres / jira / github…"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">scope *</span>
            <select
              className="surface-2 border border-token rounded px-2 py-1"
              value={scope}
              onChange={(e) => setScope(e.target.value as CredentialScope)}
            >
              <option value="personal">personal</option>
              <option value="department">department</option>
              <option value="cross_department">cross_department</option>
            </select>
          </label>
          {needsDept && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">owner_dept_id *</span>
              <input
                className="surface-2 border border-token rounded px-2 py-1"
                value={ownerDeptId}
                onChange={(e) => setOwnerDeptId(e.target.value)}
                required
                maxLength={64}
                placeholder="dept id"
              />
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">login</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              placeholder="опционально"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">secret *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1 mono"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
              maxLength={8192}
              placeholder="plaintext — шифруется at-rest"
            />
          </label>
          {needsDept && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={visibleToDept}
                onChange={(e) => setVisibleToDept(e.target.checked)}
              />
              <span className="text-dim text-xs">
                visible_to_dept (guest видит факт существования)
              </span>
            </label>
          )}

          <div className="flex items-center gap-2 mt-2">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting || !valid}
            >
              {submitting ? "Создаём…" : "Создать"}
            </button>
            <button type="button" className="btn" onClick={onCancel}>
              Отмена
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
