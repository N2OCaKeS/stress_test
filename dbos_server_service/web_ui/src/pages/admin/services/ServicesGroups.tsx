import { useCallback, useEffect, useMemo, useState } from "react";
import {
  UsersRound,
  Edit3,
  Trash2,
  Plus,
  ShieldCheck,
  AlertTriangle,
  Layers,
  Bot,
  User as UserIcon,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useLabelsInvalidate, useLabelMaps } from "@/lib/labels";
import { formatMskDate, formatMskShort } from "@/lib/datetime";
import { InlineEditor, FormRow, StatRow, useInlineState } from "./_inline";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import * as groupsApi from "@/api/auth/groups";
import * as usersApi from "@/api/auth/users";
import * as botsApi from "@/api/auth/bots";
import { listDepartments } from "@/api/auth/departments";
import { listServices } from "@/api/auth/services";
import { listServiceRoles } from "@/api/auth/service_roles";
import { ApiError, apiErrMsg } from "@/api/client";
import type {
  Group,
  Department,
  Service,
  ServiceName,
  ServiceRole,
} from "@/api/auth/types";
import { useServiceLabel } from "@/lib/labels";
import { TruncationNotice } from "@/components/ui/TruncationNotice";

/**
 * Управление группами `auth_service`: профиль, участники (юзеры + боты),
 * выдача сервисов и назначение service-ролей.
 *
 * RBAC: account_admin видит все группы; dep_admin — только группы своего
 * dept_id (фильтрация на listGroupsByDepartment). Mock-режим пока не реализован
 * — карточка предупреждает и предлагает выключить VITE_USE_MOCK_AUTH.
 */
export function ServicesGroups() {
  const mock = useMockMode();
  if (mock) {
    return (
      <div className="p-8">
        <div className="alert-block">
          Управление группами доступно только в live-режиме. Отключите
          <span className="mono"> VITE_USE_MOCK_AUTH</span> или используйте
          реальный auth_service.
        </div>
      </div>
    );
  }
  return <ServicesGroupsLive />;
}

type GroupsSortKey =
  | "name_asc"
  | "name_desc"
  | "dept"
  | "created_desc"
  | "created_asc";
type GroupsGroupKey = "none" | "department";

function sortGroupsBy(items: Group[], key: GroupsSortKey, deptLabels: Map<string, string>): Group[] {
  const arr = [...items];
  const nameOf = (g: Group) => g.name.toLowerCase();
  const deptOf = (g: Group) =>
    (deptLabels.get(g.department_id) ?? g.department_id).toLowerCase();
  switch (key) {
    case "name_asc":
      return arr.sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
    case "name_desc":
      return arr.sort((a, b) => nameOf(b).localeCompare(nameOf(a)));
    case "dept":
      return arr.sort((a, b) => {
        const c = deptOf(a).localeCompare(deptOf(b));
        if (c !== 0) return c;
        return nameOf(a).localeCompare(nameOf(b));
      });
    case "created_desc":
      return arr.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    case "created_asc":
      return arr.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  }
}

function groupsGroupKeyOf(
  g: Group,
  by: GroupsGroupKey,
  deptLabels: Map<string, string>,
): string {
  switch (by) {
    case "department":
      if (!g.department_id) return "— без отдела —";
      return deptLabels.get(g.department_id) ?? g.department_id;
    default:
      return "";
  }
}

const GROUPS_PAGE_SIZE = 200;

