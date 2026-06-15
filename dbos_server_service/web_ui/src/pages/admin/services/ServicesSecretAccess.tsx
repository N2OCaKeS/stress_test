import { useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Search,
  Key,
  X,
  ShieldPlus,
  Building2,
  UserPlus,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { isSecretZoneBlocked } from "@/lib/rbac";
import { formatMsk } from "@/lib/datetime";
import { useDeptLabel, useUserLabel, useLabelMaps } from "@/lib/labels";
import {
  getCredential,
  listCredentials,
} from "@/api/secret/credentials";
import { addRoleAcl, listRoleAcls, revokeRoleAcl } from "@/api/secret/roleAcls";
import {
  addDeptGrant,
  listDeptGrants,
  revokeDeptGrant,
} from "@/api/secret/deptGrants";
import { addUserAcl, listUserAcls, revokeUserAcl } from "@/api/secret/userAcls";
import { listServiceRoles } from "@/api/auth/service_roles";
import { listUsersByDepartment } from "@/api/auth/users";
import type {
  Credential,
  CredentialScope,
} from "@/api/secret/types";

const SCOPE_LABEL: Record<CredentialScope, string> = {
  personal: "personal",
  department: "department",
  cross_department: "cross-dept",
};

/**
 * Per-credential управление доступом к секретам отдела. Слева — список кред
 * (поиск + фильтры scope/status), справа — три слоя доступа выбранной кред'ы:
 * RoleACL (роль×право внутри отдела), DeptGrant (cross-dept) и UserACL
 * (конкретные пользователи).
 *
 * RBAC: secret_service dept-scoped. Платформенные роли без отдела backend
 * режет 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT — им страница не показывается
 * (см. `isSecretZoneBlocked`). Видна dep_admin своего отдела и носителю
 * service-роли secret.admin.
 */
export function ServicesSecretAccess() {
  const { persona } = usePersona();
  const blocked = isSecretZoneBlocked(persona);
  const canManage =
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.secret === "admin";

  if (blocked || !canManage) {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · управление доступом к секретам доступно department_admin
            своего отдела или роли <span className="mono">secret.admin</span>.
            Платформенные роли без отдела не привязаны к secret_service.
          </span>
        </div>
      </div>
    );
  }

  return <ServicesSecretAccessLive />;
}

