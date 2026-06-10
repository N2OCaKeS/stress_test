import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Download, Grid2x2 } from "lucide-react";
import { USERS } from "@/mocks/auth";
import { BOTS, ROLES, RESOURCES, USER_ASSIGNMENTS, type Service } from "@/mocks/permissions";
import { computeEffectiveUser, computeEffectiveBot } from "./permissionGraph";
import { useToast } from "@/contexts/ToastContext";

/**
 * UserAccessMatrices — scoped to a single user.
 *
 * Used as a tab inside UserDetail. Renders three matrices:
 *   1. This user × Resource (only resources the user has access to).
 *   2. Bots of this user's dept × Resource (or owned-by-user if available).
 *   3. Roles held by this user × Service (permissions per role/service).
 *
 * Layout: scrolls inside its own block via `.scroll-block` so UserDetail
 * stays anchored.
 */

const SERVICES: Service[] = ["auth", "server", "secret", "logging", "worker"];

interface Props {
  userId: string;
}

export function UserAccessMatrices({ userId }: Props) {
  const user = USERS.find((u) => u.id === userId);

  const effective = useMemo(() => computeEffectiveUser(userId), [userId]);
  const assignments = USER_ASSIGNMENTS[userId];
  const toast = useToast();

  // Resources to which this user has any access.
  const accessibleResources = useMemo(() => {
    const refs = new Set<string>();
    for (const e of effective) {
      if (e.scope_kind === "resource" && e.scope_ref) {
        // scope_ref like "secret:db-master" → keep tail part
        const tail = e.scope_ref.includes(":") ? e.scope_ref.split(":").slice(-1)[0] : e.scope_ref;
        refs.add(tail);
      }
      if (e.scope_kind === "dept" && user?.dept_id) {
        // dept-wide grants → include all resources of the user's dept
        for (const r of RESOURCES) {
          if (r.dept === user.dept_id) refs.add(r.id);
        }
      }
      if (e.scope_kind === "platform") {
        // platform-wide grants → all resources visible
        for (const r of RESOURCES) refs.add(r.id);
      }
    }
    return RESOURCES.filter((r) => refs.has(r.id));
  }, [effective, user?.dept_id]);

  // Bots in scope.
  //  - platform users (no dept) — see ALL bots (account_admin / multi-admin lens).
  //  - dept users — bots of own dept OR bots they personally created.
  const userBots = useMemo(() => {
    if (!user) return [];
    if (!user.dept_id) return BOTS;
    return BOTS.filter(
      (b) => b.owner_dept === user.dept_id || b.created_by === user.id,
    );
  }, [user]);

  // Service-roles held by the user (excluding platform-only).
  const heldRoles = useMemo(() => {
    if (!assignments) return [];
    return assignments.roles
      .map((ra) => ROLES.find((r) => r.id === ra.role_id))
      .filter(Boolean) as typeof ROLES;
  }, [assignments]);

  function userResourceCell(resourceId: string) {
    return effective.filter((e) => {
      if (e.scope_kind === "platform") return true;
      if (e.scope_kind === "dept" && user?.dept_id) {
        const r = RESOURCES.find((x) => x.id === resourceId);
        return r?.dept === user.dept_id;
      }
      if (e.scope_kind === "resource" && e.scope_ref) {
        return e.scope_ref.endsWith(resourceId);
      }
      return false;
    });
  }

  function botResourceCell(botId: string, resourceId: string) {
    const eff = computeEffectiveBot(botId);
    return eff.filter((e) => {
      if (e.scope_kind === "platform") return true;
      if (e.scope_kind === "resource" && e.scope_ref) return e.scope_ref.endsWith(resourceId);
      return false;
    });
  }

  function csvEscape(v: string): string {
    if (v.includes(",") || v.includes("\"") || v.includes("\n")) {
      return `"${v.replace(/"/g, '""')}"`;
    }
    return v;
  }

  function buildCsv(): string {
    if (!user) return "";
    const lines: string[] = [];
    // Matrix 1: User × Resource
    lines.push("# user_x_resource");
    const headers1 = ["subject", ...accessibleResources.map((r) => r.name)];
    lines.push(headers1.map(csvEscape).join(","));
    const row1 = [
      user.username,
      ...accessibleResources.map((r) => {
        const perms = userResourceCell(r.id);
        return perms.map((p) => p.permission).join("|");
      }),
    ];
    lines.push(row1.map(csvEscape).join(","));
    lines.push("");

    // Matrix 2: Bots × Resource
    lines.push("# bots_x_resource");
    const headers2 = ["bot", ...RESOURCES.map((r) => r.name)];
    lines.push(headers2.map(csvEscape).join(","));
    for (const b of userBots) {
      const row = [
        b.name,
        ...RESOURCES.map((r) => {
          const perms = botResourceCell(b.id, r.id);
          return perms.map((p) => p.permission).join("|");
        }),
      ];
      lines.push(row.map(csvEscape).join(","));
    }
    lines.push("");

    // Matrix 3: Roles × Service
    lines.push("# roles_x_service");
    const headers3 = ["role", ...SERVICES];
    lines.push(headers3.map(csvEscape).join(","));
    for (const r of heldRoles) {
      const row = [
        r.name,
        ...SERVICES.map((s) => {
          const perms = r.permissions.filter((p) => p.split(":")[1] === serviceDomain(s));
          return perms.join("|");
        }),
      ];
      lines.push(row.map(csvEscape).join(","));
    }
    return lines.join("\n");
  }

  function handleExportCsv() {
    if (!user) return;
    const csv = buildCsv();
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const a = document.createElement("a");
    a.href = url;
    a.download = `access-matrix-${user.id}-${ts}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success("Экспортировано");
  }

  if (!user) {
    return (
      <div className="empty-card danger m-5">
        Пользователь <span className="mono">{userId}</span> не найден.
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto relative">
      <div className="p-5 flex flex-col gap-5">
        <div className="card">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-semibold flex items-center gap-2">
              <Grid2x2 className="w-4 h-4 text-accent" />
              {user.username} × Resource
              <span className="text-xs text-dim">
                ({accessibleResources.length} ресурсов · {effective.length} effective)
              </span>
            </h3>
            <button
              type="button"
              className="btn flex items-center gap-1"
              onClick={handleExportCsv}
              title="Скачать CSV всех трёх матриц"
            >
              <Download className="w-4 h-4" />
              Экспорт CSV
            </button>
          </div>
          {accessibleResources.length === 0 ? (
            <div className="text-sm text-dim italic">
              Нет ресурсов, к которым у этого пользователя есть прямые или транзитивные права.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="text-sm border-collapse">
                <thead>
                  <tr>
                    <th className="surface-2 border border-token px-3 py-2 sticky left-0 z-10 text-left">User</th>
                    {accessibleResources.map((r) => (
                      <th key={r.id} className="surface-2 border border-token px-3 py-2 text-xs text-left">
                        <div className="mono">{r.name}</div>
                        <div className="text-[10px] text-dim">{r.kind} · {r.dept}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="surface border border-token px-3 py-2 sticky left-0 z-10">
                      <Link to={`/users/${user.id}`} className="text-sm">{user.username}</Link>
                      <div className="text-[10px] text-dim">{user.dept_id ?? "platform"}</div>
                    </td>
                    {accessibleResources.map((r) => {
                      const perms = userResourceCell(r.id);
                      return <Cell key={r.id} count={perms.length} perms={perms.map((p) => p.permission)} />;
                    })}
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-semibold flex items-center gap-2">
              <Grid2x2 className="w-4 h-4 text-accent" />
              Боты {user.dept_id ? `dept · ${user.dept_id}` : "all (platform user)"} × Resource
              <span className="text-xs text-dim">
                ({userBots.length} ботов)
              </span>
            </h3>
          </div>
          {userBots.length === 0 ? (
            <div className="text-sm text-dim italic">
              Нет ботов в зоне видимости.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="text-sm border-collapse">
                <thead>
                  <tr>
                    <th className="surface-2 border border-token px-3 py-2 sticky left-0 z-10 text-left">Bot</th>
                    {RESOURCES.map((r) => (
                      <th key={r.id} className="surface-2 border border-token px-3 py-2 text-xs text-left">
                        <div className="mono">{r.name}</div>
                        <div className="text-[10px] text-dim">{r.kind} · {r.dept}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {userBots.map((b) => (
                    <tr key={b.id}>
                      <td className="surface border border-token px-3 py-2 sticky left-0 z-10">
                        <Link to={`/users/bot/${b.id}`} className="text-sm mono">{b.name}</Link>
                        <div className="text-[10px] text-dim">{b.owner_dept}</div>
                      </td>
                      {RESOURCES.map((r) => {
                        const perms = botResourceCell(b.id, r.id);
                        return <Cell key={r.id} count={perms.length} perms={perms.map((p) => p.permission)} />;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-semibold flex items-center gap-2">
              <Grid2x2 className="w-4 h-4 text-accent" />
              Роли {user.username} × Service
              <span className="text-xs text-dim">
                ({heldRoles.length} ролей)
              </span>
            </h3>
          </div>
          {heldRoles.length === 0 ? (
            <div className="text-sm text-dim italic">
              У пользователя не назначено ни одной роли (ни платформенной, ни сервисной).
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="text-sm border-collapse">
                <thead>
                  <tr>
                    <th className="surface-2 border border-token px-3 py-2 sticky left-0 z-10 text-left">Role</th>
                    {SERVICES.map((s) => (
                      <th key={s} className="surface-2 border border-token px-3 py-2 text-xs text-left">{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {heldRoles.map((r) => (
                    <tr key={r.id}>
                      <td className="surface border border-token px-3 py-2 sticky left-0 z-10">
                        <div className="text-sm">{r.name}</div>
                        <div className="text-[10px] text-dim">{r.service} · {r.scope}</div>
                      </td>
                      {SERVICES.map((s) => {
                        const perms = r.permissions.filter((p) => p.split(":")[1] === serviceDomain(s));
                        return <Cell key={s} count={perms.length} perms={perms} />;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function serviceDomain(s: Service): string {
  switch (s) {
    case "logging":
      return "audit";
    case "auth":
      return "user";
    default:
      return s;
  }
}

function Cell({ count, perms }: { count: number; perms: string[] }) {
  const color =
    count === 0 ? "text-dim" : count <= 2 ? "text-ok" : count <= 5 ? "text-warn" : "text-danger";
  return (
    <td
      className="border border-token px-3 py-2 text-center"
      title={perms.join("\n")}
    >
      <span className={`text-sm font-medium ${color}`}>{count || "—"}</span>
    </td>
  );
}
