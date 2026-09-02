/* Permission graph — aggregate effective permissions for user/group/bot from
 * the mock store and compute diffs against hypothetical mutations.
 *
 * In-memory only — no network. The store import is statically resolved so
 * the helper works in tests too. */

import {
  GROUPS,
  ROLES,
  USER_ASSIGNMENTS,
  GROUP_ASSIGNMENTS,
  GROUP_MEMBERS,
  BOT_ASSIGNMENTS,
  BOTS,
  DIRECT_GRANTS,
  type MockGroup,
  type RoleDef,
  type RoleAssignment,
  type DirectGrant,
} from "@/mocks/permissions";

export type SourceKind = "group" | "role" | "direct";

export interface Source {
  kind: SourceKind;
  // For 'group': group_id; for 'role': role_id; for 'direct': grant_id.
  id: string;
  label: string;
  // Trace tail — when a permission comes through a group→role chain
  via_group?: string;
}

export interface Permission {
  permission: string;
  service: "auth" | "server" | "secret" | "logging" | "worker" | "platform";
  scope_kind: "platform" | "dept" | "resource";
  scope_ref: string | null;
}

export interface EffectiveEntry extends Permission {
  sources: Source[];
}

// Nested trace node — describes one branch of "почему у subject есть этот
// permission". Depth >1 used when permission приходит через group → role
// chain: outer node is the group, inner via[] holds the role(s) внутри
// группы, которые непосредственно несут permission.
export interface TraceNode {
  source: "group" | "role" | "grant";
  // id of the group / role / grant
  id: string;
  label: string;
  via?: TraceNode[];
}

export interface Trace {
  permission: string;
  // Roots — каждый root объясняет один независимый путь к permission.
  roots: TraceNode[];
}

// Internal — derive service from permission string suffix.
function deriveService(perm: string): Permission["service"] {
  // pattern: action:domain:rest
  const parts = perm.split(":");
  const domain = parts[1] ?? "";
  if (domain === "audit") return "logging";
  if (domain === "server") return "server";
  if (domain === "secret") return "secret";
  if (domain === "worker") return "worker";
  if (domain === "user" || domain === "dept" || domain === "bot") return "auth";
  return "platform";
}

function expandRolePermissions(
  role: RoleDef,
  scope_kind: RoleAssignment["scope_kind"],
  scope_ref: string | null,
): Permission[] {
  return role.permissions.map((p) => ({
    permission: p,
    service: deriveService(p),
    scope_kind,
    scope_ref,
  }));
}

interface Store {
  groups: MockGroup[];
  roles: RoleDef[];
  userAssignments: typeof USER_ASSIGNMENTS;
  groupAssignments: typeof GROUP_ASSIGNMENTS;
  groupMembers: typeof GROUP_MEMBERS;
  botAssignments: typeof BOT_ASSIGNMENTS;
  directGrants: DirectGrant[];
}

export const defaultStore: Store = {
  groups: GROUPS,
  roles: ROLES,
  userAssignments: USER_ASSIGNMENTS,
  groupAssignments: GROUP_ASSIGNMENTS,
  groupMembers: GROUP_MEMBERS,
  botAssignments: BOT_ASSIGNMENTS,
  directGrants: DIRECT_GRANTS,
};

function findRole(store: Store, id: string): RoleDef | undefined {
  return store.roles.find((r) => r.id === id);
}
function findGroup(store: Store, id: string): MockGroup | undefined {
  return store.groups.find((g) => g.id === id);
}

// Merge a new (permission, source) into list — dedup by (permission, scope).
function mergeEntry(acc: EffectiveEntry[], perm: Permission, source: Source) {
  const key = `${perm.permission}|${perm.scope_kind}|${perm.scope_ref ?? ""}`;
  const existing = acc.find(
    (e) =>
      `${e.permission}|${e.scope_kind}|${e.scope_ref ?? ""}` === key,
  );
  if (existing) {
    if (!existing.sources.find((s) => s.kind === source.kind && s.id === source.id)) {
      existing.sources.push(source);
    }
    return;
  }
  acc.push({ ...perm, sources: [source] });
}