function ServicesSecretAccessLive() {
  const { persona } = usePersona();
  const myDept = persona.dept_id ?? null;

  const [search, setSearch] = useState("");
  const [filterScope, setFilterScope] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const PAGE_SIZE = 100;
  const listQ = useQuery(
    () =>
      listCredentials({
        limit: PAGE_SIZE,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
      }),
    [filterScope, filterStatus],
  );

  // Cursor «load more»: первая страница из listQ, остальное дозагружаем.
  const [extra, setExtra] = useState<Credential[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const toast = useToast();

  useEffect(() => {
    setExtra([]);
    setCursor(listQ.data?.next_cursor ?? null);
  }, [listQ.data]);

  const rawItems = (listQ.data?.items ?? []) as Credential[];
  const items = useMemo(() => [...rawItems, ...extra], [rawItems, extra]);

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
      setExtra((prev) => [...prev, ...(page.items as Credential[])]);
      setCursor(page.next_cursor ?? null);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось догрузить список"));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="p-6 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h3 className="font-semibold flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            Доступ к секретам · secret_service
          </h3>
          <span className="text-xs text-dim">
            scope отдела: <span className="mono">{myDept ?? "—"}</span>
          </span>
        </div>
        <p className="text-xs text-dim leading-relaxed">
          Выберите credential слева, чтобы настроить доступ. Три слоя:{" "}
          <b>RoleACL</b> — роль отдела × право (read/write); <b>DeptGrant</b> —
          открыть cross-dept креду другому отделу; <b>UserACL</b> — доступ
          конкретному пользователю. Изменения пишутся немедленно и аудируются.
        </p>
      </div>

      <div className="grid grid-cols-[minmax(260px,340px)_1fr] gap-4 items-start">
        {/* Список credential'ов */}
        <div className="card flex flex-col gap-2 min-h-0">
          <div className="flex items-center gap-2 border border-token rounded px-2 py-1 surface-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder={`Поиск по ${items.length} credential…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-1 text-[11px] text-dim">
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

          <div className="flex flex-col gap-0.5 max-h-[60vh] overflow-y-auto">
            {listQ.loading && (
              <div className="px-2 py-6 text-xs text-dim text-center">
                Загрузка…
              </div>
            )}
            {listQ.error && (
              <div className="alert alert-danger text-xs flex flex-col gap-2">
                <span>{apiErrMsg(listQ.error, "Список не загрузился")}</span>
                <button
                  className="btn btn-ghost btn-sm self-start"
                  onClick={() => listQ.refetch()}
                >
                  Повторить
                </button>
              </div>
            )}
            {!listQ.loading && !listQ.error && filtered.length === 0 && (
              <div className="px-2 py-6 text-xs text-dim text-center">
                Кред'ов не найдено.
              </div>
            )}
            {filtered.map((c) => (
              <CredRow
                key={c.id}
                cred={c}
                active={selectedId === c.id}
                onSelect={() => setSelectedId(c.id)}
              />
            ))}
          </div>

          {!listQ.loading && !listQ.error && cursor && !search.trim() && (
            <button
              className="btn btn-ghost w-full text-xs"
              onClick={handleLoadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Загрузка…" : "Загрузить ещё"}
            </button>
          )}
        </div>

        {/* Панель доступа */}
        {selectedId ? (
          <AccessPanel
            key={selectedId}
            credId={selectedId}
            actorDeptId={myDept}
          />
        ) : (
          <div className="card flex items-center justify-center min-h-[200px]">
            <div className="text-sm text-dim text-center">
              <Key className="w-8 h-8 mx-auto text-dim mb-2" />
              Выберите credential слева, чтобы управлять доступом.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CredRow({
  cred,
  active,
  onSelect,
}: {
  cred: Credential;
  active: boolean;
  onSelect: () => void;
}) {
  const blocked = cred.status === "blocked";
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`text-left rounded px-2 py-1.5 border ${
        active
          ? "border-accent surface-2"
          : "border-transparent hover:surface-2"
      }`}
    >
      <div className="flex items-center gap-2">
        <Key className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">{cred.name}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="mono truncate">{cred.service}</span>
            <span>·</span>
            <span>{SCOPE_LABEL[cred.scope] ?? cred.scope}</span>
          </div>
        </div>
        <span className={`badge badge-${blocked ? "danger" : "ok"}`}>
          {cred.status}
        </span>
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function AccessPanel({
  credId,
  actorDeptId,
}: {
  credId: string;
  actorDeptId: string | null;
}) {
  const toast = useToast();
  const credQ = useQuery(() => getCredential(credId), [credId]);
  const cred = credQ.data;
  const isCross = cred?.scope === "cross_department";

  const aclQ = useQuery(() => listRoleAcls(credId), [credId]);
  const grantsQ = useQuery(() => listDeptGrants(credId), [credId], {
    enabled: isCross,
  });
  const userAclQ = useQuery(() => listUserAcls(credId), [credId]);

  const [acting, setActing] = useState(false);
  const [addingAcl, setAddingAcl] = useState(false);
  const [addingGrant, setAddingGrant] = useState(false);
  const [addingUserAcl, setAddingUserAcl] = useState(false);

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
      toast.error(apiErrMsg(e, "Выдача RoleACL не удалась"));
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
      toast.error(apiErrMsg(e, "Снятие RoleACL не удалось"));
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
      toast.error(apiErrMsg(e, "Выдача DeptGrant не удалась"));
    }
  }

  async function handleGrantRevoke(grantId: string) {
    if (acting) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Снять DeptGrant? Это каскадно снимет RoleACL recipient-отдела.",
      )
    )
      return;
    setActing(true);
    try {
      await revokeDeptGrant(credId, grantId);
      toast.success("DeptGrant снят");
      grantsQ.refetch();
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие DeptGrant не удалось"));
    } finally {
      setActing(false);
    }
  }

  async function handleUserAclAdd(body: {
    user_id: string;
    can_read: boolean;
    can_write: boolean;
  }) {
    try {
      await addUserAcl(credId, body);
      toast.success("Доступ пользователю выдан");
      setAddingUserAcl(false);
      userAclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Выдача доступа не удалась"));
    }
  }

  async function handleUserAclRevoke(aclId: string) {
    if (acting) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("Снять доступ этого пользователя?")
    )
      return;
    setActing(true);
    try {
      await revokeUserAcl(credId, aclId);
      toast.success("Доступ снят");
      userAclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие доступа не удалось"));
    } finally {
      setActing(false);
    }
  }

  if (credQ.loading) {
    return (
      <div className="card flex items-center justify-center min-h-[200px]">
        <div className="text-sm text-dim">Загрузка…</div>
      </div>
    );
  }
  if (credQ.error || !cred) {
    return (
      <div className="card">
        <div className="alert alert-danger flex flex-col gap-2 text-xs">
          <span>{apiErrMsg(credQ.error, "Карточка не загрузилась")}</span>
          <button
            className="btn btn-ghost btn-sm self-start"
            onClick={() => credQ.refetch()}
          >
            Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center gap-3 flex-wrap">
          <Key className="w-5 h-5 text-accent" />
          <h3 className="font-semibold truncate">{cred.name}</h3>
          <span className={`badge badge-${cred.status === "blocked" ? "danger" : "ok"}`}>
            {cred.status}
          </span>
          <span className="text-xs text-dim">
            scope: <b>{SCOPE_LABEL[cred.scope] ?? cred.scope}</b>
          </span>
        </div>
        <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
          <span className="mono">{cred.id}</span>
          <span>·</span>
          <span>service: <b>{cred.service}</b></span>
          <span>·</span>
          <OwnerLabel cred={cred} />
        </div>
      </div>

      {/* RoleACL */}
      <AccessCard
        title={`RoleACL (${aclQ.data?.items.length ?? 0})`}
        icon={<ShieldPlus className="w-3.5 h-3.5" />}
        addLabel="Выдать роль"
        onAdd={() => setAddingAcl(true)}
        loading={aclQ.loading}
        error={aclQ.error ? apiErrMsg(aclQ.error, "RoleACL не загрузился") : null}
        empty={(aclQ.data?.items ?? []).length === 0}
        emptyText="Нет выданных RoleACL."
      >
        {(aclQ.data?.items ?? []).map((a) => (
          <RoleAclRow
            key={a.id}
            deptId={a.dept_id}
            roleName={a.role_name}
            canRead={a.can_read}
            canWrite={a.can_write}
            grantedBy={a.granted_by_user_id}
            grantedAt={a.granted_at}
            acting={acting}
            onRevoke={() => handleAclRevoke(a.id)}
          />
        ))}
      </AccessCard>

      {/* DeptGrant — только cross_department */}
      {isCross && (
        <AccessCard
          title={`DeptGrant (${grantsQ.data?.items.length ?? 0})`}
          icon={<Building2 className="w-3.5 h-3.5" />}
          addLabel="Выдать отделу"
          onAdd={() => setAddingGrant(true)}
          loading={grantsQ.loading}
          error={
            grantsQ.error ? apiErrMsg(grantsQ.error, "DeptGrant не загрузился") : null
          }
          empty={(grantsQ.data?.items ?? []).length === 0}
          emptyText="Нет выданных DeptGrant'ов."
        >
          {(grantsQ.data?.items ?? []).map((g) => (
            <DeptGrantRow
              key={g.id}
              recipientDeptId={g.recipient_dept_id}
              grantedBy={g.granted_by_user_id}
              grantedAt={g.granted_at}
              acting={acting}
              onRevoke={() => handleGrantRevoke(g.id)}
            />
          ))}
        </AccessCard>
      )}

      {/* UserACL */}
      <AccessCard
        title={`UserACL (${userAclQ.data?.items.length ?? 0})`}
        icon={<UserPlus className="w-3.5 h-3.5" />}
        addLabel="Выдать пользователю"
        onAdd={() => setAddingUserAcl(true)}
        loading={userAclQ.loading}
        error={
          userAclQ.error ? apiErrMsg(userAclQ.error, "UserACL не загрузился") : null
        }
        empty={(userAclQ.data?.items ?? []).length === 0}
        emptyText="Нет выданных UserACL."
      >
        {(userAclQ.data?.items ?? []).map((a) => (
          <UserAclRow
            key={a.id}
            userId={a.user_id}
            canRead={a.can_read}
            canWrite={a.can_write}
            grantedBy={a.granted_by_user_id}
            grantedAt={a.created_at}
            acting={acting}
            onRevoke={() => handleUserAclRevoke(a.id)}
          />
        ))}
      </AccessCard>

      {addingAcl && (
        <AclModal
          isCross={isCross}
          actorDeptId={actorDeptId}
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
      {addingUserAcl && (
        <UserAclModal
          actorDeptId={actorDeptId}
          onClose={() => setAddingUserAcl(false)}
          onSubmit={handleUserAclAdd}
        />
      )}
    </div>
  );
}

function OwnerLabel({ cred }: { cred: Credential }) {
  const deptName = useDeptLabel(cred.owner_dept_id);
  const userName = useUserLabel(cred.owner_user_id);
  if (cred.owner_user_id) {
    return (
      <span title={cred.owner_user_id}>
        owner: <b>{userName}</b>
      </span>
    );
  }
  return (
    <span title={cred.owner_dept_id ?? undefined}>
      owner-отдел: <b>{deptName}</b>
    </span>
  );
}

function AccessCard({
  title,
  icon,
  addLabel,
  onAdd,
  loading,
  error,
  empty,
  emptyText,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  addLabel: string;
  onAdd: () => void;
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyText: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs uppercase tracking-wider text-dim">{title}</div>
        <button
          className="btn btn-ghost text-xs flex items-center gap-1"
          onClick={onAdd}
        >
          {icon} {addLabel}
        </button>
      </div>
      {loading && <div className="text-xs text-dim">Загрузка…</div>}
      {error && <div className="text-xs text-danger">{error}</div>}
      {!loading && !error && (
        <div className="text-sm flex flex-col gap-1">
          {empty ? (
            <div className="text-xs text-dim">{emptyText}</div>
          ) : (
            children
          )}
        </div>
      )}
    </div>
  );
}

function RoleAclRow({
  deptId,
  roleName,
  canRead,
  canWrite,
  grantedBy,
  grantedAt,
  acting,
  onRevoke,
}: {
  deptId: string;
  roleName: string;
  canRead: boolean;
  canWrite: boolean;
  grantedBy: string;
  grantedAt: string;
  acting: boolean;
  onRevoke: () => void;
}) {
  const deptName = useDeptLabel(deptId);
  const grantedByName = useUserLabel(grantedBy);
  return (
    <div className="stat-row items-center">
      <span className="text-dim" title={deptId}>
        {deptName} / <span className="mono">{roleName}</span>
      </span>
      <span className="flex items-center gap-2">
        <span
          className="text-[11px] text-dim"
          title={`выдал ${grantedByName} · ${formatMsk(grantedAt)}`}
        >
          {grantedByName}
        </span>
        <span className="mono text-xs">
          {canRead ? "r" : "-"}
          {canWrite ? "w" : "-"}
        </span>
        <button
          className="btn btn-ghost p-1"
          title="Снять RoleACL"
          disabled={acting}
          onClick={onRevoke}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </span>
    </div>
  );
}

function DeptGrantRow({
  recipientDeptId,
  grantedBy,
  grantedAt,
  acting,
  onRevoke,
}: {
  recipientDeptId: string;
  grantedBy: string;
  grantedAt: string;
  acting: boolean;
  onRevoke: () => void;
}) {
  const deptName = useDeptLabel(recipientDeptId);
  const grantedByName = useUserLabel(grantedBy);
  return (
    <div className="stat-row items-center">
      <span className="text-dim" title={recipientDeptId}>
        {deptName}
      </span>
      <span className="flex items-center gap-2">
        <span
          className="text-[11px] text-dim"
          title={`выдал ${grantedByName}`}
        >
          {formatMsk(grantedAt)}
        </span>
        <button
          className="btn btn-ghost p-1"
          title="Снять DeptGrant"
          disabled={acting}
          onClick={onRevoke}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </span>
    </div>
  );
}

function UserAclRow({
  userId,
  canRead,
  canWrite,
  grantedBy,
  grantedAt,
  acting,
  onRevoke,
}: {
  userId: string;
  canRead: boolean;
  canWrite: boolean;
  grantedBy: string;
  grantedAt: string;
  acting: boolean;
  onRevoke: () => void;
}) {
  const userName = useUserLabel(userId);
  const grantedByName = useUserLabel(grantedBy);
  return (
    <div className="stat-row items-center">
      <span className="text-dim" title={userId}>
        {userName}
      </span>
      <span className="flex items-center gap-2">
        <span
          className="text-[11px] text-dim"
          title={`выдал ${grantedByName} · ${formatMsk(grantedAt)}`}
        >
          {grantedByName}
        </span>
        <span className="mono text-xs">
          {canRead ? "r" : "-"}
          {canWrite ? "w" : "-"}
        </span>
        <button
          className="btn btn-ghost p-1"
          title="Снять UserACL"
          disabled={acting}
          onClick={onRevoke}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Модалки

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
          <button
            className="btn btn-ghost p-1"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * Выбор отдела по имени. Карта непуста — dropdown имён (значение — `dep_*` id).
 * Пуста (dep_admin видит только свой отдел через identity-сид) — текстовый
 * ввод сырого id.
 */
function DeptPicker({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
}) {
  const { depts } = useLabelMaps();
  const options = useMemo(
    () => [...depts.entries()].sort((a, b) => a[1].localeCompare(b[1])),
    [depts],
  );

  if (options.length === 0) {
    return (
      <input
        className="surface-2 border border-token rounded px-2 py-1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={64}
        required
        placeholder={placeholder ?? "dep_…"}
        title={value || undefined}
      />
    );
  }

  return (
    <select
      className="surface-2 border border-token rounded px-2 py-1"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      required
      title={value || undefined}
    >
      <option value="" disabled>
        — выберите отдел —
      </option>
      {options.map(([id, name]) => (
        <option key={id} value={id} title={id}>
          {name}
        </option>
      ))}
    </select>
  );
}

/**
 * Выбор роли отдела. Тянет каталог `secret`-ролей выбранного отдела через
 * `listServiceRoles(dept, "secret")`; если список пуст / недоступен — текстовый
 * ввод имени роли.
 */
function RolePicker({
  deptId,
  value,
  onChange,
}: {
  deptId: string;
  value: string;
  onChange: (role: string) => void;
}) {
  const rolesQ = useQuery(
    () => listServiceRoles(deptId, "secret"),
    [deptId],
    { enabled: !!deptId },
  );
  const roles = rolesQ.data ?? [];

  if (!deptId || roles.length === 0) {
    return (
      <input
        className="surface-2 border border-token rounded px-2 py-1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={64}
        required
        placeholder="reader / operator / …"
      />
    );
  }
  return (
    <select
      className="surface-2 border border-token rounded px-2 py-1"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      required
    >
      <option value="" disabled>
        — выберите роль —
      </option>
      {roles.map((r) => (
        <option key={r.role_name} value={r.role_name}>
          {r.role_name}
        </option>
      ))}
    </select>
  );
}

function AclModal({
  isCross,
  actorDeptId,
  onClose,
  onSubmit,
}: {
  isCross: boolean;
  actorDeptId: string | null;
  onClose: () => void;
  onSubmit: (body: {
    dept_id: string;
    role_name: string;
    can_read: boolean;
    can_write: boolean;
  }) => void | Promise<void>;
}) {
  const { depts } = useLabelMaps();
  // personal/department: ACL действует в отделе-владельце = свой отдел актора
  // (залочено). cross_department: dept_id — recipient-отдел, даём пикер.
  const lockedDept = !isCross && !!actorDeptId;
  const [deptId, setDeptId] = useState(lockedDept ? (actorDeptId ?? "") : "");
  const [roleName, setRoleName] = useState("");
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
            Cross-dept recipient требует заранее выданного DeptGrant — иначе
            backend отбивает (DEPT_GRANT_REQUIRED).
          </div>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">отдел *</span>
          {lockedDept ? (
            <input
              className="surface-2 border border-token rounded px-2 py-1 text-dim"
              value={depts.get(deptId) ?? deptId}
              readOnly
              disabled
              title={deptId}
            />
          ) : (
            <DeptPicker value={deptId} onChange={setDeptId} placeholder="dep_…" />
          )}
          {lockedDept && (
            <span className="text-[11px] text-dim">ACL действует в вашем отделе.</span>
          )}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">роль *</span>
          <RolePicker deptId={deptId} value={roleName} onChange={setRoleName} />
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
          DeptGrant даёт recipient-отделу право получать RoleACL на эту cross-dept
          креду. Снятие grant'а каскадно снимает RoleACL recipient-отдела.
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">отдел-получатель *</span>
          <DeptPicker value={deptId} onChange={setDeptId} placeholder="dep_…" />
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

/**
 * Выдача UserACL: ввод по username с резолвом в `usr_*` (список пользователей
 * отдела через `listUsersByDepartment`). Если списка нет (нет dept-scope) —
 * принимаем сырой `usr_*` id.
 */
function UserAclModal({
  actorDeptId,
  onClose,
  onSubmit,
}: {
  actorDeptId: string | null;
  onClose: () => void;
  onSubmit: (body: {
    user_id: string;
    can_read: boolean;
    can_write: boolean;
  }) => void | Promise<void>;
}) {
  const usersQ = useQuery(
    () => listUsersByDepartment(actorDeptId as string, { limit: 200 }),
    [actorDeptId],
    { enabled: !!actorDeptId },
  );
  const canResolve = !!actorDeptId && (usersQ.data?.items.length ?? 0) > 0;

  const [input, setInput] = useState("");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  function resolveUserId(): string | null {
    const raw = input.trim();
    if (!raw) return null;
    if (raw.startsWith("usr_")) return raw;
    const match = usersQ.data?.items.find(
      (u) => u.username.toLowerCase() === raw.toLowerCase(),
    );
    return match?.id ?? null;
  }

  const valid = input.trim() && (canRead || canWrite);
  const resolveFailed =
    input.trim() !== "" &&
    !input.trim().startsWith("usr_") &&
    canResolve &&
    resolveUserId() === null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const userId = resolveUserId();
    if (!userId) return;
    setSubmitting(true);
    Promise.resolve(
      onSubmit({ user_id: userId, can_read: canRead, can_write: canWrite }),
    ).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Выдать UserACL" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <div className="text-xs text-dim">
          Доступ выдаётся конкретному пользователю в дополнение к ролевым ACL.
          {canResolve
            ? " Введите username — он будет сопоставлен с id."
            : " Введите id пользователя (usr_…) — резолв по username недоступен."}
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            {canResolve ? "username *" : "user id (usr_…) *"}
          </span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={64}
            required
            list={canResolve ? "secret-admin-useracl-users" : undefined}
            placeholder={canResolve ? "username" : "usr_…"}
          />
          {canResolve && (
            <datalist id="secret-admin-useracl-users">
              {(usersQ.data?.items ?? []).map((u) => (
                <option key={u.id} value={u.username} />
              ))}
            </datalist>
          )}
          {resolveFailed && (
            <span className="text-[11px] text-danger">
              Пользователь с таким username не найден в вашем отделе. Можно
              ввести id (usr_…) напрямую.
            </span>
          )}
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
