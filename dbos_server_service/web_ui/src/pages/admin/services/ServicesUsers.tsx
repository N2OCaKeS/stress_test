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
  Search,
  Plus,
  X,
  ArrowLeft,
} from "lucide-react";
import {
  generateInitialPassword,
  isValidEmail,
  PASSWORD_POLICY_MESSAGE,
  validatePassword,
} from "@/lib/passwordPolicy";
import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin } from "@/lib/rbac";
import { useDeptLabel } from "@/lib/labels";
import { DEPTS as MOCK_DEPTS, USERS as MOCK_USERS, type MockUser } from "@/mocks/auth";
import { FormRow, useInlineState } from "./_inline";
import { UserBackendView } from "./_servicesUsersView";
import {
  createUser,
  isUserBanned,
  listUsers,
  listUsersByDepartment,
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
  // account_admin видит всех через listUsers (cross-dept). Все остальные
  // админы (dep_admin, носитель сервис-admin с отделом) ограничены своим
  // отделом — listUsers им вернёт 403, поэтому зовём listUsersByDepartment.
  const platformWide = isPlatformWideAdmin(persona);
  const scopeDeptId = !platformWide ? persona.dept_id : null;

  const [limit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [search, setSearch] = useState("");
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
    () => {
      const params = {
        limit,
        offset,
        include_banned: true,
        status: statusFilter || undefined,
      };
      return scopeDeptId
        ? listUsersByDepartment(scopeDeptId, params)
        : listUsers(params);
    },
    [limit, offset, statusFilter, scopeDeptId],
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

  // Client-side status filter + текстовый поиск по username/email поверх
  // API-фильтра. mock-режим реагирует на оба контрола; в live-режиме поиск
  // тоже клиентский (по загруженной странице) — backend не даёт q-параметра.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let arr = allItems;
    if (statusFilter) arr = arr.filter((u) => u.status === statusFilter);
    if (q) {
      arr = arr.filter(
        (u) =>
          u.username.toLowerCase().includes(q) ||
          (u.email ?? "").toLowerCase().includes(q),
      );
    }
    return arr;
  }, [allItems, statusFilter, search]);

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

  // Текущий выбор / режим читаем из URL (?id / ?action) — те же хелперы, что у
  // прочих admin-разделов, чтобы deep-link на пользователя и RBAC-гейт «руками
  // дописанного ?action=edit» работали как раньше.
  const { id: selectedId, mode: rawMode, select, startCreate, startEdit, close } =
    useInlineState();
  // RBAC-гейт: read-only персона не должна попасть в create/edit даже через
  // прямой URL. Совпадает с логикой InlineEditor.
  const gated = !canEdit && (rawMode === "new" || rawMode === "edit");
  const mode = gated ? "list" : rawMode;
  const selected = useMemo(
    () => (selectedId ? items.find((u) => u.id === selectedId) : undefined),
    [selectedId, items],
  );

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <UsersIcon className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Пользователи · auth_service
          </h1>
          <div className="text-xs text-dim">
            CRUD аккаунтов, reset password, lockout/ban, revoke sessions
          </div>
        </div>
        <div className="text-xs text-dim mr-2">
          {!mockMode && usersQ.loading && items.length === 0
            ? "…"
            : `${items.length} записей`}
        </div>
        {canEdit && (
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={startCreate}
          >
            <Plus className="w-4 h-4" /> Создать
          </button>
        )}
      </div>

      {!canEdit && (
        <div className="readonly-bar shrink-0">
          <span className="ro-label">read-only</span>
          <span>Просмотр без права изменения</span>
        </div>
      )}

      {/* Body — master (list + filters) | detail (workzone) */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {/* Master panel */}
        <aside className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
          {/* Filters */}
          <div className="px-3 py-2 border-b border-token shrink-0 flex flex-col gap-2">
            <div className="input-wrap">
              <input
                type="text"
                className="input pr-8"
                placeholder="Поиск по username / email…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search ? (
                <button
                  className="input-icon"
                  title="Очистить"
                  onClick={() => setSearch("")}
                >
                  <X className="w-4 h-4" />
                </button>
              ) : (
                <Search className="input-icon w-4 h-4" />
              )}
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
            <div className="text-[11px] text-dim">
              {mockMode
                ? "mock-режим — данные из src/mocks/auth.ts"
                : usersQ.loading && items.length === 0
                  ? "загрузка…"
                  : `показано ${items.length} из ${total}`}
            </div>
            {sortScopeTruncated && (
              <div className="alert-warn text-[11px]" role="status">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                <span>
                  Сортировка, группировка и поиск применены к загруженной
                  странице ({allItems.length} из {total}). Чтобы охватить всех —
                  сузьте фильтр по статусу или листайте страницы.
                </span>
              </div>
            )}
          </div>

          {/* Rows */}
          <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-0.5">
            {!mockMode && usersQ.loading && items.length === 0 ? (
              <div className="text-xs text-dim px-3 py-6 text-center">
                Загрузка…
              </div>
            ) : !mockMode && usersQ.error ? (
              <div className="alert-danger text-xs m-1 flex items-center justify-between gap-2">
                <span>{usersQ.error.message}</span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => usersQ.refetch()}
                >
                  Повторить
                </button>
              </div>
            ) : items.length === 0 ? (
              <div className="text-xs text-dim px-3 py-6 text-center">
                {search || statusFilter
                  ? "Нет пользователей под фильтр."
                  : "Список пуст."}
              </div>
            ) : null}
            {items.map((item) => {
              const groupHeaderKey = firstInGroup.get(item.id);
              const isGroupCollapsed =
                groupHeaderKey !== undefined && collapsed[groupHeaderKey];
              const active = item.id === selectedId;
              return (
                <div key={item.id}>
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
                      <span className="text-dim">
                        · {groupSizes.get(groupHeaderKey) ?? 0}
                      </span>
                    </button>
                  )}
                  {!isGroupCollapsed && (
                    <div
                      className={`cred-row text-left ${active ? "active" : ""} cursor-pointer`}
                      onClick={() => select(item.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          select(item.id);
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
                              <span className="badge badge-accent">
                                {item.platform_role}
                              </span>
                            )}
                            {item.must_change_password && (
                              <span
                                className="badge badge-warn"
                                title="must_change_password"
                              >
                                pwd!
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-dim truncate">
                            {item.dept_name ?? item.dept_id ?? "platform"} ·{" "}
                            {item.email ?? "—"}
                          </div>
                        </div>
                        <span
                          className={`badge badge-${item.status === "active" ? "ok" : item.status === "blocked" ? "warn" : "danger"}`}
                        >
                          {item.status}
                        </span>
                        {/* per-row Ban/Unban нет — деструктив только из правой
                            панели после двойного подтверждения, чтобы не
                            отстреливать юзеров случайным кликом по списку. */}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Pagination footer */}
          {!mockMode && (
            <div className="px-3 py-2 border-t border-token shrink-0 flex items-center gap-2 text-[11px]">
              <span className="text-dim">
                {offset}-{offset + allItems.length} из {total}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <button
                  className="btn btn-sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                >
                  ←
                </button>
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
        </aside>

        {/* Workzone — детально выбранного юзера / форма создания / форма правки */}
        <section className="flex-1 min-w-0 min-h-0 overflow-y-auto">
          {mode === "new" && canEdit ? (
            <div className="p-5 flex flex-col gap-4 min-h-full">
              <div className="flex items-center gap-2 shrink-0">
                <button
                  className="btn btn-ghost flex items-center gap-1"
                  onClick={close}
                >
                  <ArrowLeft className="w-4 h-4" /> Назад
                </button>
                <div className="text-sm text-dim">Новый пользователь</div>
              </div>
              <UserForm
                depts={depts}
                canEdit
                lockedDeptId={scopeDeptId}
                mockMode={mockMode}
                onDone={() => {
                  refetchAll();
                  close();
                }}
                mode="new"
              />
            </div>
          ) : selected && mode === "edit" && canEdit ? (
            <div className="p-5 flex flex-col gap-4 min-h-full">
              <div className="flex items-center gap-2 shrink-0">
                <button
                  className="btn btn-ghost flex items-center gap-1"
                  onClick={() => select(selected.id)}
                >
                  <ArrowLeft className="w-4 h-4" /> К пользователю
                </button>
                <div className="text-sm text-dim truncate">
                  Редактирование · {selected.username}
                </div>
              </div>
              <UserForm
                initial={selected}
                depts={depts}
                canEdit={canEdit}
                lockedDeptId={scopeDeptId}
                mockMode={mockMode}
                onDone={() => {
                  refetchAll();
                  select(selected.id);
                }}
                mode="edit"
              />
            </div>
          ) : selected ? (
            <div className="p-5 min-h-full">
              <ServicesUserDetailPane
                key={selected.id}
                user={selected}
                canEdit={canEdit}
                refetchAll={refetchAll}
                detailSignal={detailSignal}
                onEdit={() => startEdit(selected.id)}
              />
            </div>
          ) : (
            <div className="h-full flex items-center justify-center p-8">
              <div className="empty-card max-w-md">
                <UsersIcon className="w-10 h-10 mx-auto text-dim mb-3" />
                <div className="text-sm text-dim">
                  Выберите пользователя слева, чтобы увидеть профиль, роли,
                  сессии и действия.
                </div>
                {canEdit && (
                  <button
                    className="btn btn-primary mt-4 inline-flex items-center gap-1"
                    onClick={startCreate}
                  >
                    <Plus className="w-4 h-4" /> Создать
                  </button>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
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
  onEdit,
}: {
  user: UiUser;
  canEdit: boolean;
  refetchAll: () => void;
  detailSignal: number;
  onEdit: () => void;
}) {
  return (
    <UserBackendView
      userId={user.id}
      fallbackUsername={user.username}
      fallbackDeptId={user.dept_id}
      refetchListSignal={detailSignal}
      onChanged={refetchAll}
      onStartEdit={canEdit ? onEdit : undefined}
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
  lockedDeptId,
  mockMode,
  onDone,
  mode,
}: {
  initial?: UiUser;
  depts: Array<{ id: string; name: string }>;
  canEdit: boolean;
  /**
   * Если задан — отдел залочен на это значение (dep_admin создаёт/правит
   * только в своём отделе, backend иначе отбивает). Платформенный админ
   * получает null и свободный выбор отдела.
   */
  lockedDeptId: string | null;
  mockMode: boolean;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [username, setUsername] = useState(initial?.username ?? "");
  const [password, setPassword] = useState(() =>
    mode === "new" ? generateInitialPassword() : "",
  );
  const [email, setEmail] = useState(initial?.email ?? "");
  const [dept, setDept] = useState(
    lockedDeptId ?? initial?.dept_id ?? "",
  );
  const lockedDeptName = useDeptLabel(lockedDeptId);
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

  // С отделом доступны dept-роли: department_admin и dept-scoped аудит-читатель
  // (loging_reader_dep). Платформенные роли без отдела (account_admin /
  // loging_admin) — только при пустом dept; backend валидирует это на сервисе.
  // dep_admin (lockedDeptId задан) не может выдавать department_admin — только
  // loging_reader_dep своему сотруднику; остальное за account_admin'ом.
  const isDepScopedOperator = lockedDeptId !== null;
  const availableRoles = useMemo(() => {
    if (dept) {
      const deptScopedReader = {
        value: "loging_reader_dep",
        label: "Аудит-читатель отдела (loging_reader_dep)",
      };
      if (isDepScopedOperator) return [deptScopedReader];
      return [
        { value: "department_admin", label: "department_admin" },
        deptScopedReader,
      ];
    }
    return [
      { value: "account_admin", label: "account_admin" },
      { value: "loging_admin", label: "loging_admin" },
    ];
  }, [dept, isDepScopedOperator]);

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
    <div className="card w-full">
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
            hint={PASSWORD_POLICY_MESSAGE}
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
        <FormRow
          label="dept"
          hint={lockedDeptId ? "Отдел зафиксирован вашим scope" : undefined}
        >
          {lockedDeptId ? (
            <input className="input" value={lockedDeptName} disabled readOnly />
          ) : (
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
          )}
        </FormRow>
        <FormRow
          label="platform_role"
          hint={
            dept
              ? isDepScopedOperator
                ? "Своему отделу — аудит-читатель отдела"
                : "С отделом — department_admin или аудит-читатель отдела"
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
