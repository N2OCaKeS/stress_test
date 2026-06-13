import { useMemo, useState } from "react";
import {
  Users as UsersIcon,
  User,
  UserCog,
  ChevronDown,
  ChevronRight,
  Copy,
  RefreshCw,
  AlertTriangle,
} from "lucide-react";
import {
  generateInitialPassword,
  isValidEmail,
  validatePassword,
} from "@/lib/passwordPolicy";
import { usePersona } from "@/contexts/PersonaContext";
import { DEPTS as MOCK_DEPTS, USERS as MOCK_USERS, type MockUser } from "@/mocks/auth";
import { InlineEditor, FormRow, useInlineState } from "./_inline";
import { UserBackendView } from "./_servicesUsersView";
import {
  createUser,
  isUserBanned,
  listUsers,
  normalizeUserStatus,
  updateUser,
} from "@/api/auth/users";
import { listDepartments } from "@/api/auth/departments";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useTimeoutRef } from "@/lib/useTimeoutRef";
import type {
  Department,
  PlatformRole,
  User as ApiUser,
  UserStatus,
} from "@/api/auth/types";

/**
 * Persona view used inside the inline editor. We render both real API users
 * and mock users through this shape so the same row/detail components work
 * in either mode.
 */
type UiUser = {
  id: string;
  username: string;
  email: string | null;
  dept_id: string | null;
  dept_name?: string | null;
  platform_role: string | null;
  status: string;
  is_locked?: boolean;
  is_banned?: boolean;
  must_change_password?: boolean;
  created_by?: string | null;
  created_at?: string;
  last_login?: string;
  mfa_enabled?: boolean;
};

function adaptApi(u: ApiUser): UiUser {
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    dept_id: u.department_id,
    dept_name: u.department_name ?? null,
    platform_role: u.platform_role,
    status: normalizeUserStatus(u.status),
    // UserResponse не несёт is_banned — выводим из status (banned).
    is_banned: isUserBanned(u),
    must_change_password: u.must_change_password,
    created_at: u.created_at,
    last_login: u.updated_at ?? u.created_at,
  };
}

function adaptMock(u: MockUser): UiUser {
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    dept_id: u.dept_id,
    platform_role: u.platform_role,
    status: u.status,
    created_at: u.last_login,
    last_login: u.last_login,
    created_by: u.created_by ?? undefined,
    mfa_enabled: u.mfa_enabled,
  };
}

type SortKey = "username_asc" | "username_desc" | "status" | "created_desc" | "created_asc";
type GroupKey = "none" | "department" | "platform_role" | "status";
type StatusFilter = "" | "active" | "blocked" | "banned";

const STATUS_ORDER: Record<string, number> = {
  active: 0,
  blocked: 1,
  banned: 2,
};

function sortUsers(items: UiUser[], key: SortKey): UiUser[] {
  const arr = [...items];
  switch (key) {
    case "username_asc":
      return arr.sort((a, b) => a.username.localeCompare(b.username));
    case "username_desc":
      return arr.sort((a, b) => b.username.localeCompare(a.username));
    case "status":
      return arr.sort((a, b) => {
        const av = STATUS_ORDER[a.status] ?? 99;
        const bv = STATUS_ORDER[b.status] ?? 99;
        if (av !== bv) return av - bv;
        return a.username.localeCompare(b.username);
      });
    case "created_desc":
      return arr.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    case "created_asc":
      return arr.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  }
}

function groupKeyOf(u: UiUser, by: GroupKey, deptLabels: Map<string, string>): string {
  switch (by) {
    case "department":
      if (!u.dept_id) return "—";
      return u.dept_name ?? deptLabels.get(u.dept_id) ?? u.dept_id;
    case "platform_role":
      return u.platform_role ?? "обычные";
    case "status":
      return u.status;
    default:
      return "";
  }
}

