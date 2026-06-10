import { useState } from "react";
import {
  Building2,
  Box,
  Terminal,
  Zap,
  Database,
  HardDrive,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { DlqList, DlqDetail, type DlqRow } from "./dlqShared";

const CORE: DlqRow[] = [
  { id: "dlq_a791_install_kernel_update", icon: Box, title: "dlq_a791_install_kernel_update", errorShort: "PackageInstallError: unmet deps", meta: "3 дн назад · 5/5" },
  { id: "dlq_b8c2_ssh_exec", icon: Terminal, title: "dlq_b8c2_ssh_exec", errorShort: "SSHConnectError: timeout", meta: "2 дн назад · 3/3" },
  { id: "dlq_c9d3_power_cycle", icon: Zap, title: "dlq_c9d3_power_cycle", errorShort: "IPMIFatalError: bmc unreachable", meta: "5 дн назад · 3/3" },
  { id: "dlq_d0e4_pg_dump", icon: Database, title: "dlq_d0e4_pg_dump", errorShort: "DiskFullError", meta: "1 дн назад · 3/3" },
  { id: "dlq_e1f5_disk_resize", icon: HardDrive, title: "dlq_e1f5_disk_resize", errorShort: "LVMFatalError: pv lost", meta: "7 дн назад · 4/5" },
];

const DTKK: DlqRow[] = [
  { id: "dlq_f206_apt_install", icon: Box, title: "dlq_f206_apt_install", errorShort: "PackageInstallError: 404", meta: "4 дн назад · 5/5" },
  { id: "dlq_0317_ssh_uptime", icon: Terminal, title: "dlq_0317_ssh_uptime", errorShort: "SSHAuthError: key revoked", meta: "6 дн назад · 3/3" },
  { id: "dlq_1428_power_off", icon: Zap, title: "dlq_1428_power_off", errorShort: "IPMICredentialsError", meta: "8 дн назад · 3/3" },
  { id: "dlq_2539_disk_smart", icon: HardDrive, title: "dlq_2539_disk_smart", errorShort: "SMARTAbortedError", meta: "10 дн назад · 3/3" },
];

const INFRA: DlqRow[] = [
  { id: "dlq_364a_ssh_exec", icon: Terminal, title: "dlq_364a_ssh_exec", errorShort: "SSHFatalError: host gone", meta: "2 дн назад · 3/3" },
  { id: "dlq_475b_pg_query", icon: Database, title: "dlq_475b_pg_query", errorShort: "PgFatalError: role missing", meta: "9 дн назад · 3/3" },
  { id: "dlq_586c_apt_upgrade", icon: Box, title: "dlq_586c_apt_upgrade", errorShort: "PackageInstallError: locked", meta: "11 дн назад · 5/5" },
];

export function WorkerDlqAccountAdmin() {
  const [selected, setSelected] = useState("dlq_a791_install_kernel_update");

  return (
    <Shell breadcrumb="server_worker / dlq">
      <DlqList
        groups={[
          { title: "ЯДРО DBOS", icon: Building2, count: 5, rows: CORE },
          { title: "ДТКК", icon: Building2, count: 4, rows: DTKK },
          { title: "ИНФРА", icon: Building2, count: 3, rows: INFRA },
        ]}
        selected={selected}
        onSelect={setSelected}
        searchPlaceholder="Поиск по 12 записям DLQ..."
        countLabel="12 / 12"
        scopeOptions={["by dept", "by error type", "by task type"]}
        showTypeFilter
      />
      <DlqDetail
        title="dlq_a791_install_kernel_update"
        retries="5 / 5"
        dlqWhen="3 дня назад (2026-06-07 14:08:12)"
        originalId="tsk_8f2a91c45e"
        dept="Ядро DBOS"
        actor="bob"
        noteContent={
          <>
            <b>Re-queue</b> создаёт <span className="mono">новую task</span> с
            теми же параметрами и добавляет её в обычную очередь (не воскрешает
            оригинальный <span className="mono">tsk_8f2a91c45e</span>).
            DLQ-запись при этом удаляется. Если хочешь сохранить запись —
            сначала <b>Export for analysis</b>, потом Re-queue.
          </>
        }
        originalTask={{
          id: "tsk_8f2a91c45e_install_kernel_update",
          type: "package.install",
          target: "srv-node-17",
          args: "package=linux-image-astra-5.15.0-86 · reboot=true",
          requestedBy: "bob",
          createdAt: "2026-06-07 15:12:08",
        }}
        whyDlqItems={[
          {
            kind: "danger",
            content: (
              <>
                <b>max retries reached</b> — task сделала <b>5 попыток</b> с
                экспоненциальным backoff (15s → 30s → 60s → 120s → 240s),
                каждая упала с одной и той же ошибкой.
              </>
            ),
          },
          {
            kind: "warn",
            content: (
              <>
                Worker классифицировал ошибку как{" "}
                <span className="mono">retryable</span> (apt вернул exit 100),
                но реальная причина — <b>broken deps</b>: исправляется только
                вручную.
              </>
            ),
          },
          {
            kind: "info",
            content: (
              <>
                <b>Рекомендация:</b> починить репозиторий (добавить{" "}
                <span className="mono">linux-headers-astra</span> ровно той же
                версии), затем Re-queue. Без фикса task снова попадёт сюда.
              </>
            ),
          },
        ]}
        lastErrorTitle="Last error (try 5/5)"
        lastErrorBody={`apt-get: error: package linux-image-astra-5.15.0-86 has unmet dependencies:
  Depends: linux-headers-astra-5.15.0-86 (= 5.15.0-86.94) but it is not installable

Worker traceback (wrk-02):
  File "/opt/dbos/worker/runner.py", line 142, in execute
    rc = await self._run_apt(["install", "-y", pkg])
  File "/opt/dbos/worker/runner.py", line 218, in _run_apt
    raise PackageInstallError(stderr.decode().strip(), rc=rc)
dbos_worker.exceptions.PackageInstallError: apt-get exit code 100 — see stderr

ssh stderr tail:
  E: Unable to correct problems, you have held broken packages.
  E: Sub-process /usr/bin/dpkg returned an error code (100)`}
        retryHistory={[
          { attempt: "1/5", time: "15:12:08", backoff: "—", code: "apt:100", worker: "wrk-02", stderr: "held broken packages" },
          { attempt: "2/5", time: "15:12:35", backoff: "15s", code: "apt:100", worker: "wrk-02", stderr: "held broken packages" },
          { attempt: "3/5", time: "15:13:18", backoff: "30s", code: "apt:100", worker: "wrk-01", stderr: "held broken packages" },
          { attempt: "4/5", time: "15:14:50", backoff: "60s", code: "apt:100", worker: "wrk-03", stderr: "held broken packages" },
          { attempt: "5/5", time: "15:18:02", backoff: "120s", code: "apt:100", worker: "wrk-02", stderr: "held broken packages" },
        ]}
        retryNote={
          <>
            После 5-й попытки worker отправил task в DLQ (политика retry:{" "}
            <span className="mono">max=5, backoff=exponential, base=15s</span>).
          </>
        }
        relatedTitle="Related (5 ближайших similar errors)"
        showDept
        relatedRows={[
          { id: "dlq_f206", type: "package.install", server: "srv-edge-dtkk-04", dept: "ДТКК", error: "apt:100", when: "4 дн назад" },
          { id: "dlq_586c", type: "package.upgrade", server: "srv-build-02", dept: "Инфра", error: "apt:100", when: "11 дн назад" },
          { id: "dlq_704d", type: "package.install", server: "srv-node-22", dept: "Ядро DBOS", error: "apt:100", when: "14 дн назад (вытесн.)" },
          { id: "dlq_815e", type: "package.install", server: "srv-node-08", dept: "Ядро DBOS", error: "apt:dep", when: "17 дн назад (вытесн.)" },
          { id: "dlq_926f", type: "package.install", server: "srv-node-11", dept: "Ядро DBOS", error: "apt:100", when: "21 дн назад (вытесн.)" },
        ]}
        relatedNote={
          <>
            Похоже на <b>системную проблему репозитория</b>: 5 разных серверов в
            3-х деп'ах падают с одной ошибкой. Стоит проверить состояние
            APT-зеркала <span className="mono">repo.dbos.local</span>.
          </>
        }
      />
    </Shell>
  );
}
