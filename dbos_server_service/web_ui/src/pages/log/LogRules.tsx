import { useState } from "react";
import {
  Search,
  Filter,
  AlertTriangle,
  EyeOff,
  Bookmark,
  Share2,
  Play,
  Edit3,
  Trash2,
  GitBranch,
  Clock,
  User,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";

interface Rule {
  id: string;
  group: string;
  pattern: string;
  detail: string;
  badge: string;
  badgeKind: "danger" | "warn" | "";
  status: "active" | "muted";
}

const RULES: Rule[] = [
  // severity-override
  { id: "r1", group: "severity-override", pattern: "secret.master_key_rotated", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "active" },
  { id: "r2", group: "severity-override", pattern: "auth.login_failed.*", detail: "WARN", badge: "WARN", badgeKind: "warn", status: "active" },
  { id: "r3", group: "severity-override", pattern: "auth.brute_force_detected", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "active" },
  { id: "r4", group: "severity-override", pattern: "server.deleted", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "active" },
  { id: "r5", group: "severity-override", pattern: "secret.revoked", detail: "WARN", badge: "WARN", badgeKind: "warn", status: "active" },
  { id: "r6", group: "severity-override", pattern: "user.banned", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "active" },
  { id: "r7", group: "severity-override", pattern: "migration.failed", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "muted" },
  { id: "r8", group: "severity-override", pattern: "audit.chain_break", detail: "CRITICAL", badge: "CRITICAL", badgeKind: "danger", status: "active" },
  // suppression
  { id: "r9", group: "suppression", pattern: "healthcheck.*", detail: "healthchecks drop", badge: "", badgeKind: "", status: "active" },
  { id: "r10", group: "suppression", pattern: "worker.heartbeat", detail: "noisy", badge: "", badgeKind: "", status: "active" },
  { id: "r11", group: "suppression", pattern: "metrics.scrape", detail: "prom-scrape", badge: "", badgeKind: "", status: "active" },
  { id: "r12", group: "suppression", pattern: "cache.miss", detail: "noisy debug", badge: "", badgeKind: "", status: "active" },
  { id: "r13", group: "suppression", pattern: "session.refresh", detail: "не интересно", badge: "", badgeKind: "", status: "active" },
  { id: "r14", group: "suppression", pattern: "debug.trace.*", detail: "debug-канал off", badge: "", badgeKind: "", status: "muted" },
  // mark-as-known
  { id: "r15", group: "mark-as-known", pattern: "test.dry_run.*", detail: "CI smoke", badge: "", badgeKind: "", status: "active" },
  { id: "r16", group: "mark-as-known", pattern: "cert.renewed", detail: "acme bot", badge: "", badgeKind: "", status: "active" },
  { id: "r17", group: "mark-as-known", pattern: "backup.completed", detail: "nightly", badge: "", badgeKind: "", status: "active" },
  { id: "r18", group: "mark-as-known", pattern: "cron.tick", detail: "CronJob heartbeat", badge: "", badgeKind: "", status: "active" },
  { id: "r19", group: "mark-as-known", pattern: "rotation.scheduled", detail: "plan event", badge: "", badgeKind: "", status: "active" },
  // forward
  { id: "r20", group: "forward-to-extern", pattern: "CRITICAL → siem", detail: "syslog/tcp", badge: "", badgeKind: "", status: "active" },
  { id: "r21", group: "forward-to-extern", pattern: "user.banned → siem", detail: "syslog/tcp", badge: "", badgeKind: "", status: "active" },
  { id: "r22", group: "forward-to-extern", pattern: "secret.* → confluence", detail: "по запросу ИБ", badge: "", badgeKind: "", status: "active" },
  { id: "r23", group: "forward-to-extern", pattern: "audit.chain_break → siem", detail: "P0", badge: "", badgeKind: "", status: "active" },
  { id: "r24", group: "forward-to-extern", pattern: "migration.failed → ops_slack", detail: "webhook", badge: "", badgeKind: "", status: "active" },
];

const GROUP_META: Record<string, { count: number; icon: typeof Filter }> = {
  "severity-override": { count: 8, icon: AlertTriangle },
  suppression: { count: 6, icon: EyeOff },
  "mark-as-known": { count: 5, icon: Bookmark },
  "forward-to-extern": { count: 5, icon: Share2 },
};

export function LogRules() {
  const mockMode = useMockMode();
  const [selected, setSelected] = useState<string>("r1");
  const groups = Array.from(new Set(RULES.map((r) => r.group)));

  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="loging_service / rules"
        service="loging_service (rules)"
        endpoints={[
          "GET   /loging/v1/rules",
          "POST  /loging/v1/rules",
          "PATCH /loging/v1/rules/{id}",
        ]}
      />
    );
  }

  return (
    <Shell breadcrumb="loging_service / rules">
      <section className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск правил..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
            <Filter className="w-3 h-3" />
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>все типы</option>
              <option>severity-override</option>
              <option>suppression</option>
              <option>mark-as-known</option>
              <option>forward</option>
            </select>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>active</option>
              <option>muted</option>
            </select>
            <span className="ml-auto">24 шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {groups.map((g) => {
            const meta = GROUP_META[g];
            const Icon = meta.icon;
            return (
              <div key={g}>
                <div className="group-header flex items-center gap-2 mt-3 first:mt-0">
                  <Icon className="w-3 h-3" /> {g} · {meta.count}
                </div>
                <div className="px-2 flex flex-col gap-0.5">
                  {RULES.filter((r) => r.group === g).map((r) => (
                    <button
                      key={r.id}
                      onClick={() => setSelected(r.id)}
                      className={`cred-row text-left ${
                        selected === r.id ? "active" : ""
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <Filter
                          className={`w-4 h-4 ${
                            selected === r.id ? "text-accent" : "text-dim"
                          }`}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate mono">
                            {r.pattern}
                          </div>
                          <div className="text-[11px] text-dim flex items-center gap-2">
                            {r.badge ? (
                              <>
                                →{" "}
                                <span
                                  className={`badge${r.badgeKind ? ` badge-${r.badgeKind}` : ""}`}
                                >
                                  {r.badge}
                                </span>
                              </>
                            ) : (
                              r.detail
                            )}
                          </div>
                        </div>
                        <span
                          className={`badge ${
                            r.status === "active"
                              ? "badge-ok"
                              : "badge-warn"
                          }`}
                        >
                          {r.status}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Filter className="w-4 h-4" /> Создать правило
          </button>
        </div>
      </section>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div className="w-12 h-12 rounded surface-2 border border-token flex items-center justify-center">
            <Filter className="w-6 h-6 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">
                severity-override · secret.master_key_rotated
              </h1>
              <span className="badge badge-ok">active</span>
              <span className="badge badge-danger">→ CRITICAL</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span>pattern:</span>
              <span className="mono">^secret\.master_key_rotated$</span>
            </div>
            <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <User className="w-3 h-3" /> создал{" "}
                <span className="mono">carol</span>
              </span>
              <span>·</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" /> 2026-02-09
              </span>
              <span>·</span>
              <span>last_match 2 ч назад</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <button className="btn flex items-center gap-1">
              <Play className="w-4 h-4" /> Test
            </button>
            <button className="btn flex items-center gap-1">
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn flex items-center gap-1">
              <EyeOff className="w-4 h-4" /> Mute
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        </div>

        <div className="border-b border-token px-5 flex gap-1 flex-wrap">
          <button
            className="px-3 py-2 text-sm border-b-2 -mb-px"
            style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
          >
            Definition
          </button>
          {["Test", "History", "Audit"].map((t) => (
            <button
              key={t}
              className="px-3 py-2 text-sm border-b-2 -mb-px border-transparent text-dim hover-bg"
            >
              {t}
            </button>
          ))}
        </div>

        <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
          <div className="surface border border-token rounded-lg p-4">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <Search className="w-4 h-4" /> Pattern
            </div>
            <div className="surface-2 border border-token rounded p-3 mono text-sm">
              action.match:{" "}
              <span className="text-accent">^secret\.master_key_rotated$</span>
            </div>
            <div className="text-xs text-dim mt-2">
              regex по полю <span className="mono">action</span> · case-sensitive
            </div>
          </div>

          <div className="surface border border-token rounded-lg p-4">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" /> Action
            </div>
            <div className="text-sm">
              <div className="stat-row">
                <span className="text-dim">type</span>
                <span className="mono">set_severity</span>
              </div>
              <div className="stat-row">
                <span className="text-dim">value</span>
                <span>
                  <span className="badge badge-danger">CRITICAL</span>
                </span>
              </div>
              <div className="stat-row">
                <span className="text-dim">tag</span>
                <span className="mono">key-rotation</span>
              </div>
            </div>
          </div>

          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <GitBranch className="w-4 h-4" /> Conditions
            </div>
            <div className="grid grid-cols-2 gap-x-6 text-sm">
              <div>
                <div className="stat-row">
                  <span className="text-dim">status</span>
                  <span className="mono">success</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">service</span>
                  <span className="mono">secret_service</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">dept</span>
                  <span className="text-dim">любой</span>
                </div>
              </div>
              <div>
                <div className="stat-row">
                  <span className="text-dim">rate_limit</span>
                  <span>нет</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">window</span>
                  <span>—</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">priority</span>
                  <span>50</span>
                </div>
              </div>
            </div>
          </div>

          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4" /> Last 5 matches
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">time</th>
                  <th className="pb-2 pr-3">actor</th>
                  <th className="pb-2 pr-3">dept</th>
                  <th className="pb-2 pr-3">target</th>
                  <th className="pb-2">severity</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { time: "2 ч назад", actor: "igor", dept: "ДТКК", target: "cred_8f2a1c" },
                  { time: "8 ч назад", actor: "mira", dept: "—", target: "cred_7d9b3e" },
                  { time: "1 дн назад", actor: "rachel", dept: "—", target: "cred_4a1f8c" },
                  { time: "3 дн назад", actor: "igor", dept: "ДТКК", target: "cred_6c2d9a" },
                  { time: "7 дн назад", actor: "mira", dept: "—", target: "cred_1e8b4f" },
                ].map((m, i) => (
                  <tr key={i} className="border-t border-token">
                    <td className="py-2 text-dim text-xs">{m.time}</td>
                    <td className="mono">{m.actor}</td>
                    <td>{m.dept}</td>
                    <td className="mono text-xs">{m.target}</td>
                    <td>
                      <span className="badge badge-danger">CRITICAL</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </Shell>
  );
}
