import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Search,
  Key,
  X,
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
import { useDeptLabel, useUserLabel } from "@/lib/labels";
import {
  getCredential,
  listCredentials,
} from "@/api/secret/credentials";
import { listRoleAcls, upsertRoleAcl } from "@/api/secret/roleAcls";
import { Dropdown } from "@/components/ui/Dropdown";
import { addUserAcl, listUserAcls, revokeUserAcl } from "@/api/secret/userAcls";
import { listServiceRoles } from "@/api/auth/service_roles";
import { listUsersByDepartment } from "@/api/auth/users";
import type {
  Credential,
  CredentialScope,
  RoleACL,
} from "@/api/secret/types";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";

// Системные роли каталога secret_service в порядке отображения; кастомные роли
// отдела идут после по алфавиту.
const SECRET_ROLE_ORDER = ["guest", "admin"];

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
  service: "Сервисные учётные данные",
};

/**
 * Per-credential управление доступом к секретам отдела. Слева — список кред
 * (поиск + фильтры scope/status), справа — доступ выбранной кред'ы. ACL
 * (RoleACL роль×право + UserACL по пользователям) настраивается только для
 * personal-кред; для department / cross_department доступ задаётся сервис-
 * ролями отдела.
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
          Выберите credential слева, чтобы настроить доступ. Per-credential ACL
          настраивается только для <b>personal</b>-кред: матрица ролей (столбцы —
          роли отдела, строки — права <b>read</b> / <b>write</b>) и <b>UserACL</b>{" "}
          — доступ конкретному пользователю. Доступ к department /
          cross_department и сервисным учётным данным определяется сервис-ролями отдела.
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
            <Dropdown
              mode="single"
              placeholder="все scope"
              options={[
                { value: "personal", label: "personal" },
                { value: "department", label: "department" },
                { value: "cross_department", label: "cross_department" },
                { value: "service", label: SCOPE_LABEL.service },
              ]}
              value={filterScope}
              onChange={setFilterScope}
            />
            <Dropdown
              mode="single"
              placeholder="все статусы"
              options={[
                { value: "active", label: "active" },
                { value: "blocked", label: "blocked" },
              ]}
              value={filterStatus}
              onChange={setFilterStatus}
            />
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
                <Button variant="ghost" size="sm"
                  className="self-start"
                  onClick={() => listQ.refetch()}
                >
                  Повторить
                </Button>
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
            <Button variant="ghost"
              className="w-full text-xs"
              onClick={handleLoadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Загрузка…" : "Загрузить ещё"}
            </Button>
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
        <Badge kind={blocked ? "danger" : "ok"}>
          {cred.status}
        </Badge>
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
  const isPersonal = cred?.scope === "personal";

  // ACL-управление (RoleACL + UserACL) показываем только для personal-кред.
  // Для department / cross_department доступ определяется сервис-ролями отдела —
  // per-credential ACL там не выдаём.
  const aclQ = useQuery(() => listRoleAcls(credId), [credId], {
    enabled: isPersonal,
  });
  const userAclQ = useQuery(() => listUserAcls(credId), [credId], {
    enabled: isPersonal,
  });

  const confirm = useConfirm();
  const [acting, setActing] = useState(false);
  const [addingUserAcl, setAddingUserAcl] = useState(false);

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
          <Button variant="ghost" size="sm"
            className="self-start"
            onClick={() => credQ.refetch()}
          >
            Повторить
          </Button>
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
          <Badge kind={cred.status === "blocked" ? "danger" : "ok"}>
            {cred.status}
          </Badge>
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

      {isPersonal ? (
        <>
          {/* RoleACL — матрица: столбцы=роли, строки=read/write */}
          <RoleAccessMatrix
            credId={credId}
            actorDeptId={actorDeptId}
            ownerDeptId={cred.owner_dept_id}
            acls={aclQ.data?.items ?? []}
            loading={aclQ.loading}
            error={
              aclQ.error ? apiErrMsg(aclQ.error, "RoleACL не загрузился") : null
            }
            onChanged={() => aclQ.refetch()}
            onRetry={() => aclQ.refetch()}
          />

          {/* UserACL — доступ конкретному пользователю */}
          <AccessCard
            title={`UserACL (${userAclQ.data?.items.length ?? 0})`}
            icon={<UserPlus className="w-3.5 h-3.5" />}
            addLabel="Выдать пользователю"
            onAdd={() => setAddingUserAcl(true)}
            loading={userAclQ.loading}
            error={
              userAclQ.error
                ? apiErrMsg(userAclQ.error, "UserACL не загрузился")
                : null
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
        </>
      ) : (
        <div className="card">
          <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2 mb-2">
            <Lock className="w-3.5 h-3.5" /> Доступ
          </div>
          <p className="text-xs text-dim leading-relaxed">
            Доступ к{" "}
            <span className="mono">{SCOPE_LABEL[cred.scope] ?? cred.scope}</span>
            -кред'е определяется сервис-ролями отдела. Per-credential ACL
            настраивается только для <span className="mono">personal</span>-кред.
          </p>
        </div>
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
// Бэкенд хранит RoleACL пер (dept, role) с парой флагов can_read/can_write.
// Смена ячейки — атомарный upsert (PUT): один вызов задаёт пересчитанную пару
// для (dept, role); если оба флага гаснут — backend снимает строку.

function RoleAccessMatrix({
  credId,
  ownerDeptId,
  actorDeptId,
  acls,
  loading,
  error,
  onChanged,
  onRetry,
}: {
  credId: string;
  ownerDeptId: string | null;
  actorDeptId: string | null;
  acls: RoleACL[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onRetry: () => void;
}) {
  // Отдел-владелец ACL: owner_dept_id, либо (если backend его не отдал) отдел
  // актора.
  const baseDept = ownerDeptId ?? actorDeptId ?? null;

  const deptIds = useMemo(() => {
    const set = new Set<string>();
    if (baseDept) set.add(baseDept);
    // Добиваем отделы, у которых уже есть ACL, но которых нет в наборе.
    for (const a of acls) set.add(a.dept_id);
    return Array.from(set);
  }, [acls, baseDept]);

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
          <Button variant="ghost" size="sm" onClick={onRetry}>
            Повторить
          </Button>
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
        // Атомарный upsert: один вызов задаёт пересчитанную пару флагов; при
        // обоих false backend снимает строку.
        await upsertRoleAcl(credId, {
          dept_id: deptId,
          role_name: role,
          can_read: nextRead,
          can_write: nextWrite,
        });
        toast.success(
          `${deptName} / ${role}: ${nextRead ? "r" : "-"}${nextWrite ? "w" : "-"}`,
        );
        onChanged();
      } catch (e) {
        toast.error(apiErrMsg(e, "Не удалось изменить доступ"));
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
    <Checkbox
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
        <Button variant="ghost"
          className="text-xs flex items-center gap-1"
          onClick={onAdd}
        >
          {icon} {addLabel}
        </Button>
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
        <Button variant="ghost"
          className="p-1"
          title="Снять UserACL"
          disabled={acting}
          onClick={onRevoke}
        >
          <X className="w-3.5 h-3.5" />
        </Button>
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
          <Button variant="ghost"
            className="p-1"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>
        {children}
      </div>
    </div>
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

  const [input, setInput] = useState("");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const valid = !!usersQ.data?.items.some((user) => user.id === input) && (canRead || canWrite);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const userId = input;
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
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <Dropdown mode="single" label="Пользователь" placeholder="Выберите пользователя" value={input} onChange={setInput}
            options={(usersQ.data?.items ?? []).map((user) => ({ value: user.id, label: user.username }))} />
        </label>
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canRead}
              onChange={(e) => setCanRead(e.target.checked)}
            />
            <span className="text-dim text-xs">can_read</span>
          </label>
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canWrite}
              onChange={(e) => setCanWrite(e.target.checked)}
            />
            <span className="text-dim text-xs">can_write</span>
          </label>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <Button variant="primary"
            type="submit"
            disabled={submitting || !valid}
          >
            {submitting ? "Выдаём…" : "Выдать"}
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