function ServicesGroupsLive() {
  const { persona } = usePersona();
  const isAccountAdmin = persona.platform_role === "account_admin";
  const isDepAdmin = persona.platform_role === "dep_admin";
  const canCreate = isAccountAdmin || isDepAdmin;

  const [refreshTick, setRefreshTick] = useState(0);
  const invalidateLabels = useLabelsInvalidate();
  const bump = useCallback(() => {
    setRefreshTick((t) => t + 1);
    void invalidateLabels("groups");
  }, [invalidateLabels]);

  const [sortKey, setSortKey] = useState<GroupsSortKey>("name_asc");
  const [groupBy, setGroupBy] = useState<GroupsGroupKey>("none");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  // Pagination — accumulated pages держим тут, чтобы «Загрузить ещё» работало
  // append-style. Reset при смене listFn (dep_admin/account_admin) и при bump.
  const [accum, setAccum] = useState<Group[]>([]);
  const [offset, setOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadMoreErr, setLoadMoreErr] = useState<string | null>(null);

  // dep_admin видит только свой отдел; account_admin — все группы.
  const listFn = useMemo(() => {
    if (isAccountAdmin) {
      return (off: number) =>
        groupsApi.listGroups({ limit: GROUPS_PAGE_SIZE, offset: off });
    }
    if (isDepAdmin && persona.dept_id) {
      const deptId = persona.dept_id;
      return (off: number) =>
        groupsApi.listGroupsByDepartment(deptId, {
          limit: GROUPS_PAGE_SIZE,
          offset: off,
        });
    }
    // На случай logging_admin / нестандартной роли — пусто, кнопка «Создать» скрыта.
    return (_off: number) => Promise.resolve<Group[]>([]);
  }, [isAccountAdmin, isDepAdmin, persona.dept_id]);

  // Первая страница: используем useQuery — он же обрабатывает loading/error.
  // Сбрасываем accum при смене listFn / refreshTick.
  const listQ = useQuery(() => listFn(0), [refreshTick, listFn]);

  useEffect(() => {
    if (listQ.data) {
      setAccum(listQ.data);
      setOffset(listQ.data.length);
      setHasMore(listQ.data.length >= GROUPS_PAGE_SIZE);
      setLoadMoreErr(null);
    }
  }, [listQ.data]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    setLoadMoreErr(null);
    try {
      const next = await listFn(offset);
      setAccum((prev) => [...prev, ...next]);
      setOffset((prev) => prev + next.length);
      setHasMore(next.length >= GROUPS_PAGE_SIZE);
    } catch (e) {
      if (e instanceof ApiError) {
        setLoadMoreErr(`${e.errorCode}: ${e.message}`);
      } else if (e instanceof Error) {
        setLoadMoreErr(e.message);
      } else {
        setLoadMoreErr(String(e));
      }
    } finally {
      setLoadingMore(false);
    }
  }, [hasMore, listFn, loadingMore, offset]);

  const deptsQ = useQuery(() => listDepartments(), []);

  const rawItems = accum;
  const depts = deptsQ.data ?? [];
  const deptById = useMemo(() => {
    const m = new Map<string, Department>();
    for (const d of depts) m.set(d.id, d);
    return m;
  }, [depts]);

  // Labels из LabelsProvider предпочитаем как источник human-readable имён —
  // при изменении/создании отдела карта обновляется централизованно.
  const labelMaps = useLabelMaps();
  const deptLabels = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of depts) m.set(d.id, d.name);
    // Перекрываем тем, что есть в глобальной карте — она же используется на
    // соседних страницах и точно свежее после invalidate('depts').
    for (const [k, v] of labelMaps.depts) m.set(k, v);
    return m;
  }, [depts, labelMaps.depts]);

  const sorted = useMemo(
    () => sortGroupsBy(rawItems, sortKey, deptLabels),
    [rawItems, sortKey, deptLabels],
  );

  // Группировка: считаем порядок секций и для каждого элемента — является ли он
  // первым в своей группе (тогда renderRow вставит заголовок). Свёрнутая
  // группа оставляет в потоке один скрытый элемент-«якорь» только ради
  // заголовка, чтобы header не пропал из списка.
  const { items, firstInGroup, groupSizes } = useMemo(() => {
    if (groupBy === "none") {
      return {
        items: sorted,
        firstInGroup: new Map<string, string>(),
        groupSizes: new Map<string, number>(),
      };
    }
    const buckets = new Map<string, Group[]>();
    const order: string[] = [];
    for (const g of sorted) {
      const k = groupsGroupKeyOf(g, groupBy, deptLabels);
      if (!buckets.has(k)) {
        buckets.set(k, []);
        order.push(k);
      }
      buckets.get(k)!.push(g);
    }
    order.sort((a, b) => a.localeCompare(b));
    const flat: Group[] = [];
    const first = new Map<string, string>();
    const sizes = new Map<string, number>();
    for (const k of order) {
      const arr = buckets.get(k) ?? [];
      sizes.set(k, arr.length);
      if (arr.length === 0) continue;
      first.set(arr[0].id, k);
      if (collapsed[k]) {
        flat.push(arr[0]);
        continue;
      }
      for (const g of arr) flat.push(g);
    }
    return { items: flat, firstInGroup: first, groupSizes: sizes };
  }, [sorted, groupBy, deptLabels, collapsed]);

  function toggleGroup(k: string) {
    setCollapsed((cur) => ({ ...cur, [k]: !cur[k] }));
  }

  // dep_admin может редактировать только свою группу. account_admin — любую.
  const canEditGroup = useCallback(
    (g: Group) => {
      if (isAccountAdmin) return true;
      if (isDepAdmin && persona.dept_id) return g.department_id === persona.dept_id;
      return false;
    },
    [isAccountAdmin, isDepAdmin, persona.dept_id],
  );

  if (listQ.loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="spinner">Загрузка групп…</div>
      </div>
    );
  }
  if (listQ.error) {
    const msg =
      listQ.error instanceof ApiError
        ? `${listQ.error.errorCode}: ${listQ.error.message}`
        : listQ.error.message;
    return (
      <div className="flex-1 p-8">
        <div className="alert-danger">
          {msg}
          <button className="btn btn-sm ml-2" onClick={bump}>
            Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <InlineEditor
      title="Группы · auth_service"
      icon={UsersRound}
      hint={`live · ${rawItems.length} групп`}
      items={items}
      getId={(g) => g.id}
      canEdit={canCreate}
      readonlyNote={
        !canCreate ? "Просмотр без права изменения — нет admin-роли." : undefined
      }
      emptyHint="Выберите группу слева, чтобы увидеть участников, сервисы и роли."
      listFooter={
        <div className="flex flex-col gap-1 text-[11px]">
          {loadMoreErr && (
            <div className="alert-danger text-[11px]">{loadMoreErr}</div>
          )}
          {hasMore ? (
            <button
              className="btn btn-sm w-full"
              disabled={loadingMore}
              onClick={() => void loadMore()}
              title={`показано ${rawItems.length}, грузить следующие ${GROUPS_PAGE_SIZE}`}
            >
              {loadingMore
                ? "Загрузка…"
                : `Загрузить ещё (${GROUPS_PAGE_SIZE})`}
            </button>
          ) : (
            rawItems.length > 0 && (
              <div className="text-dim text-center">все · {rawItems.length}</div>
            )
          )}
        </div>
      }
      listHeader={
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <label className="flex items-center gap-1">
            <span className="text-dim">сорт.</span>
            <select
              className="input input-sm"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as GroupsSortKey)}
            >
              <option value="name_asc">name ↑</option>
              <option value="name_desc">name ↓</option>
              <option value="dept">по отделу</option>
              <option value="created_desc">создан ↓</option>
              <option value="created_asc">создан ↑</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <span className="text-dim">группа</span>
            <select
              className="input input-sm"
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupsGroupKey)}
            >
              <option value="none">—</option>
              <option value="department">по отделу</option>
            </select>
          </label>
        </div>
      }
      renderRow={({ item, active, onSelect }) => {
        const dept = deptById.get(item.department_id);
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
                <span className="text-dim">
                  · {groupSizes.get(groupHeaderKey) ?? 0} групп
                </span>
              </button>
            )}
            {isGroupCollapsed ? null : (
              <button
                className={`cred-row text-left ${active ? "active" : ""}`}
                onClick={onSelect}
              >
                <div className="flex items-center gap-2">
                  <UsersRound className="w-4 h-4 text-dim" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">
                      {item.name}
                    </div>
                    <div className="text-[11px] text-dim truncate mono">
                      {dept?.name ?? item.department_id}
                    </div>
                  </div>
                </div>
              </button>
            )}
          </>
        );
      }}
      renderDetail={(g, { editing, onClose }) => {
        const editable = canEditGroup(g);
        if (editing && editable) {
          return (
            <GroupEditForm
              initial={g}
              onDone={() => {
                onClose();
                bump();
              }}
            />
          );
        }
        return (
          <GroupDetailView
            group={g}
            dept={deptById.get(g.department_id) ?? null}
            canEdit={editable}
            onChange={bump}
          />
        );
      }}
      renderCreate={
        canCreate
          ? (close) => (
              <GroupCreateForm
                depts={depts}
                personaDeptId={persona.dept_id ?? null}
                lockDept={isDepAdmin && !isAccountAdmin}
                onDone={() => {
                  close();
                  bump();
                }}
              />
            )
          : undefined
      }
    />
  );
}