// Compute effective permissions for a user.
export function computeEffectiveUser(
  userId: string,
  store: Store = defaultStore,
): EffectiveEntry[] {
  const acc: EffectiveEntry[] = [];
  const ua = store.userAssignments[userId];

  // 1. roles directly assigned
  if (ua) {
    for (const ra of ua.roles) {
      const role = findRole(store, ra.role_id);
      if (!role) continue;
      const perms = expandRolePermissions(role, ra.scope_kind, ra.scope_ref);
      for (const p of perms) {
        mergeEntry(acc, p, {
          kind: "role",
          id: role.id,
          label: `Role ${role.name}`,
        });
      }
    }
    // 2. groups → group roles
    for (const groupId of ua.groups) {
      const group = findGroup(store, groupId);
      if (!group) continue;
      const groupRoles = store.groupAssignments[groupId] ?? [];
      for (const ra of groupRoles) {
        const role = findRole(store, ra.role_id);
        if (!role) continue;
        const perms = expandRolePermissions(role, ra.scope_kind, ra.scope_ref);
        for (const p of perms) {
          mergeEntry(acc, p, {
            kind: "group",
            id: group.id,
            label: `Group ${group.name}`,
            via_group: group.id,
          });
        }
      }
      // 2b. direct grants attached to the group — наследуются всеми членами
      for (const g of store.directGrants) {
        if (g.subject_kind !== "group" || g.subject_id !== groupId) continue;
        mergeEntry(
          acc,
          {
            permission: g.permission,
            service: deriveService(g.permission),
            scope_kind: "resource",
            scope_ref: `${g.resource_kind}:${g.resource_id}`,
          },
          {
            kind: "group",
            id: group.id,
            label: `Group ${group.name} (direct grant)`,
            via_group: group.id,
          },
        );
      }
    }
  }

  // 3. direct grants
  for (const g of store.directGrants) {
    if (g.subject_kind !== "user" || g.subject_id !== userId) continue;
    mergeEntry(
      acc,
      {
        permission: g.permission,
        service: deriveService(g.permission),
        scope_kind: "resource",
        scope_ref: `${g.resource_kind}:${g.resource_id}`,
      },
      { kind: "direct", id: g.id, label: `Direct grant ${g.resource_id}` },
    );
  }

  return acc.sort((a, b) => a.permission.localeCompare(b.permission));
}

// Compute effective permissions for a group (independent of members).
export function computeEffectiveGroup(
  groupId: string,
  store: Store = defaultStore,
): EffectiveEntry[] {
  const acc: EffectiveEntry[] = [];
  const group = findGroup(store, groupId);
  if (!group) return acc;
  const groupRoles = store.groupAssignments[groupId] ?? [];
  for (const ra of groupRoles) {
    const role = findRole(store, ra.role_id);
    if (!role) continue;
    const perms = expandRolePermissions(role, ra.scope_kind, ra.scope_ref);
    for (const p of perms) {
      mergeEntry(acc, p, { kind: "role", id: role.id, label: `Role ${role.name}` });
    }
  }
  // direct grants attached to the group itself
  for (const g of store.directGrants) {
    if (g.subject_kind !== "group" || g.subject_id !== groupId) continue;
    mergeEntry(
      acc,
      {
        permission: g.permission,
        service: deriveService(g.permission),
        scope_kind: "resource",
        scope_ref: `${g.resource_kind}:${g.resource_id}`,
      },
      { kind: "direct", id: g.id, label: `Direct grant ${g.resource_id}` },
    );
  }
  return acc.sort((a, b) => a.permission.localeCompare(b.permission));
}

// Compute effective permissions for a bot.
export function computeEffectiveBot(
  botId: string,
  store: Store = defaultStore,
): EffectiveEntry[] {
  const acc: EffectiveEntry[] = [];
  const ba = store.botAssignments[botId];
  if (ba) {
    for (const ra of ba.roles) {
      const role = findRole(store, ra.role_id);
      if (!role) continue;
      const perms = expandRolePermissions(role, ra.scope_kind, ra.scope_ref);
      for (const p of perms) {
        mergeEntry(acc, p, {
          kind: "role",
          id: role.id,
          label: `Role ${role.name}`,
        });
      }
    }
  }
  for (const g of store.directGrants) {
    if (g.subject_kind !== "bot" || g.subject_id !== botId) continue;
    mergeEntry(
      acc,
      {
        permission: g.permission,
        service: deriveService(g.permission),
        scope_kind: "resource",
        scope_ref: `${g.resource_kind}:${g.resource_id}`,
      },
      { kind: "direct", id: g.id, label: `Direct grant ${g.resource_id}` },
    );
  }
  return acc.sort((a, b) => a.permission.localeCompare(b.permission));
}

