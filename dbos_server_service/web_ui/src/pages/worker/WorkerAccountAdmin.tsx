import { useState } from "react";
import {
  Play,
  Clock,
  CheckCircle,
  XCircle,
  RefreshCw,
  AlertTriangle,
  HardDrive,
  Terminal,
  Zap,
  Database,
  Box,
  Cog,
  HeartPulse,
  Server,
  Bot,
  Eye,
  type LucideIcon,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { WorkerTaskList } from "./WorkerDepAdmin";
import type { TaskStatus } from "./workerShared";

interface Row {
  id: string;
  icon: LucideIcon;
  iconColor: string;
  title: string;
  target: string;
  meta: { text: string; hb?: boolean }[];
  status: TaskStatus;
}

const RUNNING: Row[] = [
  { id: "tsk_4a91_power_cycle", icon: Zap, iconColor: "text-warn", title: "tsk_4a91...power_cycle", target: "srv-node-05", meta: [{ text: "start 15:42:11" }, { text: "2с", hb: true }, { text: "try 1/3" }], status: "RUN" },
  { id: "tsk_5b02_ssh_exec", icon: Terminal, iconColor: "text-accent", title: "tsk_5b02...ssh_exec", target: "srv-jira-01", meta: [{ text: "15:41:48" }, { text: "1с", hb: true }, { text: "try 1/3" }], status: "RUN" },
  { id: "tsk_6c13_pg_query", icon: Database, iconColor: "text-accent", title: "tsk_6c13...pg_query", target: "srv-db-master-01", meta: [{ text: "15:40:22" }, { text: "4с", hb: true }, { text: "try 1/3" }], status: "RUN" },
  { id: "tsk_7d24_disk_smart", icon: HardDrive, iconColor: "text-accent", title: "tsk_7d24...disk_smart", target: "srv-node-12", meta: [{ text: "15:38:01" }, { text: "3с", hb: true }, { text: "try 1/3" }], status: "RUN" },
  { id: "tsk_9e35_apt_upgrade", icon: Box, iconColor: "text-accent", title: "tsk_9e35...apt_upgrade", target: "srv-build-02", meta: [{ text: "15:33:17" }, { text: "6с", hb: true }, { text: "try 1/3" }], status: "RUN" },
];

const QUEUED: Row[] = [
  { id: "tsk_a046_power_off", icon: Zap, iconColor: "text-dim", title: "tsk_a046...power_off", target: "srv-node-04-old", meta: [{ text: "queued 15:43:02" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_b157_install_pkg", icon: Box, iconColor: "text-dim", title: "tsk_b157...install_pkg", target: "srv-edge-01", meta: [{ text: "queued 15:42:51" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_c268_ssh_exec", icon: Terminal, iconColor: "text-dim", title: "tsk_c268...ssh_exec", target: "srv-edge-02", meta: [{ text: "queued 15:42:40" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_d379_ssh_exec", icon: Terminal, iconColor: "text-dim", title: "tsk_d379...ssh_exec", target: "srv-edge-03", meta: [{ text: "queued 15:42:39" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_e48a_pg_dump", icon: Database, iconColor: "text-dim", title: "tsk_e48a...pg_dump", target: "srv-conf-db-01", meta: [{ text: "queued 15:42:01" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_f59b_disk_resize", icon: HardDrive, iconColor: "text-dim", title: "tsk_f59b...disk_resize", target: "srv-mon-db-01", meta: [{ text: "queued 15:41:52" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_06ac_apt_install", icon: Box, iconColor: "text-dim", title: "tsk_06ac...apt_install", target: "srv-wiki-01", meta: [{ text: "queued 15:41:45" }, { text: "try 0/3" }], status: "Q" },
  { id: "tsk_17bd_power_status", icon: Zap, iconColor: "text-dim", title: "tsk_17bd...power_status", target: "srv-node-02", meta: [{ text: "queued 15:41:30" }, { text: "try 0/3" }], status: "Q" },
];

const SUCCEEDED: Row[] = [
  { id: "tsk_28ce_power_cycle", icon: Zap, iconColor: "text-ok", title: "tsk_28ce...power_cycle", target: "srv-node-01", meta: [{ text: "15:38:11" }, { text: "3.2s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_39df_ssh_uptime", icon: Terminal, iconColor: "text-ok", title: "tsk_39df...ssh_uptime", target: "srv-jira-db-01", meta: [{ text: "15:35:05" }, { text: "0.8s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_4ae0_apt_update", icon: Box, iconColor: "text-ok", title: "tsk_4ae0...apt_update", target: "srv-build-01", meta: [{ text: "15:30:42" }, { text: "12.4s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_5bf1_pg_vacuum", icon: Database, iconColor: "text-ok", title: "tsk_5bf1...pg_vacuum", target: "srv-db-replica-01", meta: [{ text: "15:27:18" }, { text: "1m 4s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_6c02_disk_smart", icon: HardDrive, iconColor: "text-ok", title: "tsk_6c02...disk_smart", target: "srv-edge-02", meta: [{ text: "15:22:01" }, { text: "2.1s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_7d13_power_status", icon: Zap, iconColor: "text-ok", title: "tsk_7d13...power_status", target: "srv-mon-db-01", meta: [{ text: "15:18:45" }, { text: "0.4s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_8e24_ssh_exec", icon: Terminal, iconColor: "text-ok", title: "tsk_8e24...ssh_exec", target: "srv-conf-01", meta: [{ text: "15:12:11" }, { text: "3.0s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_9f35_pg_query", icon: Database, iconColor: "text-ok", title: "tsk_9f35...pg_query", target: "srv-jira-db-01", meta: [{ text: "15:01:09" }, { text: "0.9s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_a046s_apt_install", icon: Box, iconColor: "text-ok", title: "tsk_a046...apt_install", target: "srv-edge-01", meta: [{ text: "14:55:18" }, { text: "22s" }, { text: "try 1/3" }], status: "OK" },
  { id: "tsk_b157s_disk_smart", icon: HardDrive, iconColor: "text-ok", title: "tsk_b157...disk_smart", target: "srv-node-12", meta: [{ text: "14:50:01" }, { text: "1.8s" }, { text: "try 1/3" }], status: "OK" },
];

const FAILED: Row[] = [
  { id: "tsk_8f2a_install_kernel_update", icon: Box, iconColor: "text-danger", title: "tsk_8f2a...install_kernel_update", target: "srv-node-17", meta: [{ text: "15:12:08" }, { text: "4 мин", hb: true }, { text: "try 2/3" }], status: "FAIL" },
  { id: "tsk_9038_power_cycle", icon: Zap, iconColor: "text-danger", title: "tsk_9038...power_cycle", target: "srv-router-core-01", meta: [{ text: "14:45:18" }, { text: "—" }, { text: "try 3/3" }], status: "FAIL" },
  { id: "tsk_a149_ssh_exec", icon: Terminal, iconColor: "text-danger", title: "tsk_a149...ssh_exec", target: "srv-node-03", meta: [{ text: "14:32:08" }, { text: "—" }, { text: "try 3/3" }], status: "FAIL" },
  { id: "tsk_b25a_pg_dump", icon: Database, iconColor: "text-danger", title: "tsk_b25a...pg_dump", target: "srv-jira-db-01", meta: [{ text: "13:55:22" }, { text: "—" }, { text: "try 2/3" }], status: "FAIL" },
  { id: "tsk_c36b_disk_resize", icon: HardDrive, iconColor: "text-danger", title: "tsk_c36b...disk_resize", target: "srv-db-replica-02", meta: [{ text: "13:20:01" }, { text: "—" }, { text: "try 3/3" }], status: "FAIL" },
  { id: "tsk_d47c_apt_install", icon: Box, iconColor: "text-danger", title: "tsk_d47c...apt_install", target: "srv-build-02", meta: [{ text: "12:48:18" }, { text: "—" }, { text: "try 3/3" }], status: "FAIL" },
  { id: "tsk_e58d_power_off", icon: Zap, iconColor: "text-danger", title: "tsk_e58d...power_off", target: "srv-edge-dtkk-01", meta: [{ text: "11:15:42" }, { text: "—" }, { text: "try 3/3" }], status: "FAIL" },
];

const RETRYING: Row[] = [
  { id: "tsk_f69e_ssh_exec", icon: Terminal, iconColor: "text-warn", title: "tsk_f69e...ssh_exec", target: "srv-edge-03", meta: [{ text: "backoff 15s" }, { text: "try 2/3" }], status: "RTY" },
  { id: "tsk_07af_power_status", icon: Zap, iconColor: "text-warn", title: "tsk_07af...power_status", target: "srv-node-03", meta: [{ text: "backoff 30s" }, { text: "try 2/3" }], status: "RTY" },
  { id: "tsk_18b0_apt_install", icon: Box, iconColor: "text-warn", title: "tsk_18b0...apt_install", target: "srv-build-02", meta: [{ text: "backoff 60s" }, { text: "try 3/3" }], status: "RTY" },
];

const ORPHANED: Row[] = [
  { id: "tsk_29c1_ssh_exec", icon: Terminal, iconColor: "text-danger", title: "tsk_29c1...ssh_exec", target: "srv-conf-01", meta: [{ text: "worker dropped" }, { text: "last hb 12 мин" }], status: "ORP" },
  { id: "tsk_3ad2_pg_query", icon: Database, iconColor: "text-danger", title: "tsk_3ad2...pg_query", target: "srv-mon-db-01", meta: [{ text: "worker dropped" }, { text: "last hb 25 мин" }], status: "ORP" },
];

export function WorkerAccountAdmin() {
  const [selected, setSelected] = useState<string>(
    "tsk_8f2a_install_kernel_update",
  );

  return (
    <Shell breadcrumb="server_worker / tasks">
      <WorkerTaskList
        groups={[
          { title: "RUNNING", icon: Play, count: 5, rows: RUNNING },
          { title: "QUEUED", icon: Clock, count: 8, rows: QUEUED },
          { title: "SUCCEEDED", icon: CheckCircle, count: 25, rows: SUCCEEDED },
          { title: "FAILED", icon: XCircle, count: 7, rows: FAILED },
          { title: "RETRYING", icon: RefreshCw, count: 3, rows: RETRYING },
          { title: "ORPHANED", icon: AlertTriangle, count: 2, rows: ORPHANED },
        ]}
        selected={selected}
        onSelect={setSelected}
        searchPlaceholder="Поиск по 50 task'ам..."
        countLabel="50 / 50"
        scopeOptions={["by status", "by dept", "by type", "by actor"]}
        extraFilters={[
          { options: ["dept: all", "Ядро DBOS", "ДТКК", "Инфра"] },
          { options: ["actor: all", "bob", "alice", "system"] },
        ]}
      />
      <FailedKernelDetail />
    </Shell>
  );
}

function FailedKernelDetail() {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <Box className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">
              tsk_8f2a91c45e_install_kernel_update
            </h1>
            <span className="badge badge-danger">FAILED</span>
            <span className="text-xs text-dim">
              try <b>2 / 3</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span>
              <Server className="w-3 h-3 inline" /> target:{" "}
              <b className="mono">srv-node-17</b>
            </span>
            <span>·</span>
            <span>
              <Bot className="w-3 h-3 inline" /> worker:{" "}
              <b className="mono">wrk-02</b>
            </span>
            <span>·</span>
            <span>
              dept: <b>Ядро DBOS</b>
            </span>
            <span>·</span>
            <span>
              actor: <b>bob</b>
            </span>
            <span>·</span>
            <span>
              started: <b>15:12:08</b>
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="btn btn-primary flex items-center gap-1">
            <RefreshCw className="w-4 h-4" /> Re-run
          </button>
          <button className="btn flex items-center gap-1">
            <XCircle className="w-4 h-4" /> Cancel
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
  "type": "package.install",
  "package": "linux-image-astra-5.15.0-86",
  "version": "5.15.0-86.94-generic",
  "reboot_after": true,
  "timeout_s": 600,
  "retry_policy": {
    "max": 3,
    "backoff": "exponential",
    "base_s": 15
  },
  "requested_by": "bob",
  "request_id": "req_8f2a91c4"
}`}</pre>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Server className="w-4 h-4" /> Servers (1)
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
                <td className="py-2 mono">srv-node-17</td>
                <td>Ядро DBOS</td>
                <td>
                  <span className="badge badge-danger">FAILED</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div className="mt-3 text-xs text-dim">
            На этом task'е был 1 target. Для bulk-package одного task'а часто
            несколько серверов.
          </div>
        </div>

        <div className="col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-danger" /> Last error
          </div>
          <div className="alert-block mono text-xs whitespace-pre overflow-x-auto">
            {`apt-get: error: package linux-image-astra-5.15.0-86 has unmet dependencies:
  Depends: linux-headers-astra-5.15.0-86 (= 5.15.0-86.94) but it is not installable

Worker traceback (wrk-02):
  File "/opt/dbos/worker/runner.py", line 142, in execute
    rc = await self._run_apt(["install", "-y", pkg])
  File "/opt/dbos/worker/runner.py", line 218, in _run_apt
    raise PackageInstallError(stderr.decode().strip(), rc=rc)
dbos_worker.exceptions.PackageInstallError: apt-get exit code 100 — see stderr

ssh stderr tail:
  E: Unable to correct problems, you have held broken packages.`}
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Clock className="w-4 h-4" /> Timeline
          </div>
          <div className="text-sm">
            {[
              { c: "ok", t: "queued", ts: "15:12:08" },
              { c: "ok", t: "dispatched → worker wrk-02", ts: "15:12:09" },
              { c: "ok", t: "started: apt-get update", ts: "15:12:11" },
              { c: "accent", t: "heartbeat — running", ts: "15:12:41", hb: true },
              { c: "accent", t: "heartbeat — running", ts: "15:13:11", hb: true },
              { c: "accent", t: "heartbeat — running", ts: "15:13:41", hb: true },
              { c: "accent", t: "heartbeat — running", ts: "15:14:11", hb: true },
              { c: "accent", t: "heartbeat — running", ts: "15:14:41", hb: true },
              { c: "danger", t: "failed — PackageInstallError", ts: "15:15:02", danger: true },
              { c: "warn", t: "retry scheduled (try 3/3, backoff 60s)", ts: "15:16:02", warn: true },
            ].map((s, i) => (
              <div key={i} className="timeline-step">
                <span className={`dot ${s.c}`} />
                <span
                  className={s.danger ? "text-danger" : s.warn ? "text-warn" : ""}
                >
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
              {[
                { role: "predecessor", id: "tsk_7f1980b34d_apt_update", typ: "package.update", st: "OK", stCls: "badge-ok", time: "15:08:55" },
                { role: "retry-1", id: "tsk_8e1d80b34d_install_kernel_update", typ: "package.install", st: "FAILED", stCls: "badge-danger", time: "15:11:20" },
              ].map((r) => (
                <tr key={r.id} className="border-t border-token">
                  <td className="py-2 text-dim">{r.role}</td>
                  <td className="mono">
                    <a href="#" className="text-accent">
                      {r.id}
                    </a>
                  </td>
                  <td>{r.typ}</td>
                  <td>
                    <span className={`badge ${r.stCls}`}>{r.st}</span>
                  </td>
                  <td className="text-dim text-xs mono">{r.time}</td>
                </tr>
              ))}
              <tr
                className="border-t border-token"
                style={{ background: "var(--selected)" }}
              >
                <td className="py-2 text-dim">current</td>
                <td className="mono">tsk_8f2a91c45e_install_kernel_update</td>
                <td>package.install</td>
                <td>
                  <span className="badge badge-danger">FAILED</span>
                </td>
                <td className="text-dim text-xs mono">15:12:08</td>
              </tr>
              <tr className="border-t border-token">
                <td className="py-2 text-dim">retry-next</td>
                <td className="mono text-dim">— (scheduled 15:16:02)</td>
                <td>package.install</td>
                <td>
                  <span className="badge badge-warn">PENDING</span>
                </td>
                <td className="text-dim text-xs mono">15:16:02</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