export function ServicesUsers() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "dep_admin";

  const [limit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [sortKey, setSortKey] = useState<SortKey>("username_asc");
  const [groupBy, setGroupBy] = useState<GroupKey>("none");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  // Бампается при каждом refetchAll — пробрасывается в правую панель, чтобы её
  // user/perms/groups перечитались после правок из формы (смена отдела/роли/
  // статуса в UserForm иначе оставляет осиротевшие service-роли в детали).
  const [detailSignal, setDetailSignal] = useState(0);

  // Departments — used by forms and labels.
  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  const depts: Array<Pick<Department, "id" | "name">> = mockMode
    ? MOCK_DEPTS.map((d) => ({ id: d.id, name: d.name }))
    : (deptsQ.data ?? []).map((d) => ({
        id: d.id,
        name: d.name,
      }));

  // List users. include_banned всегда true — фильтрация по статусу делается
  // отдельным dropdown'ом, при «все» backend должен отдать всех (включая banned).
  const usersQ = useQuery(
    () =>
      listUsers({
        limit,
        offset,
        include_banned: true,
        status: statusFilter || undefined,
      }),
    [limit, offset, statusFilter],
    { enabled: !mockMode },
  );

  const allItems: UiUser[] = useMemo(() => {
    if (mockMode) {
      const arr = MOCK_USERS.map(adaptMock);
      // dep_admin sees only own dept; account_admin / logging_* see all.
      return persona.platform_role === "dep_admin" && persona.dept_id
        ? arr.filter((u) => u.dept_id === persona.dept_id)
        : arr;
    }
    return (usersQ.data?.items ?? []).map(adaptApi);
  }, [mockMode, persona.platform_role, persona.dept_id, usersQ.data]);

  // Client-side status filter on top of API-level filter — mock-режим тоже
  // должен реагировать на dropdown.
  const filtered = useMemo(() => {
    if (!statusFilter) return allItems;
    return allItems.filter((u) => u.status === statusFilter);
  }, [allItems, statusFilter]);

  const sorted = useMemo(() => sortUsers(filtered, sortKey), [filtered, sortKey]);

  const deptLabels = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of depts) m.set(d.id, d.name);
    return m;
  }, [depts]);

  // Группировка: вычисляем порядок групп и для каждого элемента — является ли он
  // первым в своей группе (тогда renderRow вставит заголовок).
  const { items, firstInGroup, groupSizes } = useMemo(() => {
    if (groupBy === "none") {
      return {
        items: sorted,
        firstInGroup: new Map<string, string>(),
        groupSizes: new Map<string, number>(),
      };
    }
    const buckets = new Map<string, UiUser[]>();
    const order: string[] = [];
    for (const u of sorted) {
      const k = groupKeyOf(u, groupBy, deptLabels);
      if (!buckets.has(k)) {
        buckets.set(k, []);
        order.push(k);
      }
      buckets.get(k)!.push(u);
    }
    // Стабильный порядок ключей в group=status и platform_role.
    if (groupBy === "status") {
      order.sort((a, b) => (STATUS_ORDER[a] ?? 99) - (STATUS_ORDER[b] ?? 99));
    } else {
      order.sort((a, b) => a.localeCompare(b));
    }
    const flat: UiUser[] = [];
    const first = new Map<string, string>(); // user.id -> group key
    const sizes = new Map<string, number>();
    for (const k of order) {
      const arr = buckets.get(k) ?? [];
      sizes.set(k, arr.length);
      if (arr.length === 0) continue;
      // Первый item группы остаётся в flat даже если группа свёрнута —
      // renderRow покажет только header, а body спрячет. Без этого
      // свёрнутая группа полностью пропадала из списка.
      first.set(arr[0].id, k);
      if (collapsed[k]) {
        flat.push(arr[0]);
        continue;
      }
      for (const u of arr) flat.push(u);
    }
    return { items: flat, firstInGroup: first, groupSizes: sizes };
  }, [sorted, groupBy, deptLabels, collapsed]);

  const total = mockMode ? allItems.length : usersQ.data?.total ?? allItems.length;
  // Сортировка и группировка работают только по загруженной странице. Когда
  // всего пользователей больше, чем влезло в текущую страницу, «отсортировано
  // по имени» — это порядок лишь среди показанных строк, а не глобальный.
  const sortScopeTruncated = !mockMode && total > allItems.length;

  const refetchAll = () => {
    usersQ.refetch();
    setDetailSignal((n) => n + 1);
  };

  function toggleGroup(k: string) {
    setCollapsed((cur) => ({ ...cur, [k]: !cur[k] }));
  }

  return (
    <InlineEditor
      title="Пользователи · auth_service"
      icon={UsersIcon}
      hint="CRUD аккаунтов, reset password, lockout/ban, revoke sessions"
      items={items}
      loading={!mockMode && usersQ.loading}
      error={!mockMode && usersQ.error ? usersQ.error.message : null}
      onRetry={() => usersQ.refetch()}
      getId={(u) => u.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      emptyHint="Выберите пользователя слева, чтобы увидеть детали и действия."
      listHeader={
        <div className="flex flex-col gap-2">
          <div className="text-[11px] text-dim">
            {mockMode
              ? "mock-режим — данные из src/mocks/auth.ts"
              : usersQ.loading && items.length === 0
                ? "загрузка…"
                : `показано ${items.length} из ${total}`}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <label className="flex items-center gap-1">
              <span className="text-dim">сорт.</span>
              <select
                className="input input-sm"
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as SortKey)}
              >
                <option value="username_asc">username ↑</option>
                <option value="username_desc">username ↓</option>
                <option value="status">статус</option>
                <option value="created_desc">создан ↓</option>
                <option value="created_asc">создан ↑</option>
              </select>
            </label>
            <label className="flex items-center gap-1">
              <span className="text-dim">группа</span>
              <select
                className="input input-sm"
                value={groupBy}
                onChange={(e) => setGroupBy(e.target.value as GroupKey)}
              >
                <option value="none">—</option>
                <option value="department">по отделу</option>
                <option value="platform_role">по platform_role</option>
                <option value="status">по статусу</option>
              </select>
            </label>
            <label className="flex items-center gap-1">
              <span className="text-dim">статус</span>
              <select
                className="input input-sm"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value as StatusFilter);
                  setOffset(0);
                }}
              >
                <option value="">все</option>
                <option value="active">только active</option>
                <option value="blocked">только blocked</option>
                <option value="banned">только banned</option>
              </select>
            </label>
          </div>
          {sortScopeTruncated && (
            <div className="alert-warn text-[11px]" role="status">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
              <span>
                Сортировка и группировка применены к загруженной странице
                ({allItems.length} из {total}). Чтобы упорядочить всех —
                сузьте фильтр или листайте страницы.
              </span>
            </div>
          )}
          {!mockMode && (
            <div className="flex items-center gap-2 text-[11px]">
              <div className="ml-auto flex items-center gap-1">
                <button
                  className="btn btn-sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                >
                  ←
                </button>
                <span>
                  {offset}-{offset + allItems.length}
                </span>
                <button
                  className="btn btn-sm"
                  disabled={offset + allItems.length >= total}
                  onClick={() => setOffset(offset + limit)}
                >
                  →
                </button>
              </div>
            </div>
          )}
        </div>
      }
      renderRow={({ item, active, onSelect }) => {
        const groupHeaderKey = firstInGroup.get(item.id);
        const isGroupCollapsed =
          groupHeaderKey !== undefined && collapsed[groupHeaderKey];
        return (
          <>
            {groupHeaderKey !== undefined && (
              <button
                className="w-full text-left px-2 py-1 text-[11px] uppercase tracking-wider text-dim flex items-center gap-1 hover:text-accent"
                onClick={() => toggleGroup(groupHeaderKey)}
              >
                {collapsed[groupHeaderKey] ? (
                  <ChevronRight className="w-3 h-3" />
                ) : (
                  <ChevronDown className="w-3 h-3" />
                )}
                <span>{groupHeaderKey}</span>
                <span className="text-dim">· {groupSizes.get(groupHeaderKey) ?? 0}</span>
              </button>
            )}
            {isGroupCollapsed ? null : (
            <div
              className={`cred-row text-left ${active ? "active" : ""} cursor-pointer`}
              onClick={onSelect}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect();
                }
              }}
            >
              <div className="flex items-center gap-2">
                {item.platform_role ? (
                  <UserCog className="w-4 h-4 text-accent" />
                ) : (
                  <User className="w-4 h-4 text-dim" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate flex items-center gap-2">
                    <span>{item.username}</span>
                    {item.platform_role && (
                      <span className="badge badge-accent">{item.platform_role}</span>
                    )}
                    {item.must_change_password && (
                      <span className="badge badge-warn" title="must_change_password">
                        pwd!
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-dim truncate">
                    {item.dept_name ?? item.dept_id ?? "platform"} · {item.email ?? "—"}
                  </div>
                </div>
                <span
                  className={`badge badge-${item.status === "active" ? "ok" : item.status === "blocked" ? "warn" : "danger"}`}
                >
                  {item.status}
                </span>
                {/* per-row Ban/Unban убран — банить и удалять только из
                    workzone справа после двойного подтверждения, чтобы не
                    отстреливать юзеров случайным кликом в списке. */}
              </div>
            </div>
            )}
          </>
        );
      }}
      renderDetail={(u, { editing, onClose }) => {
        if (editing)
          return (
            <UserForm
              initial={u}
              depts={depts}
              canEdit={canEdit}
              mockMode={mockMode}
              onDone={() => {
                refetchAll();
                onClose();
              }}
              mode="edit"
            />
          );
        return (
          <ServicesUserDetailPane
            user={u}
            canEdit={canEdit}
            refetchAll={refetchAll}
            detailSignal={detailSignal}
          />
        );
      }}
      renderCreate={
        canEdit
          ? (onClose) => (
              <UserForm
                depts={depts}
                canEdit
                mockMode={mockMode}
                onDone={() => {
                  refetchAll();
                  onClose();
                }}
                mode="new"
              />
            )
          : undefined
      }
    />
  );
}