// Mutations to be evaluated by computeDiff — caller mutates a copy of store
// and we compare before vs after.
//
// `user_id` retains the historical name for backwards compatibility with existing
// callers, but actually carries the subject id; `subject` selects which assignment
// table to mutate ('user' → USER_ASSIGNMENTS, 'bot' → BOT_ASSIGNMENTS, 'group' →
// GROUP_ASSIGNMENTS). Defaults to 'user' when omitted.
export type SubjectKind = "user" | "group" | "bot";

export type Mutation =
  | { kind: "remove_role"; user_id: string; role_id: string; subject?: SubjectKind }
  | { kind: "remove_from_group"; user_id: string; group_id: string }
  | {
      kind: "add_role";
      user_id: string;
      role_id: string;
      scope_kind?: RoleAssignment["scope_kind"];
      scope_ref?: string | null;
      subject?: SubjectKind;
    }
  | { kind: "add_to_group"; user_id: string; group_id: string }
  | { kind: "remove_group"; group_id: string }
  | { kind: "remove_grant"; grant_id: string };

function cloneStore(s: Store): Store {
  return {
    groups: s.groups,
    roles: s.roles,
    userAssignments: JSON.parse(JSON.stringify(s.userAssignments)),
    groupAssignments: JSON.parse(JSON.stringify(s.groupAssignments)),
    groupMembers: JSON.parse(JSON.stringify(s.groupMembers)),
    botAssignments: JSON.parse(JSON.stringify(s.botAssignments)),
    directGrants: [...s.directGrants],
  };
}

export function applyMutation(store: Store, m: Mutation): Store {
  const s = cloneStore(store);
  switch (m.kind) {
    case "remove_role": {
      const subject = m.subject ?? "user";
      if (subject === "user") {
        const ua = s.userAssignments[m.user_id];
        if (ua) ua.roles = ua.roles.filter((r) => r.role_id !== m.role_id);
      } else if (subject === "bot") {
        const ba = s.botAssignments[m.user_id];
        if (ba) ba.roles = ba.roles.filter((r) => r.role_id !== m.role_id);
      } else {
        const ga = s.groupAssignments[m.user_id];
        if (ga) s.groupAssignments[m.user_id] = ga.filter((r) => r.role_id !== m.role_id);
      }
      break;
    }
    case "remove_from_group": {
      const ua = s.userAssignments[m.user_id];
      if (ua) ua.groups = ua.groups.filter((g) => g !== m.group_id);
      s.groupMembers[m.group_id] = (s.groupMembers[m.group_id] ?? []).filter(
        (u) => u !== m.user_id,
      );
      break;
    }
    case "add_role": {
      const subject = m.subject ?? "user";
      const ra: RoleAssignment = {
        role_id: m.role_id,
        scope_kind: m.scope_kind ?? "platform",
        scope_ref: m.scope_ref ?? null,
        granted_by: "<simulated>",
        granted_at: new Date().toISOString(),
      };
      if (subject === "user") {
        if (!s.userAssignments[m.user_id]) s.userAssignments[m.user_id] = { groups: [], roles: [] };
        s.userAssignments[m.user_id].roles.push(ra);
      } else if (subject === "bot") {
        if (!s.botAssignments[m.user_id]) s.botAssignments[m.user_id] = { groups: [], roles: [] };
        s.botAssignments[m.user_id].roles.push(ra);
      } else {
        if (!s.groupAssignments[m.user_id]) s.groupAssignments[m.user_id] = [];
        s.groupAssignments[m.user_id].push(ra);
      }
      break;
    }
    case "add_to_group": {
      if (!s.userAssignments[m.user_id]) s.userAssignments[m.user_id] = { groups: [], roles: [] };
      const ua = s.userAssignments[m.user_id];
      if (!ua.groups.includes(m.group_id)) ua.groups.push(m.group_id);
      if (!s.groupMembers[m.group_id]) s.groupMembers[m.group_id] = [];
      if (!s.groupMembers[m.group_id].includes(m.user_id)) {
        s.groupMembers[m.group_id].push(m.user_id);
      }
      break;
    }
    case "remove_group": {
      // Wipe group from every user that has it.
      for (const uid of Object.keys(s.userAssignments)) {
        s.userAssignments[uid].groups = s.userAssignments[uid].groups.filter(
          (g) => g !== m.group_id,
        );
      }
      s.groupAssignments[m.group_id] = [];
      s.groupMembers[m.group_id] = [];
      break;
    }
    case "remove_grant": {
      s.directGrants = s.directGrants.filter((g) => g.id !== m.grant_id);
      break;
    }
  }
  return s;
}

