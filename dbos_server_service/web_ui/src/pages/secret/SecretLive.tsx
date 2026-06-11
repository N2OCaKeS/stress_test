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
import { useEffect, useMemo, useState } from "react";
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
  Pencil,
  ArrowRightLeft,
  ShieldPlus,
  Building2,
  X,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { formatMsk } from "@/lib/datetime";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  createCredential,
  deleteCredential,
  getCredential,
  listCredentials,
  recoverCredential,
  revealCredential,
  transferCredential,
  updateCredential,
} from "@/api/secret/credentials";
import { addRoleAcl, listRoleAcls, revokeRoleAcl } from "@/api/secret/roleAcls";
import {
  addDeptGrant,
  listDeptGrants,
  revokeDeptGrant,
} from "@/api/secret/deptGrants";
import type {
  Credential,
  CredentialCreateRequest,
  CredentialScope,
  CredentialUpdateRequest,
  TransferRequest,
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

/**
 * `<input type="datetime-local">` отдаёт `YYYY-MM-DDTHH:mm` без зоны. Backend
 * валидирует окно валидности в UTC и naive-строку трактует как UTC, поэтому
 * добавляем `:00Z` — иначе локальное время уехало бы на смещение зоны.
 */
function localToIso(local: string): string {
  return local.length === 16 ? `${local}:00Z` : local;
}

/** ISO-таймстамп → значение для `datetime-local` (`YYYY-MM-DDTHH:mm`, UTC). */
function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.slice(0, 16);
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

  const PAGE_SIZE = 50;
  const listQ = useQuery(
    () =>
      listCredentials({
        limit: PAGE_SIZE,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
      }),
    [filterScope, filterStatus],
  );

  // Аккумулятор для cursor «load more»: первая страница приходит из listQ,
  // последующие — дозагружаем по next_cursor и добавляем сюда. Сбрасываем,
  // когда меняются фильтры (новый набор данных).
  const [extraItems, setExtraItems] = useState<
    Array<Credential & { status?: string }>
  >([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setExtraItems([]);
    setCursor(listQ.data?.next_cursor ?? null);
  }, [listQ.data]);

  const canManage =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "dep_admin" ||
    persona.service_roles.secret === "admin" ||
    persona.service_roles.secret === "operator";

  // Список может прийти как полный (CredentialList) или guest-урезанный
  // (CredentialGuestList). Поля name/id/service/scope есть в обоих shape'ах —
  // их и показываем в aside. Guest-shape не несёт owner_user_id/status —
  // по их отсутствию и распознаём read-only режим без reveal-доступа.
  const rawItems = listQ.data?.items ?? [];
  const firstRow = rawItems[0] as (Credential & { status?: string }) | undefined;
  const isGuestList =
    rawItems.length > 0 && firstRow !== undefined && !("owner_user_id" in firstRow);
  const items = [
    ...(rawItems as Array<Credential & { status?: string }>),
    ...extraItems,
  ];

  async function handleLoadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listCredentials({
        limit: PAGE_SIZE,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
        cursor,
      });
      setExtraItems((prev) => [
        ...prev,
        ...(page.items as Array<Credential & { status?: string }>),
      ]);
      setCursor(page.next_cursor ?? null);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось догрузить список"));
    } finally {
      setLoadingMore(false);
    }
  }

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
        {!listQ.loading && !listQ.error && cursor && !search.trim() && (
          <div className="px-3 pt-2">
            <button
              className="btn btn-ghost w-full text-xs"
              onClick={handleLoadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Загрузка…" : "Загрузить ещё"}
            </button>
          </div>
        )}
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
          isGuest={isGuestList}
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
  isGuest,
  onDelete,
  onChanged,
}: {
  credId: string;
  canManage: boolean;
  isGuest: boolean;
  onDelete: (c: Credential) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const credQ = useQuery(() => getCredential(credId), [credId]);
  const cred = credQ.data;

  const isCross = cred?.scope === "cross_department";
  // Guest без reveal-доступа не нагружаем ACL/grant-листингами (бэк всё равно
  // отобьёт 403), показываем только метаданные.
  const aclQ = useQuery(() => listRoleAcls(credId), [credId], {
    enabled: !isGuest,
  });
  const grantsQ = useQuery(() => listDeptGrants(credId), [credId], {
    enabled: isCross && !isGuest,
  });

  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  // 429-countdown: per-IP rate-limit или actor-lockout кладёт
  // retry_after_seconds; гасим Reveal на это окно и тикаем секундами для UX.
  // (5-минутный reveal-throttle сам по себе не 429 — он лишь меняет severity
  // audit-события, секрет всё равно отдаётся.)
  const [throttleUntil, setThrottleUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // Модалки управления.
  const [editing, setEditing] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [addingAcl, setAddingAcl] = useState(false);
  const [addingGrant, setAddingGrant] = useState(false);

  // Общий гейт для прямых мутаций detail-панели (recover / delete / снятие
  // ACL и grant) — блокирует двойной клик, пока запрос в полёте.
  const [acting, setActing] = useState(false);

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);

  // Сбрасываем reveal/throttle при переходе на другую креду.
  useEffect(() => {
    setRevealed(null);
    setThrottleUntil(0);
  }, [credId]);

  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));

  async function handleReveal() {
    if (revealing || throttleLeft > 0) return;
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
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 300;
        setThrottleUntil(Date.now() + secs * 1000);
        setNow(Date.now());
        toast.error(`Throttled: повторите через ${secs} сек`);
      } else {
        toast.error(apiErrMsg(e, "Reveal не удался"));
      }
    } finally {
      setRevealing(false);
    }
  }

  async function handleRecover() {
    if (acting) return;
    setActing(true);
    try {
      await recoverCredential(credId);
      toast.success("Credential разблокирован");
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Recover не удался"));
    } finally {
      setActing(false);
    }
  }

  async function handleEditSubmit(body: CredentialUpdateRequest) {
    try {
      await updateCredential(credId, body);
      toast.success("Credential обновлён");
      setEditing(false);
      setRevealed(null);
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление не удалось"));
    }
  }

  async function handleTransferSubmit(body: TransferRequest) {
    try {
      await transferCredential(credId, body);
      toast.success("Ownership передан, credential разблокирован");
      setTransferring(false);
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Transfer не удался"));
    }
  }

  async function handleAclAdd(body: {
    dept_id: string;
    role_name: string;
    can_read: boolean;
    can_write: boolean;
  }) {
    try {
      await addRoleAcl(credId, body);
      toast.success("RoleACL выдан");
      setAddingAcl(false);
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Выдача ACL не удалась"));
    }
  }

  async function handleAclRevoke(aclId: string) {
    if (acting) return;
    if (typeof window !== "undefined" && !window.confirm("Снять этот RoleACL?"))
      return;
    setActing(true);
    try {
      await revokeRoleAcl(credId, aclId);
      toast.success("RoleACL снят");
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие ACL не удалось"));
    } finally {
      setActing(false);
    }
  }

  async function handleGrantAdd(recipientDeptId: string) {
    try {
      await addDeptGrant(credId, { recipient_dept_id: recipientDeptId });
      toast.success("DeptGrant выдан");
      setAddingGrant(false);
      grantsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Выдача grant'а не удалась"));
    }
  }

  async function handleGrantRevoke(grantId: string) {
    if (acting) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("Снять DeptGrant? Это каскадно снимет RoleACL recipient-dep'а.")
    )
      return;
    setActing(true);
    try {
      await revokeDeptGrant(credId, grantId);
      toast.success("DeptGrant снят");
      grantsQ.refetch();
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие grant'а не удалось"));
    } finally {
      setActing(false);
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
            {!blocked && (
              <button
                className="btn"
                onClick={() => setEditing(true)}
                disabled={acting}
                title="Редактировать"
              >
                <Pencil className="w-4 h-4 inline-block" /> Edit
              </button>
            )}
            {blocked && (
              <>
                <button
                  className="btn"
                  onClick={handleRecover}
                  disabled={acting}
                  title="Recover"
                >
                  <RotateCcw className="w-4 h-4 inline-block" /> Recover
                </button>
                <button
                  className="btn"
                  onClick={() => setTransferring(true)}
                  disabled={acting}
                  title="Transfer ownership"
                >
                  <ArrowRightLeft className="w-4 h-4 inline-block" /> Transfer
                </button>
              </>
            )}
            <button
              className="btn btn-danger"
              onClick={() => onDelete(cred)}
              disabled={acting}
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
            {isGuest ? (
              <span className="text-[11px] text-dim">
                guest: reveal недоступен
              </span>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-warn">
                  {throttleLeft > 0
                    ? `rate-limited: ${throttleLeft}с`
                    : "reveal → CRITICAL audit"}
                </span>
                <button
                  className="btn"
                  onClick={handleReveal}
                  disabled={revealing || blocked || throttleLeft > 0}
                >
                  <Eye className="w-4 h-4 inline-block" />{" "}
                  <span>
                    {revealing
                      ? "…"
                      : throttleLeft > 0
                      ? `${throttleLeft}с`
                      : "Reveal"}
                  </span>
                </button>
                <button
                  className="btn"
                  onClick={handleCopy}
                  disabled={!revealed}
                >
                  <Copy className="w-4 h-4 inline-block" />
                </button>
              </div>
            )}
          </div>
          <div
            className={`mono text-lg p-3 surface-2 rounded border border-token ${
              revealed === null ? "secret-mask" : ""
            }`}
          >
            {revealed ?? "••••••••••••••••••••••••••"}
          </div>
          <div className="text-xs text-dim mt-2">
            {isGuest
              ? "Guest видит метаданные; reveal закрыт — запросите доступ у dep_admin."
              : "Reveal эмитит CRITICAL audit-событие; rate-limit / lockout отвечает 429 с Retry-After."}
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
            <MetaRow label="Created" value={formatMsk(cred.created_at)} />
            <MetaRow label="Updated" value={formatMsk(cred.updated_at)} />
            <MetaRow label="visible_to_dept" value={String(cred.visible_to_dept)} />
            <MetaRow label="valid_from" value={cred.valid_from ?? "—"} />
            <MetaRow label="valid_to" value={cred.valid_to ?? "—"} />
            {blocked && (
              <>
                <MetaRow label="blocked_at" value={formatMsk(cred.blocked_at)} />
                <MetaRow
                  label="blocked_reason"
                  value={cred.blocked_reason ?? "—"}
                />
              </>
            )}
          </div>
        </div>

        {/* Access — RoleACL */}
        {!isGuest && (
          <div className="surface border border-token rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim">
                RoleACL ({aclQ.data?.items.length ?? 0})
              </div>
              {canManage && (
                <button
                  className="btn btn-ghost text-xs flex items-center gap-1"
                  onClick={() => setAddingAcl(true)}
                >
                  <ShieldPlus className="w-3.5 h-3.5" /> Выдать
                </button>
              )}
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
                  <div key={a.id} className="stat-row items-center">
                    <span className="text-dim">
                      {a.dept_id} / {a.role_name}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="mono text-xs">
                        {a.can_read ? "r" : "-"}
                        {a.can_write ? "w" : "-"}
                      </span>
                      {canManage && (
                        <button
                          className="btn btn-ghost p-1"
                          title="Снять ACL"
                          disabled={acting}
                          onClick={() => handleAclRevoke(a.id)}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Dept grants — только cross_department */}
        {isCross && !isGuest && (
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim">
                Dept grants ({grantsQ.data?.items.length ?? 0})
              </div>
              {canManage && (
                <button
                  className="btn btn-ghost text-xs flex items-center gap-1"
                  onClick={() => setAddingGrant(true)}
                >
                  <Building2 className="w-3.5 h-3.5" /> Выдать grant
                </button>
              )}
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
                  <div key={g.id} className="stat-row items-center">
                    <span className="text-dim">{g.recipient_dept_id}</span>
                    <span className="flex items-center gap-2">
                      <span className="mono text-xs">{formatMsk(g.granted_at)}</span>
                      {canManage && (
                        <button
                          className="btn btn-ghost p-1"
                          title="Снять grant"
                          disabled={acting}
                          onClick={() => handleGrantRevoke(g.id)}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {editing && cred && (
        <EditModal
          cred={cred}
          onClose={() => setEditing(false)}
          onSubmit={handleEditSubmit}
        />
      )}
      {transferring && cred && (
        <TransferModal
          scope={cred.scope}
          onClose={() => setTransferring(false)}
          onSubmit={handleTransferSubmit}
        />
      )}
      {addingAcl && (
        <AclModal
          isCross={isCross}
          onClose={() => setAddingAcl(false)}
          onSubmit={handleAclAdd}
        />
      )}
      {addingGrant && (
        <GrantModal
          onClose={() => setAddingGrant(false)}
          onSubmit={handleGrantAdd}
        />
      )}
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
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const needsDept = scope === "department" || scope === "cross_department";
  // valid_to обязан быть в будущем и строго позже valid_from — backend
  // отбивает 422; гасим submit заранее, чтобы не ловить ошибку формой.
  const windowInvalid =
    (validTo !== "" && new Date(validTo).getTime() <= Date.now()) ||
    (validFrom !== "" &&
      validTo !== "" &&
      new Date(validTo).getTime() <= new Date(validFrom).getTime());
  const valid =
    name.trim() &&
    service.trim() &&
    secret.trim() &&
    (!needsDept || ownerDeptId.trim()) &&
    !windowInvalid;

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
      // datetime-local даёт naive-строку без зоны; backend трактует naive как
      // UTC. NULL = open-ended с этой стороны.
      valid_from: validFrom ? localToIso(validFrom) : null,
      valid_to: validTo ? localToIso(validTo) : null,
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
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">valid_from (UTC)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-2 py-1"
                value={validFrom}
                onChange={(e) => setValidFrom(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">valid_to (UTC, в будущем)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-2 py-1"
                value={validTo}
                onChange={(e) => setValidTo(e.target.value)}
              />
            </label>
          </div>
          {windowInvalid && (
            <div className="text-xs text-danger">
              valid_to должен быть в будущем и строго позже valid_from.
            </div>
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

// ───────────────────────────────────────────────────────────────────────────
// Модалки управления

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header flex items-center justify-between">
          <div className="text-sm font-semibold">{title}</div>
          <button className="btn btn-ghost p-1" onClick={onClose} aria-label="Закрыть">
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function EditModal({
  cred,
  onClose,
  onSubmit,
}: {
  cred: Credential;
  onClose: () => void;
  onSubmit: (body: CredentialUpdateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState(cred.name);
  const [login, setLogin] = useState(cred.login ?? "");
  const [secret, setSecret] = useState("");
  const [validFrom, setValidFrom] = useState(isoToLocal(cred.valid_from));
  const [validTo, setValidTo] = useState(isoToLocal(cred.valid_to));
  const [submitting, setSubmitting] = useState(false);

  // backend требует valid_to > valid_from; гасим submit, чтобы не словить 422.
  const windowInvalid =
    validFrom !== "" &&
    validTo !== "" &&
    new Date(validTo).getTime() <= new Date(validFrom).getTime();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || windowInvalid) return;
    // Шлём только реально изменённые поля. Пустой secret = не перешифровывать.
    // Очистку окна через PATCH backend намеренно не маппит (NULL не снимает
    // защиту), поэтому пустое поле = «не трогать», а не «обнулить».
    const body: CredentialUpdateRequest = {};
    if (name.trim() && name.trim() !== cred.name) body.name = name.trim();
    if (login.trim() !== (cred.login ?? "")) body.login = login.trim() || null;
    if (secret) body.secret = secret;
    if (validFrom && validFrom !== isoToLocal(cred.valid_from))
      body.valid_from = localToIso(validFrom);
    if (validTo && validTo !== isoToLocal(cred.valid_to))
      body.valid_to = localToIso(validTo);
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title={`Редактировать ${cred.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">name</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={64}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">login</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            maxLength={4096}
            placeholder="пусто = очистить login"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">new secret (re-encrypt)</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 mono"
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            maxLength={8192}
            placeholder="пусто = оставить текущий секрет"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">valid_from (UTC)</span>
            <input
              type="datetime-local"
              className="surface-2 border border-token rounded px-2 py-1"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">valid_to (UTC)</span>
            <input
              type="datetime-local"
              className="surface-2 border border-token rounded px-2 py-1"
              value={validTo}
              onChange={(e) => setValidTo(e.target.value)}
            />
          </label>
        </div>
        {windowInvalid && (
          <div className="text-xs text-danger">
            valid_to должен быть строго позже valid_from.
          </div>
        )}
        <div className="flex items-center gap-2 mt-1">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || windowInvalid}
          >
            {submitting ? "Сохраняем…" : "Сохранить"}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

function TransferModal({
  scope,
  onClose,
  onSubmit,
}: {
  scope: CredentialScope;
  onClose: () => void;
  onSubmit: (body: TransferRequest) => void | Promise<void>;
}) {
  // personal → новый owner-user; department/cross_department → новый owner-dept.
  const isPersonal = scope === "personal";
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const valid = target.trim() && reason.trim();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const body: TransferRequest = isPersonal
      ? { new_owner_user_id: target.trim(), reason: reason.trim() }
      : { new_owner_dept_id: target.trim(), reason: reason.trim() };
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Transfer ownership" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <div className="text-xs text-dim">
          Transfer допустим только для заблокированной кред'ы и снимает блокировку.
          Гейтится admin secret_service владеющего dept'а.
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            {isPersonal ? "new_owner_user_id *" : "new_owner_dept_id *"}
          </span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            maxLength={64}
            required
            placeholder={isPersonal ? "usr_…" : "dept id"}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">reason * (попадёт в audit)</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={256}
            required
          />
        </label>
        <div className="flex items-center gap-2 mt-1">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || !valid}
          >
            {submitting ? "Передаём…" : "Передать"}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

function AclModal({
  isCross,
  onClose,
  onSubmit,
}: {
  isCross: boolean;
  onClose: () => void;
  onSubmit: (body: {
    dept_id: string;
    role_name: string;
    can_read: boolean;
    can_write: boolean;
  }) => void | Promise<void>;
}) {
  const [deptId, setDeptId] = useState("");
  const [roleName, setRoleName] = useState("reader");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const valid = deptId.trim() && roleName.trim() && (canRead || canWrite);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    setSubmitting(true);
    Promise.resolve(
      onSubmit({
        dept_id: deptId.trim(),
        role_name: roleName.trim(),
        can_read: canRead,
        can_write: canWrite,
      }),
    ).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Выдать RoleACL" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        {isCross && (
          <div className="text-xs text-warn">
            Cross-dept recipient требует заранее выданного DeptGrant — иначе backend
            отбивает (DEPT_GRANT_REQUIRED).
          </div>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">dept_id *</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={deptId}
            onChange={(e) => setDeptId(e.target.value)}
            maxLength={64}
            required
            placeholder="dept id"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">role_name *</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={roleName}
            onChange={(e) => setRoleName(e.target.value)}
            maxLength={64}
            required
            placeholder="reader / operator / …"
          />
        </label>
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={canRead}
              onChange={(e) => setCanRead(e.target.checked)}
            />
            <span className="text-dim text-xs">can_read</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={canWrite}
              onChange={(e) => setCanWrite(e.target.checked)}
            />
            <span className="text-dim text-xs">can_write</span>
          </label>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || !valid}
          >
            {submitting ? "Выдаём…" : "Выдать"}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

function GrantModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (recipientDeptId: string) => void | Promise<void>;
}) {
  const [deptId, setDeptId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !deptId.trim()) return;
    setSubmitting(true);
    Promise.resolve(onSubmit(deptId.trim())).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Выдать DeptGrant" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <div className="text-xs text-dim">
          DeptGrant даёт recipient-dep'у право получать RoleACL на эту cross-dept
          креду. Снятие grant'а каскадно снимает RoleACL recipient-dep'а.
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">recipient_dept_id *</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={deptId}
            onChange={(e) => setDeptId(e.target.value)}
            maxLength={64}
            required
            placeholder="dept id"
          />
        </label>
        <div className="flex items-center gap-2 mt-1">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || !deptId.trim()}
          >
            {submitting ? "Выдаём…" : "Выдать"}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
