import { useState } from "react";
import {
  Search,
  User,
  Building2,
  HardDrive,
  Terminal,
  Zap,
  Database,
  Box,
  Cog,
  HeartPulse,
  RefreshCw,
  XCircle,
  Eye,
  Server,
  Bot,
  Clock,
  CheckCircle,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Shell } from "@/components/shell/Shell";
import { statusBadge, type TaskStatus } from "./workerShared";

interface Row {
  id: string;
  icon: LucideIcon;
  iconColor: string;
  title: string;
  target: string;
  meta: { text: string; hb?: boolean }[];
  status: TaskStatus;
}

const MY: Row[] = [
  { id: "tsk_aa12_disk_snapshot_n01", icon: HardDrive, iconColor: "text-ok", title: "tsk_aa12...disk_snapshot", target: "srv-node-01", meta: [{ text: "15:42:08" }, { text: "2.1s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_aa12_disk_snapshot_n05", icon: HardDrive, iconColor: "text-ok", title: "tsk_aa12_disk_snapshot", target: "srv-node-05", meta: [{ text: "15:38:11" }, { text: "3.2s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_bb23_ssh_exec", icon: Terminal, iconColor: "text-ok", title: "tsk_bb23...ssh_exec", target: "srv-node-12", meta: [{ text: "15:30:22" }, { text: "0.9s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_cc34_power_status", icon: Zap, iconColor: "text-warn", title: "tsk_cc34...power_status", target: "srv-node-17", meta: [{ text: "start 15:27:42" }, { text: "4с", hb: true }, { text: "try 1/3" }], status: "RUN" },
];

const MY_DEP: Row[] = [
  { id: "tsk_dd45_ssh_uptime", icon: Terminal, iconColor: "text-accent", title: "tsk_dd45...ssh_uptime", target: "srv-jira-db-01", meta: [{ text: "15:41:48" }, { text: "1с", hb: true }, { text: "by bob" }], status: "RUN" },
  { id: "tsk_ee56_pg_query", icon: Database, iconColor: "text-accent", title: "tsk_ee56...pg_query", target: "srv-db-master-01", meta: [{ text: "15:40:22" }, { text: "4с", hb: true }, { text: "by dave" }], status: "RUN" },
  { id: "tsk_ff67_disk_smart", icon: HardDrive, iconColor: "text-accent", title: "tsk_ff67...disk_smart", target: "srv-node-12", meta: [{ text: "15:38:01" }, { text: "3с", hb: true }, { text: "by ci_bot" }], status: "RUN" },
  { id: "tsk_0078_power_cycle", icon: Zap, iconColor: "text-dim", title: "tsk_0078...power_cycle", target: "srv-node-04-old", meta: [{ text: "queued 15:43:02" }, { text: "by bob" }], status: "Q" },
  { id: "tsk_1189_apt_update", icon: Box, iconColor: "text-dim", title: "tsk_1189...apt_update", target: "srv-build-02", meta: [{ text: "queued 15:42:51" }, { text: "by grace" }], status: "Q" },
  { id: "tsk_229a_power_status", icon: Zap, iconColor: "text-ok", title: "tsk_229a...power_status", target: "srv-node-02", meta: [{ text: "15:35:05" }, { text: "0.4s" }, { text: "by henry" }], status: "OK" },
  { id: "tsk_33ab_apt_install", icon: Box, iconColor: "text-ok", title: "tsk_33ab...apt_install", target: "srv-build-01", meta: [{ text: "15:30:42" }, { text: "22s" }, { text: "by ci_bot" }], status: "OK" },
  { id: "tsk_44bc_pg_vacuum", icon: Database, iconColor: "text-ok", title: "tsk_44bc...pg_vacuum", target: "srv-db-replica-01", meta: [{ text: "15:27:18" }, { text: "1m 4s" }, { text: "by cron" }], status: "OK" },
  { id: "tsk_55cd_disk_smart", icon: HardDrive, iconColor: "text-ok", title: "tsk_55cd...disk_smart", target: "srv-edge-02", meta: [{ text: "15:22:01" }, { text: "2.1s" }, { text: "by audit_bot" }], status: "OK" },
  { id: "tsk_66de_install_kernel", icon: Box, iconColor: "text-danger", title: "tsk_66de...install_kernel", target: "srv-node-17", meta: [{ text: "15:12:08" }, { text: "—" }, { text: "try 2/3" }], status: "FAIL" },
  { id: "tsk_77ef_ssh_exec", icon: Terminal, iconColor: "text-warn", title: "tsk_77ef...ssh_exec", target: "srv-edge-03", meta: [{ text: "backoff 15s" }, { text: "try 2/3" }], status: "RTY" },
  { id: "tsk_88f0_ssh_uptime", icon: Terminal, iconColor: "text-ok", title: "tsk_88f0...ssh_uptime", target: "srv-conf-01", meta: [{ text: "15:01:09" }, { text: "0.9s" }, { text: "by monitor_bot" }], status: "OK" },
  { id: "tsk_9901_pg_dump", icon: Database, iconColor: "text-ok", title: "tsk_9901...pg_dump", target: "srv-conf-db-01", meta: [{ text: "14:55:18" }, { text: "14s" }, { text: "by cron" }], status: "OK" },
  { id: "tsk_aa12_power_off", icon: Zap, iconColor: "text-ok", title: "tsk_aa12...power_off", target: "srv-node-04-old", meta: [{ text: "14:50:01" }, { text: "1.8s" }, { text: "by bob" }], status: "OK" },
];

const CROSS_DEP: Row[] = [
  { id: "tsk_bb23_disk_smart_cross", icon: HardDrive, iconColor: "text-dim", title: "tsk_bb23...disk_smart", target: "srv-jira-db-01", meta: [{ text: "summary only" }, { text: "by audit_bot" }], status: "summ" },
  { id: "tsk_cc34_pg_query_cross", icon: Database, iconColor: "text-dim", title: "tsk_cc34...pg_query", target: "srv-edge-dtkk-04", meta: [{ text: "summary only" }, { text: "cross-dept rule" }], status: "summ" },
];

export function WorkerDepAdmin() {
  const [selected, setSelected] = useState<string>("tsk_aa12_disk_snapshot_n05");

  return (
    <Shell breadcrumb="server_worker / tasks">
      <WorkerTaskList
        groups={[
          { title: "my", icon: User, count: 4, rows: MY },
          { title: "my_dep · Ядро DBOS", icon: Building2, count: 14, rows: MY_DEP },
          { title: "cross_dep", icon: Building2, count: 2, rows: CROSS_DEP },
        ]}
        selected={selected}
        onSelect={setSelected}
        searchPlaceholder="Поиск по 20 task'ам..."
        countLabel="20 / 20"
        scopeOptions={["scope (my / my_dep / cross_dep)", "by status", "by type"]}
      />
      <DiskSnapshotDetail />
    </Shell>
  );
}

interface ListGroup {
  title: string;
  icon: LucideIcon;
  count: number;
  rows: Row[];
}

interface WorkerTaskListProps {
  groups: ListGroup[];
  selected: string;
  onSelect: (id: string) => void;
  searchPlaceholder: string;
  countLabel: string;
  scopeOptions: string[];
  extraFilters?: { options: string[] }[];
}

export function WorkerTaskList({
  groups,
  selected,
  onSelect,
  searchPlaceholder,
  countLabel,
  scopeOptions,
  extraFilters,
}: WorkerTaskListProps) {
  return (
    <section className="w-[380px] shrink-0 border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={searchPlaceholder}
          />
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
          <span>Группировка:</span>
          <select className="surface-2 border border-token rounded px-2 py-0.5">
            {scopeOptions.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
          <span className="ml-auto">{countLabel}</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-xs flex-wrap">
          <select className="surface-2 border border-token rounded px-2 py-0.5 text-dim">
            {[
              "status: all",
              "running",
              "queued",
              "succeeded",
              "failed",
              "retrying",
            ].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <select className="surface-2 border border-token rounded px-2 py-0.5 text-dim">
            {["type: all", "power", "ssh", "package", "db", "disk"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          {extraFilters?.map((f, i) => (
            <select
              key={i}
              className="surface-2 border border-token rounded px-2 py-0.5 text-dim"
            >
              {f.options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {groups.map((g, gi) => {
          const Icon = g.icon;
          return (
            <div key={g.title}>
              <div
                className={`group-header flex items-center gap-2 ${gi === 0 ? "" : "mt-3"}`}
              >
                <Icon className="w-3 h-3" /> {g.title} · {g.count}
              </div>
              {g.rows.length === 0 ? (
                <div className="px-3 py-2 text-xs text-dim italic">
                  пусто
                </div>
              ) : (
                <div className="px-2 flex flex-col gap-0.5">
                  {g.rows.map((r) => {
                    const RowIcon = r.icon;
                    return (
                      <button
                        key={r.id}
                        onClick={() => onSelect(r.id)}
                        className={`cred-row text-left ${
                          selected === r.id ? "active" : ""
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <RowIcon className={`w-4 h-4 ${r.iconColor}`} />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm truncate mono">
                              {r.title}{" "}
                              <span className="text-dim">→ {r.target}</span>
                            </div>
                            <div className="text-[11px] text-dim flex items-center gap-2 flex-wrap">
                              {r.meta.map((m, i) => (
                                <span key={i} className="flex items-center gap-1">
                                  {m.hb && (
                                    <HeartPulse className="w-3 h-3 inline" />
                                  )}
                                  {m.text}
                                  {i < r.meta.length - 1 && <span>·</span>}
                                </span>
                              ))}
                            </div>
                          </div>
                          <span className={statusBadge(r.status)}>
                            {r.status}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="border-t border-token p-2 flex gap-2">
        <button className="btn flex-1 flex items-center justify-center gap-2">
          <RefreshCw className="w-4 h-4" /> Re-run selected
        </button>
        <button className="btn btn-danger flex-1 flex items-center justify-center gap-2">
          <XCircle className="w-4 h-4" /> Cancel selected
        </button>
      </div>
    </section>
  );
}

function DiskSnapshotDetail() {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <HardDrive className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">
              tsk_aa12_disk_snapshot
            </h1>
            <span className="badge badge-ok">SUCCEEDED</span>
            <span className="text-xs text-dim">
              try <b>1 / 3</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span>
              <Server className="w-3 h-3 inline" /> target:{" "}
              <Link to="/server" className="text-accent mono">
                srv-node-05
              </Link>
            </span>
            <span>·</span>
            <span>
              <Bot className="w-3 h-3 inline" /> worker:{" "}
              <b className="mono">wrk-01</b>
            </span>
            <span>·</span>
            <span>
              dept: <b>Ядро DBOS</b>
            </span>
            <span>·</span>
            <span>
              actor: <b>alice</b> (вы)
            </span>
            <span>·</span>
            <span>
              started: <b>15:38:11</b>
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="btn btn-primary flex items-center gap-1">
            <RefreshCw className="w-4 h-4" /> Re-run
          </button>
          <button className="btn flex items-center gap-1">
            <Eye className="w-4 h-4" /> View full log
          </button>
        </div>
      </div>

      <div className="border-b border-token px-5 flex gap-1">
        <button
          className="px-3 py-2 text-sm border-b-2 -mb-px"
          style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
        >
          Overview
        </button>
        {["Execution log", "Heartbeats", "Audit"].map((t) => (
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
            <Cog className="w-4 h-4" /> Parameters
          </div>
          <pre className="mono text-xs surface-2 border border-token rounded p-3 overflow-x-auto leading-relaxed whitespace-pre">{`{
  "type": "disk.snapshot",
  "device": "/dev/sda",
  "label": "pre-kernel-update",
  "freeze_fs": true,
  "timeout_s": 120,
  "retry_policy": {
    "max": 3,
    "backoff": "exponential",
    "base_s": 15
  },
  "requested_by": "alice",
  "request_id": "req_aa12_disk_snap"
}`}</pre>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Server className="w-4 h-4" /> Target (1)
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">Сервер</th>
                <th className="pb-2 pr-3">dept</th>
                <th className="pb-2">Статус</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-token">
                <td className="py-2 mono">
                  <Link to="/server" className="text-accent">
                    srv-node-05
                  </Link>
                </td>
                <td>Ядро DBOS</td>
                <td>
                  <span className="badge badge-ok">SUCCEEDED</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div className="mt-3 text-xs text-dim">
            Snapshot создан перед плановой выкаткой kernel update — fall-back при
            сбое.
          </div>
        </div>

        <div className="col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-ok" /> Result
          </div>
          <pre className="mono text-xs surface-2 border border-token rounded p-3 overflow-x-auto leading-relaxed whitespace-pre">{`snapshot_id: snap-2026-06-10-1538-srv-node-05-sda
size_bytes: 47 482 393 856  (44.2 GiB)
device: /dev/sda
created_at: 2026-06-10T15:38:14Z
fs_frozen: true
duration_s: 3.2
checksum_sha256: 8a9c3f2e1d4b...`}</pre>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Clock className="w-4 h-4" /> Timeline
          </div>
          <div className="text-sm">
            {[
              { c: "ok", t: "queued", ts: "15:38:11" },
              { c: "ok", t: "dispatched → worker wrk-01", ts: "15:38:11" },
              { c: "ok", t: "fs_freeze: /", ts: "15:38:12" },
              { c: "accent", t: "heartbeat — running", ts: "15:38:13", hb: true },
              { c: "ok", t: "snapshot created", ts: "15:38:14" },
              { c: "ok", t: "fs_unfreeze: /", ts: "15:38:14" },
              { c: "ok", t: "succeeded (3.2s)", ts: "15:38:14" },
            ].map((s, i) => (
              <div key={i} className="timeline-step">
                <span className={`dot ${s.c}`} />
                <span>
                  {s.hb && (
                    <HeartPulse className="w-3 h-3 inline text-accent mr-1" />
                  )}
                  {s.t}
                </span>
                <span className="text-xs text-dim mono">{s.ts}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <RefreshCw className="w-4 h-4" /> Related tasks
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">Роль</th>
                <th className="pb-2 pr-3">Task ID</th>
                <th className="pb-2 pr-3">Тип</th>
                <th className="pb-2 pr-3">Статус</th>
                <th className="pb-2">Время</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-token">
                <td className="py-2 text-dim">predecessor</td>
                <td className="mono">
                  <a href="#" className="text-accent">
                    tsk_9988_apt_update
                  </a>
                </td>
                <td>package.update</td>
                <td>
                  <span className="badge badge-ok">OK</span>
                </td>
                <td className="text-dim text-xs mono">15:36:55</td>
              </tr>
              <tr
                className="border-t border-token"
                style={{ background: "var(--selected)" }}
              >
                <td className="py-2 text-dim">current</td>
                <td className="mono">tsk_aa12_disk_snapshot</td>
                <td>disk.snapshot</td>
                <td>
                  <span className="badge badge-ok">OK</span>
                </td>
                <td className="text-dim text-xs mono">15:38:11</td>
              </tr>
              <tr className="border-t border-token">
                <td className="py-2 text-dim">successor</td>
                <td className="mono">
                  <a href="#" className="text-accent">
                    tsk_66de_install_kernel
                  </a>
                </td>
                <td>package.install</td>
                <td>
                  <span className="badge badge-danger">FAILED</span>
                </td>
                <td className="text-dim text-xs mono">15:12:08</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