export interface DiffEntry {
  permission: string;
  scope_kind: Permission["scope_kind"];
  scope_ref: string | null;
  change: "added" | "removed" | "unchanged";
  // sources before / after the mutation
  sources_before: Source[];
  sources_after: Source[];
}

export function computeDiff(
  before: EffectiveEntry[],
  after: EffectiveEntry[],
): DiffEntry[] {
  const keyOf = (e: EffectiveEntry) =>
    `${e.permission}|${e.scope_kind}|${e.scope_ref ?? ""}`;
  const byBefore = new Map(before.map((e) => [keyOf(e), e]));
  const byAfter = new Map(after.map((e) => [keyOf(e), e]));
  const allKeys = new Set([...byBefore.keys(), ...byAfter.keys()]);
  const out: DiffEntry[] = [];
  for (const k of allKeys) {
    const b = byBefore.get(k);
    const a = byAfter.get(k);
    const ref = (b ?? a)!;
    let change: DiffEntry["change"] = "unchanged";
    if (!b && a) change = "added";
    else if (b && !a) change = "removed";
    out.push({
      permission: ref.permission,
      scope_kind: ref.scope_kind,
      scope_ref: ref.scope_ref,
      change,
      sources_before: b?.sources ?? [],
      sources_after: a?.sources ?? [],
    });
  }
  return out.sort((x, y) => {
    const rank = { added: 0, removed: 1, unchanged: 2 } as const;
    if (rank[x.change] !== rank[y.change]) return rank[x.change] - rank[y.change];
    return x.permission.localeCompare(y.permission);
  });
}

// Helper — trace one permission back through its sources, including
// group→role nesting. Each root в roots[] — независимый путь.
//
// Сценарии:
//   • role напрямую → root = { source: 'role', via undefined }
//   • group → root = { source: 'group', via: [{ source: 'role', ... }] }
//     где via содержит только те роли группы, что несут запрошенный perm.
//   • direct grant → root = { source: 'grant', via undefined }.
export function traceForUser(
  userId: string,
  permission: string,
  store: Store = defaultStore,
): Trace {
  const roots: TraceNode[] = [];
  const ua = store.userAssignments[userId];

  if (ua) {
    // Direct roles
    for (const ra of ua.roles) {
      const role = findRole(store, ra.role_id);
      if (!role) continue;
      if (role.permissions.includes(permission)) {
        roots.push({ source: "role", id: role.id, label: `Role ${role.name}` });
      }
    }
    // Groups → roles → perm
    for (const groupId of ua.groups) {
      const group = findGroup(store, groupId);
      if (!group) continue;
      const groupRoles = store.groupAssignments[groupId] ?? [];
      const matchingRoles: TraceNode[] = [];
      for (const ra of groupRoles) {
        const role = findRole(store, ra.role_id);
        if (!role) continue;
        if (role.permissions.includes(permission)) {
          matchingRoles.push({
            source: "role",
            id: role.id,
            label: `Role ${role.name}`,
          });
        }
      }
      if (matchingRoles.length > 0) {
        roots.push({
          source: "group",
          id: group.id,
          label: `Group ${group.name}`,
          via: matchingRoles,
        });
      }
    }
  }

  // Direct grants — match by exact permission string.
  for (const g of store.directGrants) {
    if (g.subject_kind !== "user" || g.subject_id !== userId) continue;
    if (g.permission === permission) {
      roots.push({ source: "grant", id: g.id, label: `Direct grant ${g.resource_id}` });
    }
  }

  return { permission, roots };
}

// Drift detection for bots — compare current effective vs. initial spec.
export interface BotDrift {
  added: string[]; // perms present now but not in initial spec
  removed: string[]; // perms in initial spec but not now
}

export function computeBotDrift(
  botId: string,
  store: Store = defaultStore,
): BotDrift {
  const bot = BOTS.find((b) => b.id === botId);
  const eff = computeEffectiveBot(botId, store);
  const current = new Set(eff.map((e) => e.permission));
  const initial = new Set(bot?.initial_spec ?? []);
  return {
    added: [...current].filter((p) => !initial.has(p)).sort(),
    removed: [...initial].filter((p) => !current.has(p)).sort(),
  };
}
