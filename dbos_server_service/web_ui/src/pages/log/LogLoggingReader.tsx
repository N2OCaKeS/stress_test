import { useState } from "react";
import {
  TriangleAlert,
  ShieldAlert,
  Download,
  Filter,
  Cog,
  Share2,
  User,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { LogMiddle } from "./LogLoggingAdmin";

/**
 * Logging-reader log view.
 * Read-only banner + simplified workzone (only Export JSON action).
 */
export function LogLoggingReader() {
  const [selected, setSelected] = useState<string>("ev-003");

  return (
    <Shell breadcrumb="loging_service / events">
      <div className="flex flex-col flex-1 min-w-0">
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · loging_reader доступ.</b> Создавать ruleset, помечать
            события и менять retention нельзя. Доступен только просмотр и
            экспорт JSON.
          </span>
        </div>
        <div className="flex-1 flex min-h-0">
          <LogMiddle
            selected={selected}
            onSelect={setSelected}
            live={false}
            onLiveToggle={() => {}}
            showLive={false}
          />
          <section className="flex-1 overflow-hidden flex flex-col min-w-0">
            <div className="border-b border-token p-5 flex items-start gap-4">
              <div
                className="w-12 h-12 rounded flex items-center justify-center"
                style={{
                  background: "var(--warn-bg)",
                  color: "var(--warn-fg)",
                }}
              >
                <TriangleAlert className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3 flex-wrap">
                  <h1 className="text-xl font-semibold truncate mono">
                    user.login_failed
                  </h1>
                  <span className="sev sev-WARNING">WARNING</span>
                  <span className="ro-label">read-only</span>
                </div>
                <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
                  <span className="mono">req_f3b2c91a8e7d4022</span>
                  <span>·</span>
                  <span className="mono">2026-06-10 15:40:32.118 UTC</span>
                  <span>·</span>
                  <span>3 минуты назад</span>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button className="btn btn-primary flex items-center gap-1">
                  <Download className="w-4 h-4" /> Export JSON
                </button>
              </div>
            </div>

            <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
              <div className="surface border border-token rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  <User className="w-4 h-4 text-accent" />
                  <div className="text-xs uppercase tracking-wider text-dim">
                    Actor
                  </div>
                </div>
                <div className="text-sm">
                  <div className="stat-row">
                    <span className="text-dim">Username</span>
                    <span>
                      <i>unknown</i>
                    </span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Attempted</span>
                    <span className="mono">igor</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">IP</span>
                    <span className="mono">10.20.30.4</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">User-Agent</span>
                    <span className="text-xs">Chrome/124.0 · Linux</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Identity type</span>
                    <span>
                      <span className="badge">anonymous</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="surface border border-token rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  <User className="w-4 h-4 text-warn" />
                  <div className="text-xs uppercase tracking-wider text-dim">
                    Target
                  </div>
                </div>
                <div className="text-sm">
                  <div className="stat-row">
                    <span className="text-dim">Type</span>
                    <span>
                      <span className="badge">user</span>
                    </span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">ID</span>
                    <span className="mono text-xs">usr_e5d8c91a4b227710</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Name</span>
                    <span>igor</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Department</span>
                    <span>ДТКК</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Status</span>
                    <span>
                      <span className="badge badge-danger">blocked</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="surface border border-token rounded-lg p-4 col-span-2">
                <div className="flex items-center gap-2 mb-3">
                  <Filter className="w-4 h-4 text-dim" />
                  <div className="text-xs uppercase tracking-wider text-dim">
                    Details JSON
                  </div>
                </div>
                <pre className="json-block mono whitespace-pre">{`{
  "event_id": "evt_42c7e801f9a14bc2",
  "action": "user.login_failed",
  "severity": "WARNING",
  "status": "failure",
  "reason": "invalid_credentials",
  "attempt_number": 3,
  "lockout_threshold": 5,
  "source_ip": "10.20.30.4",
  "geo_country": "RU",
  "will_block_at_attempt": 5
}`}</pre>
              </div>

              <div className="surface border border-token rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Cog className="w-4 h-4 text-dim" />
                  <div className="text-xs uppercase tracking-wider text-dim">
                    Service
                  </div>
                </div>
                <div className="text-sm">
                  <div className="stat-row">
                    <span className="text-dim">Emitter</span>
                    <span>
                      <span className="badge badge-warn">auth_service</span>
                    </span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">X-Service-Identity</span>
                    <span className="mono text-xs">
                      auth_service@dbos-srv-01
                    </span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">X-Request-ID</span>
                    <span className="mono text-xs">req_f3b2c91a8e7d4022</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Version</span>
                    <span className="mono">1.5.2</span>
                  </div>
                  <div className="stat-row">
                    <span className="text-dim">Trace</span>
                    <span className="mono text-xs text-accent">
                      trace_9d2a17b0
                    </span>
                  </div>
                </div>
              </div>

              <div className="surface border border-token rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Share2 className="w-4 h-4 text-dim" />
                  <div className="text-xs uppercase tracking-wider text-dim">
                    Related events
                  </div>
                  <span className="text-[10px] text-dim ml-auto">
                    по request_id
                  </span>
                </div>
                <table className="w-full related-tbl">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Action</th>
                      <th>Sev</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { t: "15:38:14", a: "user.login_failed", s: "INFO" },
                      { t: "15:39:23", a: "user.login_failed", s: "INFO" },
                      { t: "15:40:32", a: "user.login_failed", s: "WARNING" },
                      { t: "15:40:32", a: "auth.rate_limit_hit", s: "WARNING" },
                      { t: "15:40:33", a: "auth.captcha_required", s: "INFO" },
                    ].map((r) => (
                      <tr key={r.t + r.a}>
                        <td className="mono">{r.t}</td>
                        <td className="mono">{r.a}</td>
                        <td>
                          <span className={`sev sev-${r.s}`}>
                            {r.s === "WARNING" ? "WARN" : r.s}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>
      </div>
    </Shell>
  );
}