// ---------------------------------------------------------------------------
// View pane
// ---------------------------------------------------------------------------

function ServicesUserDetailPane({
  user,
  canEdit,
  refetchAll,
  detailSignal,
}: {
  user: UiUser;
  canEdit: boolean;
  refetchAll: () => void;
  detailSignal: number;
}) {
  const { startEdit } = useInlineState();
  return (
    <UserBackendView
      userId={user.id}
      fallbackUsername={user.username}
      fallbackDeptId={user.dept_id}
      refetchListSignal={detailSignal}
      onChanged={refetchAll}
      onStartEdit={canEdit ? () => startEdit(user.id) : undefined}
    />
  );
}

// ---------------------------------------------------------------------------
// Form pane (create / edit)
// ---------------------------------------------------------------------------

function UserForm({
  initial,
  depts,
  canEdit,
  mockMode,
  onDone,
  mode,
}: {
  initial?: UiUser;
  depts: Array<{ id: string; name: string }>;
  canEdit: boolean;
  mockMode: boolean;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [username, setUsername] = useState(initial?.username ?? "");
  const [password, setPassword] = useState(() =>
    mode === "new" ? generateInitialPassword() : "",
  );
  const [email, setEmail] = useState(initial?.email ?? "");
  const [dept, setDept] = useState(initial?.dept_id ?? "");
  const [platformRole, setPlatformRole] = useState<string>(
    initial?.platform_role ?? "",
  );
  const [status, setStatus] = useState<string>(
    (initial?.status ?? "active").toString(),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copyHint, setCopyHint] = useState(false);
  const setCopyHintTimeout = useTimeoutRef();

  // Когда выбран отдел, доступна только department_admin. Платформенные роли
  // (account_admin / loging_admin / loging_reader) требуют отсутствия dept,
  // и backend это валидирует на уровне сервиса.
  const availableRoles = useMemo(() => {
    if (dept) return [{ value: "department_admin", label: "department_admin" }];
    return [
      { value: "account_admin", label: "account_admin" },
      { value: "loging_admin", label: "loging_admin" },
    ];
  }, [dept]);

  if (platformRole && !availableRoles.some((r) => r.value === platformRole)) {
    setPlatformRole("");
  }

  const passwordError = mode === "new" ? validatePassword(password) : null;
  const emailError =
    email && !isValidEmail(email) ? "Неверный формат email" : null;

  function copyPassword() {
    navigator.clipboard?.writeText(password).catch(() => {});
    setCopyHint(true);
    setCopyHintTimeout(() => setCopyHint(false), 1500);
  }

  async function submit() {
    if (mockMode) {
      onDone();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (mode === "new") {
        // must_change_password=true прямо в create-body — временный пароль не
        // должен остаться постоянным (форма обещает «юзер сменит при первом
        // входе»). Передаём атомарно, без отдельного вызова.
        const body = {
          username,
          password,
          email: email || undefined,
          department_id: dept || null,
          platform_role: (platformRole || null) as PlatformRole,
          must_change_password: true,
        };
        await createUser(body);
      } else if (initial) {
        const body: {
          email?: string;
          department_id?: string | null;
          platform_role?: PlatformRole;
          status?: UserStatus;
        } = {};
        if (email !== initial.email) body.email = email;
        if (dept !== (initial.dept_id ?? ""))
          body.department_id = dept || null;
        if (platformRole !== (initial.platform_role ?? ""))
          body.platform_role = (platformRole || null) as PlatformRole;
        const newStatus = normalizeUserStatus(status);
        if (newStatus !== normalizeUserStatus(initial.status))
          body.status = newStatus;
        await updateUser(initial.id, body);
      }
      onDone();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <UserCog className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый пользователь" : `Edit · ${initial?.username}`}
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="username">
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={mode === "edit"}
          />
        </FormRow>
        <FormRow label="email">
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
          />
          {emailError && (
            <div className="text-[11px] text-danger mt-1">{emailError}</div>
          )}
        </FormRow>
        {mode === "new" && (
          <FormRow
            label="пароль (виден, юзер сменит при первом входе)"
            hint="минимум 12 символов, буквы и цифры"
          >
            <div className="flex gap-2 items-center">
              <input
                className="input mono flex-1"
                type="text"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="btn"
                title="Сгенерировать новый"
                onClick={() => setPassword(generateInitialPassword())}
                disabled={busy}
              >
                <RefreshCw className="w-4 h-4" />
              </button>
              <button
                type="button"
                className="btn"
                title="Скопировать"
                onClick={copyPassword}
                disabled={busy}
              >
                <Copy className="w-4 h-4" />
              </button>
            </div>
            <div className="text-[11px] mt-1">
              {copyHint ? (
                <span className="text-ok">Скопировано</span>
              ) : passwordError ? (
                <span className="text-danger">{passwordError}</span>
              ) : (
                <span className="text-dim">Пароль соответствует политике</span>
              )}
            </div>
          </FormRow>
        )}
        <FormRow label="dept">
          <select
            className="input"
            value={dept ?? ""}
            onChange={(e) => setDept(e.target.value)}
          >
            <option value="">— (платформенный)</option>
            {depts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="platform_role"
          hint={
            dept
              ? "С отделом доступна только department_admin"
              : "Платформенные роли — без отдела"
          }
        >
          <select
            className="input"
            value={platformRole}
            onChange={(e) => setPlatformRole(e.target.value)}
          >
            <option value="">— (обычный user)</option>
            {availableRoles.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </FormRow>
        {mode === "edit" && (
          <FormRow label="status">
            <select
              className="input"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="active">active</option>
              <option value="blocked">blocked</option>
              <option value="banned">banned</option>
            </select>
          </FormRow>
        )}
      </div>
      {err && <div className="alert-danger mt-3">{err}</div>}
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone} disabled={busy}>
          Отмена
        </button>
        {canEdit && (
          <button
            className="btn btn-primary"
            onClick={submit}
            disabled={
              busy ||
              !!emailError ||
              (mode === "new" &&
                (!username || !password || !!passwordError))
            }
          >
            {busy ? "..." : mode === "new" ? "Создать" : "Сохранить"}
          </button>
        )}
      </div>
      {mockMode && (
        <div className="mt-3 text-[11px] text-dim">
          mock-режим: сохранение в реальный backend не выполняется.
        </div>
      )}
    </div>
  );
}
