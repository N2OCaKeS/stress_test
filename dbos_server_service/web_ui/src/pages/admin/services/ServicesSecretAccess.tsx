import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Search,
  Key,
  X,
  Building2,
  UserPlus,
  Loader2,
  Lock,
  Grid3x3,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
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
  RoleACL,
} from "@/api/secret/types";

// Системные роли каталога secret_service в порядке отображения; кастомные роли
// отдела идут после по алфавиту.
const SECRET_ROLE_ORDER = ["guest", "reader", "operator", "admin"];

function roleSortKey(role: string): string {
  const idx = SECRET_ROLE_ORDER.indexOf(role);
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

// Права, по которым строятся строки матрицы.
type AclRight = "read" | "write";
const ACL_RIGHTS: AclRight[] = ["read", "write"];

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
          Выберите credential слева, чтобы настроить доступ. Матрица доступа:
          столбцы — <b>роли отдела</b>, строки — права <b>read</b> / <b>write</b>,
          клик по ячейке выдаёт или отзывает право. <b>DeptGrant</b> открывает
          cross-dept креду другому отделу; <b>UserACL</b> (только для personal-кред)
          — доступ конкретному пользователю. Изменения пишутся немедленно и
          аудируются.
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
  const isPersonal = cred?.scope === "personal";

  const aclQ = useQuery(() => listRoleAcls(credId), [credId]);
  const grantsQ = useQuery(() => listDeptGrants(credId), [credId], {
    enabled: isCross,
  });
  const userAclQ = useQuery(() => listUserAcls(credId), [credId], {
    enabled: isPersonal,
  });

  const confirm = useConfirm();
  const [acting, setActing] = useState(false);
  const [addingGrant, setAddingGrant] = useState(false);
  const [addingUserAcl, setAddingUserAcl] = useState(false);

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
      !(await confirm.confirm({
        message:
          "Снять DeptGrant? Это каскадно снимет RoleACL recipient-отдела.",
        danger: true,
        confirmLabel: "Снять",
      }))
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
      !(await confirm.confirm({
        message: "Снять доступ этого пользователя?",
        danger: true,
        confirmLabel: "Снять",
      }))
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

      {/* RoleACL — матрица: столбцы=роли, строки=read/write */}
      <RoleAccessMatrix
        credId={credId}
        scope={cred.scope}
        ownerDeptId={cred.owner_dept_id}
        actorDeptId={actorDeptId}
        acls={aclQ.data?.items ?? []}
        deptGrants={grantsQ.data?.items ?? []}
        loading={aclQ.loading}
        error={aclQ.error ? apiErrMsg(aclQ.error, "RoleACL не загрузился") : null}
        onChanged={() => aclQ.refetch()}
        onRetry={() => aclQ.refetch()}
      />

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

      {/* UserACL — только для personal-кред (backend отбивает остальные
          422 USER_ACL_SCOPE_NOT_PERSONAL). Для не-personal показываем
          задизейбленный блок с пояснением. */}
      {isPersonal ? (
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
      ) : (
        <div className="card opacity-70">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
              <Lock className="w-3.5 h-3.5" /> UserACL
            </div>
          </div>
          <p className="text-xs text-dim leading-relaxed">
            Доступ конкретному пользователю выдаётся только для{" "}
            <span className="mono">personal</span>-кред. Для{" "}
            <span className="mono">{SCOPE_LABEL[cred.scope] ?? cred.scope}</span>{" "}
            используйте матрицу ролей выше — backend отбивает UserACL для не-personal
            (<span className="mono">422 USER_ACL_SCOPE_NOT_PERSONAL</span>).
          </p>
        </div>
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

// ───────────────────────────────────────────────────────────────────────────
// RoleACL-матрица: столбцы — роли отдела, строки — права read/write.
//
// Бэкенд хранит RoleACL пер (dept, role) с парой флагов can_read/can_write и
// не умеет upsert (POST 409'ит дубль, PATCH'а нет). Поэтому смена ячейки —
// это revoke существующего ACL'я и re-add с пересчитанной парой; если оба
// флага гаснут — просто revoke.
//
// Для personal/department роли действуют в отделе-владельце (один dept). Для
// cross_department ACL могут жить в нескольких отделах (owner + recipient'ы с
// DeptGrant) — рисуем отдельную матрицу на каждый такой отдел.

function RoleAccessMatrix({
  credId,
  scope,
  ownerDeptId,
  actorDeptId,
  acls,
  deptGrants,
  loading,
  error,
  onChanged,
  onRetry,
}: {
  credId: string;
  scope: CredentialScope;
  ownerDeptId: string | null;
  actorDeptId: string | null;
  acls: RoleACL[];
  deptGrants: { recipient_dept_id: string }[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onRetry: () => void;
}) {
  // Отдел-владелец ACL: для personal/department это owner_dept_id (или, если
  // backend его не отдал, отдел актора). Для cross добавим recipient'ов.
  const baseDept = ownerDeptId ?? actorDeptId ?? null;

  const deptIds = useMemo(() => {
    const set = new Set<string>();
    if (baseDept) set.add(baseDept);
    if (scope === "cross_department") {
      for (const g of deptGrants) set.add(g.recipient_dept_id);
    }
    // Добиваем отделы, у которых уже есть ACL, но которых нет в наборе (на
    // случай, если grant был снят, а строки остались — их видно и можно убрать).
    for (const a of acls) set.add(a.dept_id);
    return Array.from(set);
  }, [acls, baseDept, deptGrants, scope]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
          <Grid3x3 className="w-3.5 h-3.5" /> Матрица доступа (RoleACL · {acls.length})
        </div>
      </div>

      {loading && <div className="text-xs text-dim">Загрузка…</div>}
      {error && (
        <div className="text-xs text-danger flex items-center gap-2">
          <span>{error}</span>
          <button className="btn btn-ghost btn-sm" onClick={onRetry}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !error && deptIds.length === 0 && (
        <div className="text-xs text-dim">
          Отдел-владелец не определён — матрицу построить нельзя.
        </div>
      )}

      {!loading &&
        !error &&
        deptIds.map((deptId) => (
          <DeptRoleMatrix
            key={deptId}
            credId={credId}
            deptId={deptId}
            acls={acls.filter((a) => a.dept_id === deptId)}
            onChanged={onChanged}
          />
        ))}
    </div>
  );
}

// Матрица одного отдела: тянет каталог его secret-ролей, рисует таблицу
// роли(столбцы) × read/write(строки) с тогглами.
function DeptRoleMatrix({
  credId,
  deptId,
  acls,
  onChanged,
}: {
  credId: string;
  deptId: string;
  acls: RoleACL[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const deptName = useDeptLabel(deptId);
  const rolesQ = useQuery(() => listServiceRoles(deptId, "secret"), [deptId], {
    enabled: !!deptId,
  });

  // ACL пер role_name выбранного отдела.
  const aclByRole = useMemo(() => {
    const m = new Map<string, RoleACL>();
    for (const a of acls) m.set(a.role_name, a);
    return m;
  }, [acls]);

  // Столбцы: системные роли каталога + кастомные + любые роли, на которые уже
  // висит ACL (даже если каталог их не вернул). Сортируем системные первыми.
  const roleNames = useMemo(() => {
    const set = new Set<string>(SECRET_ROLE_ORDER);
    for (const r of rolesQ.data ?? []) set.add(r.role_name);
    for (const a of acls) set.add(a.role_name);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [acls, rolesQ.data]);

  // Какие ячейки в полёте — ключ `role::right`.
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  const isAllowed = useCallback(
    (role: string, right: AclRight): boolean => {
      const acl = aclByRole.get(role);
      if (!acl) return false;
      return right === "read" ? acl.can_read : acl.can_write;
    },
    [aclByRole],
  );

  const toggle = useCallback(
    async (role: string, right: AclRight) => {
      const key = `${role}::${right}`;
      if (saving[key]) return;
      const existing = aclByRole.get(role);
      const curRead = existing?.can_read ?? false;
      const curWrite = existing?.can_write ?? false;
      const nextRead = right === "read" ? !curRead : curRead;
      const nextWrite = right === "write" ? !curWrite : curWrite;

      setSaving((m) => ({ ...m, [key]: true }));
      try {
        // Бэкенд не апсертит: сначала снимаем старый ACL (если был), потом
        // создаём новый с пересчитанной парой. Если оба флага сняты — только
        // revoke.
        if (existing) {
          await revokeRoleAcl(credId, existing.id);
        }
        if (nextRead || nextWrite) {
          await addRoleAcl(credId, {
            dept_id: deptId,
            role_name: role,
            can_read: nextRead,
            can_write: nextWrite,
          });
        }
        toast.success(
          `${deptName} / ${role}: ${nextRead ? "r" : "-"}${nextWrite ? "w" : "-"}`,
        );
        onChanged();
      } catch (e) {
        toast.error(apiErrMsg(e, "Не удалось изменить доступ"));
        // На частичном сбое (revoke прошёл, add упал) перечитываем правду.
        onChanged();
      } finally {
        setSaving((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
      }
    },
    [aclByRole, credId, deptId, deptName, onChanged, saving, toast],
  );

  return (
    <div className="mt-2 first:mt-0">
      <div className="text-[11px] text-dim mb-1" title={deptId}>
        отдел: <span className="mono">{deptName}</span>
      </div>
      {rolesQ.loading ? (
        <div className="text-xs text-dim">Загрузка ролей…</div>
      ) : (
        <div className="overflow-auto border border-token rounded">
          <table className="text-sm border-collapse w-full">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pt-2 px-3 sticky left-0 z-10 bg-[var(--bg-soft)]">
                  право
                </th>
                {roleNames.map((role) => (
                  <th
                    key={role}
                    className="pb-2 pt-2 px-2 mono font-normal text-center bg-[var(--bg-soft)] whitespace-nowrap"
                  >
                    {role}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ACL_RIGHTS.map((right) => (
                <tr key={right} className="border-t border-token">
                  <td className="py-2 px-3 mono text-xs sticky left-0 z-10 bg-[var(--bg-soft)]">
                    {right}
                  </td>
                  {roleNames.map((role) => {
                    const key = `${role}::${right}`;
                    const allowed = isAllowed(role, right);
                    const isSaving = !!saving[key];
                    const title = allowed
                      ? `Снять ${right} у роли ${role}`
                      : `Выдать ${right} роли ${role}`;
                    return (
                      <td key={role} className="py-1.5 px-2 text-center">
                        <AclToggle
                          allowed={allowed}
                          saving={isSaving}
                          title={title}
                          onClick={() => toggle(role, right)}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AclToggle({
  allowed,
  saving,
  title,
  onClick,
}: {
  allowed: boolean;
  saving: boolean;
  title: string;
  onClick: () => void;
}) {
  if (saving) {
    return (
      <span
        className="inline-flex items-center justify-center w-4 h-4 align-middle"
        title={title}
      >
        <Loader2 className="w-3.5 h-3.5 animate-spin text-dim" />
      </span>
    );
  }
  return (
    <input
      type="checkbox"
      className="w-4 h-4 align-middle accent-[var(--accent)] cursor-pointer"
      checked={allowed}
      title={title}
      onChange={onClick}
    />
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
