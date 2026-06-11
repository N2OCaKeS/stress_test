import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  UsersRound,
  ShieldCheck,
  ListTree,
  GitCompareArrows,
  Trash2,
  UserPlus,
  Bot as BotIcon,
  KeyRound,
  Edit3,
  Plug,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { USERS, userById } from "@/mocks/auth";
import { usePersona } from "@/contexts/PersonaContext";
import { groupMutationCaps } from "@/lib/rbac";
import {
  GROUPS,
  ROLES,
  GROUP_MEMBERS,
  GROUP_ASSIGNMENTS,
  DIRECT_GRANTS,
} from "@/mocks/permissions";
import {
  computeEffectiveGroup,
  computeEffectiveUser,
  applyMutation,
  computeDiff,
  defaultStore,
} from "./permissionGraph";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import * as groupsApi from "@/api/auth/groups";
import { ApiError } from "@/api/client";
import { useDeptLabel, useServiceLabel } from "@/lib/labels";

export function GroupDetail() {
  const { id } = useParams<{ id: string }>();
  const { persona } = usePersona();
  const mockMode = useMockMode();
  // В mock-режиме используем mock GROUPS / GROUP_MEMBERS / permissionGraph.
  // В live-режиме всё через GroupLiveData; здесь определяем владельца отдела
  // для caps через getGroup.
  const mockGroup = useMemo(
    () => (mockMode ? GROUPS.find((g) => g.id === id) : undefined),
    [mockMode, id],
  );
  const liveGroupQ = useQuery(
    () => groupsApi.getGroup(id ?? ""),
    [id],
    { enabled: !mockMode && !!id },
  );
  const group = mockGroup;
  const ownerDept = mockMode
    ? mockGroup?.owner_dept ?? null
    : liveGroupQ.data?.department_id ?? null;
  const caps = groupMutationCaps(persona, ownerDept);

  const [diffOn, setDiffOn] = useState(false);

  const groupId = mockMode ? (mockGroup?.id ?? "") : (liveGroupQ.data?.id ?? id ?? "");
  const memberRefs = mockMode ? (GROUP_MEMBERS[groupId] ?? []) : [];
  const members = mockMode
    ? (memberRefs
        .map((uid) => USERS.find((u) => u.id === uid))
        .filter(Boolean) as typeof USERS)
    : [];
  const orphanMembers = mockMode
    ? memberRefs.filter((uid) => !USERS.some((u) => u.id === uid))
    : [];

  const groupRoles = mockMode ? (GROUP_ASSIGNMENTS[groupId] ?? []) : [];
  const effective = useMemo(
    () => (mockMode && groupId ? computeEffectiveGroup(groupId) : []),
    [mockMode, groupId],
  );

  // For each member: how much this group contributes (mock-only — permission
  // graph живёт только в моках).
  const memberContrib = useMemo(() => {
    if (!mockMode || !groupId) return [];
    return members.map((m) => {
      const before = computeEffectiveUser(m.id);
      const after = computeEffectiveUser(
        m.id,
        applyMutation(defaultStore, { kind: "remove_from_group", user_id: m.id, group_id: groupId }),
      );
      const diff = computeDiff(before, after);
      const lostByThisGroup = diff.filter((d) => d.change === "removed").length;
      return { user: m, lostByThisGroup, total: before.length };
    });
  }, [mockMode, members, groupId]);

  const groupDeleteDiff = useMemo(() => {
    if (!mockMode || !diffOn || !groupId) return null;
    return members.map((m) => {
      const before = computeEffectiveUser(m.id);
      const after = computeEffectiveUser(
        m.id,
        applyMutation(defaultStore, { kind: "remove_group", group_id: groupId }),
      );
      return { user: m, diff: computeDiff(before, after).filter((d) => d.change === "removed") };
    });
  }, [mockMode, diffOn, members, groupId]);

  if (mockMode && !group) {
    return (
      <Shell breadcrumb="auth_service / users / group">
        <section className="flex-1 overflow-y-auto p-8">
          <div className="empty-card danger">
            Группа <span className="mono">{id}</span> не найдена.
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

  const headerName = mockMode ? group!.name : (liveGroupQ.data?.display_name || liveGroupQ.data?.name || id || "—");
  const headerDescription = mockMode ? group!.description : (liveGroupQ.data?.description ?? "");
  return (
    <Shell breadcrumb={`auth_service / users / group / ${headerName}`}>
      <section className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* HEADER */}
        <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
          <Link to="/users" className="btn btn-ghost mt-1">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="w-12 h-12 rounded-full surface-2 border border-token flex items-center justify-center">
            <UsersRound className="w-6 h-6 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">{headerName}</h1>
              {mockMode ? (
                group!.cross_dept ? (
                  <span className="badge badge-warn">cross-dept</span>
                ) : (
                  <span className="badge">dept · {group!.owner_dept}</span>
                )
              ) : liveGroupQ.data ? (
                <GroupHeaderDept deptId={liveGroupQ.data.department_id} />
              ) : null}
              {mockMode && (
                <span className="badge">{members.length} участников</span>
              )}
            </div>
            <div className="text-sm text-dim mt-1">{headerDescription}</div>
          </div>
          <div className="flex items-center gap-2">
            {!caps.edit && !caps.delete && !caps.manageMembers && (
              <span className="badge badge-warn" title={caps.reason}>
                read-only
              </span>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 grid grid-cols-2 gap-5 auto-rows-min">
          <GroupLiveData groupId={groupId} caps={caps} />
          {mockMode && group && <>
          {/* Members */}
          <Section icon={<UsersRound className="w-4 h-4" />} title={`Members · ${members.length}`}>
            {members.length === 0 && orphanMembers.length === 0 ? (
              <div className="text-sm text-dim italic">Пусто.</div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {members.map((m) => (
                  <Link key={m.id} to={`/users/${m.id}`} className="member-pill">
                    <span className="av">{m.username.slice(0, 2).toUpperCase()}</span>
                    <span className={m.is_bot ? "mono text-xs" : ""}>{m.username}</span>
                    {m.is_bot && <BotIcon className="w-3 h-3 text-dim" />}
                  </Link>
                ))}
                {orphanMembers.map((uid) => (
                  <span
                    key={uid}
                    className="member-pill"
                    title="Ссылка на отсутствующего пользователя"
                  >
                    <span className="av">—</span>
                    <span className="mono text-xs text-dim">{uid} (удалён)</span>
                  </span>
                ))}
              </div>
            )}
          </Section>

          {/* Group roles */}
          <Section icon={<ShieldCheck className="w-4 h-4" />} title={`Роли группы · ${groupRoles.length}`}>
            {groupRoles.length === 0 ? (
              <div className="text-sm text-dim italic">Роли группе не назначены.</div>
            ) : (
              <div className="flex flex-col gap-2">
                {groupRoles.map((ra) => {
                  const r = ROLES.find((x) => x.id === ra.role_id)!;
                  return (
                    <div key={ra.role_id} className="row-line">
                      <div className="flex items-center gap-2">
                        <span className="badge badge-accent">{r.name}</span>
                        <span className="text-xs text-dim">{r.service}</span>
                      </div>
                      <span className="text-xs">
                        {ra.scope_kind === "platform" && <span className="badge">платформа</span>}
                        {ra.scope_kind === "dept" && <span className="badge">отдел · {ra.scope_ref}</span>}
                        {ra.scope_kind === "resource" && <span className="badge">{ra.scope_ref}</span>}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </Section>

          {/* Direct grants on the group itself */}
          {(() => {
            const groupGrants = DIRECT_GRANTS.filter(
              (g) => g.subject_kind === "group" && g.subject_id === group.id,
            );
            if (groupGrants.length === 0) return null;
            return (
              <Section
                icon={<KeyRound className="w-4 h-4" />}
                title={`Прямые grants на группу · ${groupGrants.length}`}
                className="col-span-2"
              >
                <div className="text-xs text-dim mb-2">
                  Эти permissions унаследуют все члены группы.
                </div>
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
                    {groupGrants.map((g) => {
                      const grantor = userById(g.granted_by);
                      return (
                        <tr key={g.id} className="border-t border-token">
                          <td className="py-2 mono text-xs">{g.permission}</td>
                          <td className="text-xs">
                            <span
                              className={`domain-tag ${g.resource_kind === "secret" ? "tag-secret" : "tag-server"}`}
                            >
                              {g.resource_kind}
                            </span>
                            <span className="mono">{g.resource_id}</span>
                          </td>
                          <td className="text-xs text-dim">
                            {grantor ? (
                              <Link to={`/users/${grantor.id}`} className="mono hover-bg">
                                {grantor.username}
                              </Link>
                            ) : (
                              <span className="mono text-dim" title="user removed or unknown">
                                {g.granted_by} (удалён)
                              </span>
                            )}
                          </td>
                          <td className="text-xs text-dim">
                            {g.granted_at.slice(0, 10)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Section>
            );
          })()}

          {/* Effective for group */}
          <Section icon={<ListTree className="w-4 h-4" />} title={`Effective permissions (group) · ${effective.length}`} className="col-span-2">
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase surface-2">
                <tr>
                  <th className="px-3 py-2">Permission</th>
                  <th className="px-3 py-2">Service</th>
                  <th className="px-3 py-2">Scope</th>
                  <th className="px-3 py-2">Источник</th>
                </tr>
              </thead>
              <tbody>
                {effective.map((e) => (
                  <tr key={`${e.permission}-${e.scope_ref}`} className="border-t border-token">
                    <td className="px-3 py-2 mono text-xs">{e.permission}</td>
                    <td className="px-3 py-2 text-xs">{e.service}</td>
                    <td className="px-3 py-2 text-xs">
                      {e.scope_kind === "platform" && <span className="badge">платформа</span>}
                      {e.scope_kind === "dept" && <span className="badge">отдел · {e.scope_ref}</span>}
                      {e.scope_kind === "resource" && <span className="badge">{e.scope_ref}</span>}
                    </td>
                    <td className="px-3 py-2">
                      {e.sources.map((s) => (
                        <span key={s.id} className="badge badge-accent">{s.label}</span>
                      ))}
                    </td>
                  </tr>
                ))}
                {effective.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-3 text-sm text-dim italic">Группа не наделена правами.</td></tr>
                )}
              </tbody>
            </table>
          </Section>

          {/* Members contribution */}
          <Section icon={<UsersRound className="w-4 h-4" />} title="Что добавляет эта группа каждому члену" className="col-span-2">
            {memberContrib.length === 0 ? (
              <div className="text-sm text-dim italic">Нет участников.</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">User</th>
                    <th className="pb-2 pr-3">Перм. всего</th>
                    <th className="pb-2 pr-3">Из них даёт эта группа</th>
                    <th className="pb-2 pr-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {memberContrib.map(({ user, total, lostByThisGroup }) => (
                    <tr key={user.id} className="border-t border-token">
                      <td className="py-2">
                        <Link to={`/users/${user.id}`} className="hover-bg">{user.username}</Link>
                      </td>
                      <td className="text-xs">{total}</td>
                      <td className="text-xs">
                        {lostByThisGroup > 0 ? (
                          <span className="badge badge-warn">+{lostByThisGroup}</span>
                        ) : (
                          <span className="text-dim">0</span>
                        )}
                      </td>
                      <td>
                        <Link to={`/users/${user.id}`} className="btn btn-sm">детали</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          {/* Delete-group diff */}
          <Section icon={<GitCompareArrows className="w-4 h-4" />} title="Diff: что потеряют члены если удалить группу" className="col-span-2">
            <div className="flex items-center gap-2 mb-3">
              <button
                className={`btn btn-sm ${diffOn ? "btn-primary" : ""}`}
                onClick={() => setDiffOn(!diffOn)}
              >
                {diffOn ? "скрыть" : "посчитать"}
              </button>
              <span className="text-xs text-dim">simulate: remove_group({group.name})</span>
            </div>
            {diffOn && groupDeleteDiff && (
              <div className="flex flex-col gap-2">
                {groupDeleteDiff.map(({ user, diff }) => (
                  <div key={user.id} className="border border-token rounded p-3" style={{ background: "rgba(244,135,113,0.05)" }}>
                    <div className="flex items-center gap-2 mb-2">
                      <Link to={`/users/${user.id}`} className="font-medium text-sm">{user.username}</Link>
                      <span className="text-xs text-dim">потеряет {diff.length} permission'ов</span>
                    </div>
                    {diff.length === 0 ? (
                      <div className="text-xs text-dim italic">Ничего — у юзера эти права остались бы из других источников.</div>
                    ) : (
                      <ul className="text-xs">
                        {diff.map((d) => (
                          <li key={`${d.permission}-${d.scope_ref}`} className="mono py-0.5">
                            <span className="text-danger">−</span> {d.permission} <span className="text-dim">({d.scope_kind})</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Section>
          </>}
        </div>
      </section>
    </Shell>
  );
}

/**
 * Live-data panel: queries auth_service for the *real* group definition,
 * members, bots, roles and service-access. In mock mode it is hidden — the
 * pre-existing mock graph above already paints everything.
 *
 * Actions (create member, grant role, delete group, etc.) are gated by
 * `caps` and call `groupsApi` directly. On any 2xx the panel refetches
 * its data so the next render reflects the new state.
 */
function GroupLiveData({
  groupId,
  caps,
}: {
  groupId: string;
  caps: ReturnType<typeof groupMutationCaps>;
}) {
  const mock = useMockMode();

  const detail = useQuery(() => groupsApi.getGroup(groupId), [groupId], {
    enabled: !mock,
  });
  const members = useQuery(
    () => groupsApi.listGroupMembers(groupId),
    [groupId],
    { enabled: !mock },
  );
  const bots = useQuery(
    () => groupsApi.listGroupBots(groupId),
    [groupId],
    { enabled: !mock },
  );
  const services = useQuery(
    () => groupsApi.listGroupServices(groupId),
    [groupId],
    { enabled: !mock },
  );
  const roles = useQuery(
    () => groupsApi.listGroupRoles(groupId),
    [groupId],
    { enabled: !mock },
  );

  const [addMemberId, setAddMemberId] = useState("");
  const [addBotId, setAddBotId] = useState("");
  const [addService, setAddService] = useState("");
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const refetchAll = useCallback(() => {
    detail.refetch();
    members.refetch();
    bots.refetch();
    services.refetch();
    roles.refetch();
  }, [detail, members, bots, services, roles]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActionErr(null);
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

  if (mock) return null;

  const loading =
    detail.loading ||
    members.loading ||
    bots.loading ||
    services.loading ||
    roles.loading;

  // Most-informative error (group not found dominates).
  const errs = [detail.error, members.error, bots.error, services.error, roles.error].filter(
    Boolean,
  );
  const firstErr = errs[0];

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
        <div className="alert-danger">
          {firstErr.message}
          <button className="btn btn-sm ml-2" onClick={refetchAll}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !firstErr && (
        <div className="flex flex-col gap-4">
          {actionErr && <div className="alert-danger">{actionErr}</div>}

          {/* Group meta + edit/delete */}
          {detail.data && (
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-xs text-dim">name</div>
                <div className="mono">{detail.data.name}</div>
              </div>
              <div>
                <div className="text-xs text-dim">display_name</div>
                <div>{detail.data.display_name ?? "—"}</div>
              </div>
              <div>
                <div className="text-xs text-dim">department</div>
                <GroupDeptLine deptId={detail.data.department_id} />
              </div>
              <div>
                <div className="text-xs text-dim">description</div>
                <div>{detail.data.description ?? "—"}</div>
              </div>
              <div className="col-span-2 flex gap-2">
                <button
                  className="btn flex items-center gap-1"
                  disabled={!caps.edit || pending}
                  title={caps.edit ? "Изменить display_name / description" : caps.reason}
                  onClick={() => {
                    const next = window.prompt(
                      "Новый display_name:",
                      detail.data?.display_name ?? "",
                    );
                    if (next === null) return;
                    run(() =>
                      groupsApi.patchGroup(groupId, { display_name: next }),
                    );
                  }}
                >
                  <Edit3 className="w-4 h-4" /> Изменить
                </button>
                <button
                  className="btn btn-danger flex items-center gap-1"
                  disabled={!caps.delete || pending}
                  title={
                    caps.delete
                      ? "Все participants потеряют роли группы"
                      : caps.reason
                  }
                  onClick={() => {
                    if (
                      !window.confirm(
                        "Удалить группу? Участники потеряют унаследованные роли.",
                      )
                    ) {
                      return;
                    }
                    run(() => groupsApi.deleteGroup(groupId));
                  }}
                >
                  <Trash2 className="w-4 h-4" /> Удалить
                </button>
              </div>
            </div>
          )}

          {/* Members live */}
          <div>
            <div className="text-xs uppercase text-dim mb-2">
              Участники · {(members.data ?? []).length}
            </div>
            {(members.data ?? []).length === 0 ? (
              <div className="empty-card">Нет участников.</div>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {(members.data ?? []).map((m) => (
                  <li
                    key={m.user_id}
                    className="row-line flex items-center justify-between"
                  >
                    <span>
                      <span className="mono">{m.username}</span>
                      <span className="text-xs text-dim ml-2">
                        ({m.user_id})
                      </span>
                    </span>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={!caps.manageMembers || pending}
                      onClick={() =>
                        run(() =>
                          groupsApi.removeGroupMember(groupId, m.user_id),
                        )
                      }
                    >
                      убрать
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2 mt-2">
              <input
                className="input"
                placeholder="user_id"
                value={addMemberId}
                onChange={(e) => setAddMemberId(e.target.value)}
              />
              <button
                className="btn btn-primary flex items-center gap-1"
                disabled={!caps.manageMembers || pending || !addMemberId.trim()}
                onClick={() =>
                  run(async () => {
                    await groupsApi.addGroupMember(groupId, addMemberId.trim());
                    setAddMemberId("");
                  })
                }
              >
                <UserPlus className="w-4 h-4" /> Добавить
              </button>
            </div>
          </div>

          {/* Bots live */}
          <div>
            <div className="text-xs uppercase text-dim mb-2">
              Боты · {(bots.data ?? []).length}
            </div>
            {(bots.data ?? []).length === 0 ? (
              <div className="empty-card">Ботов в группе нет.</div>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {(bots.data ?? []).map((b) => (
                  <li
                    key={b.bot_id}
                    className="row-line flex items-center justify-between"
                  >
                    <span>
                      <BotIcon className="w-3 h-3 inline mr-1" />
                      <span className="mono">{b.name}</span>
                    </span>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={!caps.manageMembers || pending}
                      onClick={() =>
                        run(() => groupsApi.removeGroupBot(groupId, b.bot_id))
                      }
                    >
                      убрать
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2 mt-2">
              <input
                className="input"
                placeholder="bot_id"
                value={addBotId}
                onChange={(e) => setAddBotId(e.target.value)}
              />
              <button
                className="btn btn-primary flex items-center gap-1"
                disabled={!caps.manageMembers || pending || !addBotId.trim()}
                onClick={() =>
                  run(async () => {
                    await groupsApi.addGroupBot(groupId, addBotId.trim());
                    setAddBotId("");
                  })
                }
              >
                <BotIcon className="w-4 h-4" /> Добавить бота
              </button>
            </div>
          </div>

          {/* Services + roles live */}
          <div>
            <div className="text-xs uppercase text-dim mb-2">
              Service-access · {(services.data ?? []).length}
            </div>
            {(services.data ?? []).length === 0 ? (
              <div className="empty-card">Нет доступа к сервисам.</div>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {(services.data ?? []).map((s) => (
                  <li
                    key={s.service_name}
                    className="row-line flex items-center justify-between"
                  >
                    <span>
                      <ServiceInline name={s.service_name} />
                    </span>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={!caps.edit || pending}
                      onClick={() =>
                        run(() =>
                          groupsApi.removeGroupService(
                            groupId,
                            s.service_name,
                          ),
                        )
                      }
                    >
                      revoke
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2 mt-2">
              <input
                className="input"
                placeholder="service_name"
                value={addService}
                onChange={(e) => setAddService(e.target.value)}
              />
              <button
                className="btn btn-primary flex items-center gap-1"
                disabled={!caps.edit || pending || !addService.trim()}
                onClick={() =>
                  run(async () => {
                    await groupsApi.addGroupService(groupId, addService.trim());
                    setAddService("");
                  })
                }
              >
                <Plug className="w-4 h-4" /> Grant access
              </button>
            </div>
          </div>

          {/* Roles live */}
          <div>
            <div className="text-xs uppercase text-dim mb-2">
              Роли группы · {(roles.data ?? []).length}
            </div>
            {(roles.data ?? []).length === 0 ? (
              <div className="empty-card">Роли группе не выданы.</div>
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
                  {(roles.data ?? []).map((r) => (
                    <tr key={r.service_name} className="border-t border-token">
                      <td className="py-2 text-xs">
                        <ServiceInline name={r.service_name} />
                      </td>
                      <td className="text-xs">{r.roles.join(", ")}</td>
                      <td>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={!caps.edit || pending}
                          onClick={() =>
                            run(() =>
                              groupsApi.revokeGroupRoles(
                                groupId,
                                r.service_name,
                              ),
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
            <RoleAssignRow
              disabled={!caps.edit || pending}
              onAssign={(service, list) =>
                run(() =>
                  groupsApi.assignGroupRoles(groupId, {
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

function RoleAssignRow({
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
        placeholder="roles (csv, например reader,operator)"
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

function Section({ icon, title, children, className = "" }: { icon: React.ReactNode; title: string; children: React.ReactNode; className?: string; }) {
  return (
    <div className={`surface border border-token rounded-lg p-4 flex flex-col min-h-0 ${className}`}>
      <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
        {icon} {title}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}

function GroupDeptLine({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <div>{label}</div>;
}

function GroupHeaderDept({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span className="badge">dept · {label}</span>;
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