// ---------------------------------------------------------------------------
// Detail view — заголовок + секции profile / members / services / roles / danger
// ---------------------------------------------------------------------------

function GroupDetailView({
  group,
  dept,
  canEdit,
  onChange,
}: {
  group: Group;
  dept: Department | null;
  canEdit: boolean;
  onChange: () => void;
}) {
  const { startEdit } = useInlineState();
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const membersQ = useQuery(
    () => groupsApi.listGroupMembers(group.id),
    [group.id],
  );
  const botMembersQ = useQuery(
    () => groupsApi.listGroupBots(group.id),
    [group.id],
  );
  const servicesQ = useQuery(
    () => groupsApi.listGroupServices(group.id),
    [group.id],
  );
  const rolesQ = useQuery(
    () => groupsApi.listGroupRoles(group.id),
    [group.id],
  );

  const refetchAll = useCallback(() => {
    membersQ.refetch();
    botMembersQ.refetch();
    servicesQ.refetch();
    rolesQ.refetch();
    onChange();
  }, [membersQ, botMembersQ, servicesQ, rolesQ, onChange]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setErr(null);
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

  const members = membersQ.data ?? [];
  const botMembers = botMembersQ.data ?? [];
  const services = servicesQ.data ?? [];
  const roles = rolesQ.data ?? [];

  return (
    <div className="flex flex-col gap-4 max-w-3xl">
      {/* ============== Header / profile ============== */}
      <div className="card">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-semibold flex items-center gap-2">
            <UsersRound className="w-4 h-4 text-accent" />
            <span>{group.name}</span>
            {!canEdit && (
              <span
                className="badge badge-warn"
                title="Группа не в вашем отделе"
              >
                read-only
              </span>
            )}
          </h3>
          {canEdit && (
            <button
              className="btn flex items-center gap-1"
              onClick={() => startEdit(group.id)}
            >
              <Edit3 className="w-4 h-4" /> Edit
            </button>
          )}
        </div>

        {err && <div className="alert-danger mb-2">{err}</div>}

        <div className="text-xs uppercase text-dim mb-2">Профиль</div>
        <StatRow k="group_id" v={<span className="mono">{group.id}</span>} />
        <StatRow k="name" v={group.name} />
        <StatRow
          k="department"
          v={
            <span>
              {dept ? dept.name : group.department_id}
            </span>
          }
        />
        <StatRow k="description" v={group.description ?? "—"} />
        <StatRow
          k="members"
          v={`${members.length} юзеров · ${botMembers.length} ботов`}
        />
        <StatRow k="services" v={`${services.length}`} />
        <StatRow
          k="created_at"
          v={
            <span className="mono">
              {formatMskShort(group.created_at)}
            </span>
          }
        />
      </div>

      {/* ============== Members ============== */}
      <MembersCard
        group={group}
        users={members}
        bots={botMembers}
        loading={membersQ.loading || botMembersQ.loading}
        error={membersQ.error || botMembersQ.error}
        canEdit={canEdit}
        pending={pending}
        run={run}
      />

      {/* ============== Service grants ============== */}
      <ServicesCard
        group={group}
        granted={services}
        loading={servicesQ.loading}
        error={servicesQ.error}
        canEdit={canEdit}
        pending={pending}
        run={run}
      />

      {/* ============== Service roles ============== */}
      <RolesCard
        group={group}
        grantedServices={services.map((s) => s.service_name)}
        roles={roles}
        loading={rolesQ.loading}
        error={rolesQ.error}
        canEdit={canEdit}
        pending={pending}
        run={run}
      />

      {/* ============== Danger zone ============== */}
      {canEdit && (
        <div className="card" style={{ borderColor: "rgba(244,135,113,0.3)" }}>
          <div className="text-xs uppercase text-danger mb-2 flex items-center gap-2">
            <AlertTriangle className="w-3 h-3" /> Danger zone
          </div>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm">
              <div className="font-medium">Удалить группу</div>
              <div className="text-xs text-dim">
                Все участники потеряют выданные через эту группу права.
              </div>
            </div>
            <DeleteGroupButton
              groupId={group.id}
              groupName={group.name}
              disabled={pending}
              onDeleted={onChange}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Members card — users + bots
// ---------------------------------------------------------------------------

function MembersCard({
  group,
  users,
  bots,
  loading,
  error,
  canEdit,
  pending,
  run,
}: {
  group: Group;
  users: Array<{ user_id: string; username: string; added_at: string }>;
  bots: Array<{ bot_id: string; name: string; added_at: string }>;
  loading: boolean;
  error: Error | ApiError | null;
  canEdit: boolean;
  pending: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [addKind, setAddKind] = useState<"user" | "bot">("user");
  const [pickerId, setPickerId] = useState("");

  // Список юзеров/ботов для выбора. Фильтруем по dept — только из того же
  // отдела, что и группа (правило backend: members обязаны принадлежать dept).
  const usersQ = useQuery(
    () => usersApi.listUsersByDepartment(group.department_id, { limit: 200 }),
    [group.department_id],
    { enabled: canEdit && addKind === "user" },
  );
  const botsQ = useQuery(
    () =>
      botsApi.listBotsWithTotal({
        department_id: group.department_id,
        limit: 200,
      }),
    [group.department_id],
    { enabled: canEdit && addKind === "bot" },
  );

  const memberUserIds = useMemo(
    () => new Set(users.map((m) => m.user_id)),
    [users],
  );
  const memberBotIds = useMemo(
    () => new Set(bots.map((b) => b.bot_id)),
    [bots],
  );

  const candidateUsers = (usersQ.data?.items ?? []).filter(
    (u) => !memberUserIds.has(u.id),
  );
  const candidateBots = (botsQ.data?.items ?? []).filter(
    (b) => !memberBotIds.has(b.id),
  );

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-xs uppercase text-dim flex items-center gap-2">
          <UsersRound className="w-3 h-3" /> Участники · {users.length} юзеров ·{" "}
          {bots.length} ботов
        </div>
        {loading && <span className="text-xs text-dim">…</span>}
      </div>

      {error && (
        <div className="alert-danger mb-2">
          {error instanceof ApiError
            ? `${error.errorCode}: ${error.message}`
            : error.message}
        </div>
      )}

      {!loading && users.length === 0 && bots.length === 0 && (
        <div className="empty-card text-sm">В группе пока нет участников.</div>
      )}

      {(users.length > 0 || bots.length > 0) && (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">тип</th>
              <th className="pb-2 pr-3">id / name</th>
              <th className="pb-2 pr-3">added</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={`u:${u.user_id}`} className="border-t border-token">
                <td className="py-2">
                  <span className="badge badge-accent flex items-center gap-1 w-fit">
                    <UserIcon className="w-3 h-3" /> user
                  </span>
                </td>
                <td>
                  <div className="text-sm">{u.username}</div>
                  <div className="text-[11px] text-dim mono">{u.user_id}</div>
                </td>
                <td className="text-xs text-dim mono">
                  {formatMskDate(u.added_at)}
                </td>
                <td className="text-right">
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={!canEdit || pending}
                    onClick={() =>
                      run(() =>
                        groupsApi.removeGroupMember(group.id, u.user_id),
                      )
                    }
                  >
                    remove
                  </button>
                </td>
              </tr>
            ))}
            {bots.map((b) => (
              <tr key={`b:${b.bot_id}`} className="border-t border-token">
                <td className="py-2">
                  <span className="badge flex items-center gap-1 w-fit">
                    <Bot className="w-3 h-3" /> bot
                  </span>
                </td>
                <td>
                  <div className="text-sm mono">{b.name}</div>
                  <div className="text-[11px] text-dim mono">{b.bot_id}</div>
                </td>
                <td className="text-xs text-dim mono">
                  {formatMskDate(b.added_at)}
                </td>
                <td className="text-right">
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={!canEdit || pending}
                    onClick={() =>
                      run(() => groupsApi.removeGroupBot(group.id, b.bot_id))
                    }
                  >
                    remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canEdit && (
        <div className="mt-3 flex flex-wrap gap-2 items-center">
          <select
            className="input input-sm"
            value={addKind}
            onChange={(e) => {
              setAddKind(e.target.value as "user" | "bot");
              setPickerId("");
            }}
          >
            <option value="user">user</option>
            <option value="bot">bot</option>
          </select>
          <select
            className="input flex-1 mono"
            value={pickerId}
            onChange={(e) => setPickerId(e.target.value)}
          >
            <option value="">
              — выберите {addKind === "user" ? "юзера" : "бота"} —
            </option>
            {addKind === "user"
              ? candidateUsers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username} ({u.id})
                  </option>
                ))
              : candidateBots.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name} ({b.id})
                  </option>
                ))}
          </select>
          <button
            className="btn btn-primary flex items-center gap-1"
            disabled={pending || !pickerId}
            onClick={() =>
              run(async () => {
                if (addKind === "user") {
                  await groupsApi.addGroupMember(group.id, pickerId);
                } else {
                  await groupsApi.addGroupBot(group.id, pickerId);
                }
                setPickerId("");
              })
            }
          >
            <Plus className="w-4 h-4" /> Добавить
          </button>
          <TruncationNotice
            className="w-full"
            shown={
              addKind === "user"
                ? (usersQ.data?.items.length ?? 0)
                : (botsQ.data?.items.length ?? 0)
            }
            total={
              addKind === "user"
                ? (usersQ.data?.total ?? null)
                : (botsQ.data?.total ?? null)
            }
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Services card — grant / revoke service access
// ---------------------------------------------------------------------------

function ServicesCard({
  group,
  granted,
  loading,
  error,
  canEdit,
  pending,
  run,
}: {
  group: Group;
  granted: Array<{ service_name: ServiceName; granted_at?: string }>;
  loading: boolean;
  error: Error | ApiError | null;
  canEdit: boolean;
  pending: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const allServicesQ = useQuery(() => listServices(), [], { enabled: canEdit });
  const [picker, setPicker] = useState<ServiceName | "">("");

  const grantedSet = useMemo(
    () => new Set(granted.map((s) => s.service_name)),
    [granted],
  );
  const candidates: Service[] = (allServicesQ.data ?? []).filter(
    (s) => !grantedSet.has(s.service_name),
  );

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-xs uppercase text-dim flex items-center gap-2">
          <Layers className="w-3 h-3" /> Доступ к сервисам · {granted.length}
        </div>
        {loading && <span className="text-xs text-dim">…</span>}
      </div>

      {error && (
        <div className="alert-danger mb-2">
          {error instanceof ApiError
            ? `${error.errorCode}: ${error.message}`
            : error.message}
        </div>
      )}

      {!loading && granted.length === 0 && (
        <div className="empty-card text-sm">
          Группа пока не имеет доступа ни к одному сервису.
        </div>
      )}

      {granted.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">service</th>
              <th className="pb-2 pr-3">granted</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {granted.map((s) => (
              <tr key={s.service_name} className="border-t border-token">
                <td className="py-2 text-xs">
                  <ServiceInline name={s.service_name} />
                </td>
                <td className="text-xs text-dim mono">
                  {formatMskDate(s.granted_at)}
                </td>
                <td className="text-right">
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={!canEdit || pending}
                    onClick={() => {
                      if (
                        !confirm(
                          `Отозвать доступ группы «${group.name}» к ${s.service_name}? Все роли в этом сервисе тоже снимутся.`,
                        )
                      )
                        return;
                      void run(() =>
                        groupsApi.removeGroupService(group.id, s.service_name),
                      );
                    }}
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
        <div className="mt-3 flex gap-2 flex-wrap items-center">
          <select
            className="input flex-1 mono"
            value={picker}
            onChange={(e) => setPicker(e.target.value as ServiceName | "")}
          >
            <option value="">— сервис —</option>
            {candidates.map((s) => (
              <option key={s.service_name} value={s.service_name}>
                {s.service_name}
              </option>
            ))}
          </select>
          <button
            className="btn btn-primary flex items-center gap-1"
            disabled={pending || !picker}
            onClick={() =>
              run(async () => {
                if (!picker) return;
                await groupsApi.addGroupService(group.id, picker);
                setPicker("");
              })
            }
          >
            <Plus className="w-4 h-4" /> Выдать
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Roles card — service-роли группы. Backend хранит как `(service_name, roles[])`,
// POST replace-семантика, DELETE снимает все роли в сервисе.
// ---------------------------------------------------------------------------

function RolesCard({
  group,
  grantedServices,
  roles,
  loading,
  error,
  canEdit,
  pending,
  run,
}: {
  group: Group;
  grantedServices: ServiceName[];
  roles: Array<{ service_name: ServiceName; roles: string[] }>;
  loading: boolean;
  error: Error | ApiError | null;
  canEdit: boolean;
  pending: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-xs uppercase text-dim flex items-center gap-2">
          <ShieldCheck className="w-3 h-3" /> Service-роли · {roles.length}
        </div>
        {loading && <span className="text-xs text-dim">…</span>}
      </div>

      {error && (
        <div className="alert-danger mb-2">
          {error instanceof ApiError
            ? `${error.errorCode}: ${error.message}`
            : error.message}
        </div>
      )}

      {!loading && roles.length === 0 && (
        <div className="empty-card text-sm">Роли группе не выданы.</div>
      )}

      {roles.length > 0 && (
        <table className="w-full text-sm mb-3">
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
                    {r.roles.length === 0 ? (
                      <span className="text-dim italic">—</span>
                    ) : (
                      r.roles.map((role) => (
                        <span key={role} className="badge badge-accent">
                          {role}
                        </span>
                      ))
                    )}
                  </div>
                </td>
                <td className="text-right">
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={!canEdit || pending}
                    onClick={() =>
                      run(() =>
                        groupsApi.revokeGroupRoles(group.id, r.service_name),
                      )
                    }
                  >
                    revoke all
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canEdit && grantedServices.length > 0 && (
        <RoleAssigner
          deptId={group.department_id}
          grantedServices={grantedServices}
          existingByService={Object.fromEntries(
            roles.map((r) => [r.service_name, r.roles]),
          )}
          disabled={pending}
          onAssign={(serviceName, roleList) =>
            run(() =>
              groupsApi.assignGroupRoles(group.id, {
                service_name: serviceName,
                roles: roleList,
              }),
            )
          }
        />
      )}

      {canEdit && grantedServices.length === 0 && (
        <div className="text-xs text-dim">
          Сначала выдайте группе доступ к сервису — тогда можно назначить роли.
        </div>
      )}
    </div>
  );
}

function RoleAssigner({
  deptId,
  grantedServices,
  existingByService,
  disabled,
  onAssign,
}: {
  deptId: string;
  grantedServices: ServiceName[];
  existingByService: Record<string, string[]>;
  disabled: boolean;
  onAssign: (serviceName: ServiceName, roles: string[]) => void;
}) {
  const [serviceName, setServiceName] = useState<ServiceName | "">(
    grantedServices[0] ?? "",
  );
  const rolesQ = useQuery<ServiceRole[]>(
    () => listServiceRoles(deptId, serviceName as ServiceName),
    [deptId, serviceName],
    { enabled: !!serviceName },
  );
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);

  const allRoles = rolesQ.data ?? [];
  const existing = serviceName ? existingByService[serviceName] ?? [] : [];

  const toggleRole = (name: string) => {
    setSelectedRoles((prev) =>
      prev.includes(name) ? prev.filter((r) => r !== name) : [...prev, name],
    );
  };

  return (
    <div className="border-t border-token pt-3 flex flex-col gap-2">
      <div className="text-xs text-dim uppercase">Назначить роли (replace)</div>
      <div className="flex flex-wrap gap-2 items-center">
        <select
          className="input input-sm mono"
          value={serviceName}
          onChange={(e) => {
            setServiceName(e.target.value as ServiceName);
            setSelectedRoles([]);
          }}
        >
          {grantedServices.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {rolesQ.loading && <span className="text-xs text-dim">…</span>}
        {rolesQ.error && (
          <span className="text-xs text-danger">
            {rolesQ.error instanceof ApiError
              ? rolesQ.error.errorCode
              : rolesQ.error.message}
          </span>
        )}
      </div>
      {allRoles.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {allRoles.map((r) => {
            const checked = selectedRoles.includes(r.role_name);
            const isExisting = existing.includes(r.role_name);
            return (
              <label
                key={r.role_name}
                className={`badge cursor-pointer ${checked ? "badge-accent" : ""}`}
                title={isExisting ? "уже назначена" : undefined}
              >
                <input
                  type="checkbox"
                  className="mr-1"
                  checked={checked}
                  onChange={() => toggleRole(r.role_name)}
                />
                {r.role_name}
                {isExisting && <span className="text-dim ml-1">·current</span>}
              </label>
            );
          })}
        </div>
      )}
      {!rolesQ.loading && allRoles.length === 0 && serviceName && (
        <div className="text-xs text-dim">
          В сервисе {serviceName} нет ролей для отдела {deptId}.
        </div>
      )}
      <div className="flex gap-2 flex-wrap">
        <button
          className="btn btn-sm"
          disabled={disabled || !serviceName}
          onClick={() => setSelectedRoles(existing)}
          title="Заполнить чекбоксы текущим набором"
        >
          текущие
        </button>
        <button
          className="btn btn-primary btn-sm flex items-center gap-1"
          disabled={disabled || !serviceName}
          onClick={() => {
            if (!serviceName) return;
            onAssign(serviceName, selectedRoles);
          }}
        >
          <ShieldCheck className="w-3 h-3" /> Применить (replace)
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / edit forms
// ---------------------------------------------------------------------------

function GroupCreateForm({
  depts,
  personaDeptId,
  lockDept,
  onDone,
}: {
  depts: Department[];
  personaDeptId: string | null;
  lockDept: boolean;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [deptId, setDeptId] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Synchronise dept default once depts have loaded — useState initial fires
  // once on mount, before depts arrive, so without this `deptId` stays "".
  useEffect(() => {
    if (deptId) return;
    if (lockDept && personaDeptId) {
      setDeptId(personaDeptId);
    } else if (depts.length > 0) {
      setDeptId(depts[0].id);
    }
  }, [deptId, depts, lockDept, personaDeptId]);

  const submit = async () => {
    setErr(null);
    setPending(true);
    try {
      await groupsApi.createGroup({
        name: name.trim(),
        department_id: deptId,
        description: description.trim() || undefined,
      });
      onDone();
    } catch (e) {
      if (e instanceof ApiError) {
        const errs = (e.details as { errors?: { loc?: unknown[]; msg?: string }[] } | undefined)?.errors;
        const extra = errs?.length
          ? " · " + errs.map((x) => `${(x.loc ?? []).join(".")}: ${x.msg}`).join("; ")
          : "";
        setErr(`${apiErrMsg(e)}${extra}`);
      } else setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <UsersRound className="w-4 h-4 text-accent" /> Новая группа
      </h3>
      {err && <div className="alert-danger mb-2">{err}</div>}
      <div className="flex flex-col gap-3">
        <FormRow label="name" hint="человеческое имя — уникально внутри отдела">
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Операторы (read-only)"
          />
        </FormRow>
        <FormRow label="department_id">
          <select
            className="input"
            value={deptId}
            disabled={lockDept}
            onChange={(e) => setDeptId(e.target.value)}
          >
            {depts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow label="description">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          disabled={pending || !name.trim() || !deptId}
          onClick={submit}
        >
          Создать
        </button>
      </div>
    </div>
  );
}

function GroupEditForm({
  initial,
  onDone,
}: {
  initial: Group;
  onDone: () => void;
}) {
  const [name, setName] = useState(initial.name ?? "");
  const [description, setDescription] = useState(initial.description ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    setErr(null);
    setPending(true);
    try {
      await groupsApi.patchGroup(initial.id, {
        name: name.trim() || undefined,
        description: description.trim() || undefined,
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
        <UsersRound className="w-4 h-4 text-accent" /> Edit · {initial.name}
      </h3>
      {err && <div className="alert-danger mb-2">{err}</div>}
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input
            className="input"
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
        <div className="text-xs text-dim">
          department_id не редактируется. Чтобы перенести группу — удалите и
          создайте новую.
        </div>
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

function DeleteGroupButton({
  groupId,
  groupName,
  disabled,
  onDeleted,
}: {
  groupId: string;
  groupName: string;
  disabled: boolean;
  onDeleted: () => void;
}) {
  const { select } = useInlineState();
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    if (
      !confirm(
        `Удалить группу «${groupName}»? Все участники потеряют выданные через эту группу права.`,
      )
    )
      return;
    setErr(null);
    setPending(true);
    try {
      await groupsApi.deleteGroup(groupId);
      select(null);
      onDeleted();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      {err && <div className="alert-danger text-xs">{err}</div>}
      <button
        className="btn btn-danger flex items-center gap-1"
        disabled={disabled || pending}
        onClick={submit}
      >
        <Trash2 className="w-4 h-4" /> Delete
      </button>
    </div>
  );
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
