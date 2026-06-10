import { useState } from "react";
import { User, Building2, Terminal, Box, HardDrive } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { DlqList, DlqDetail, type DlqRow } from "./dlqShared";

const MY_DEP: DlqRow[] = [
  { id: "dlq_b4f8_grafana_provision", icon: Terminal, title: "dlq_b4f8_grafana_provision", errorShort: "SSHAuthError: key revoked", meta: "2 дн назад · 3/3 · by ci_bot" },
  { id: "dlq_c5a9_install_kernel_update", icon: Box, title: "dlq_c5a9_install_kernel_update", errorShort: "PackageInstallError: unmet deps", meta: "3 дн назад · 5/5 · by bob" },
  { id: "dlq_d6ba_disk_resize", icon: HardDrive, title: "dlq_d6ba_disk_resize", errorShort: "LVMFatalError: pv lost", meta: "7 дн назад · 4/5 · by dave" },
];

export function WorkerDlqDepAdmin() {
  const [selected, setSelected] = useState("dlq_b4f8_grafana_provision");

  return (
    <Shell breadcrumb="server_worker / dlq">
      <DlqList
        groups={[
          { title: "my", icon: User, count: 0, rows: [], emptyLabel: "Лично за тобой пока DLQ нет." },
          { title: "my_dep · Ядро DBOS", icon: Building2, count: 3, rows: MY_DEP },
          { title: "cross_dep", icon: Building2, count: 0, rows: [], emptyLabel: "DLQ других депов не видны — обратись к bob (account_admin)." },
        ]}
        selected={selected}
        onSelect={setSelected}
        searchPlaceholder="Поиск по 3 записям DLQ..."
        countLabel="3 / 3"
        scopeOptions={["scope (my / my_dep)", "by error type", "by task type"]}
      />
      <DlqDetail
        title="dlq_b4f8_grafana_provision"
        retries="3 / 3"
        dlqWhen="2 дня назад (2026-06-08 11:18:02)"
        originalId="tsk_b4f8c1a2_ssh_exec"
        dept="Ядро DBOS"
        actor="ci_bot"
        noteContent={
          <>
            Видишь только DLQ <b>своего депа</b> (Ядро DBOS). Для cross-dept
            анализа — bob (account_admin). <b>Re-queue</b> создаст новую task с
            теми же параметрами; original{" "}
            <span className="mono">tsk_b4f8c1a2</span> не воскресает.
          </>
        }
        originalTask={{
          id: "tsk_b4f8c1a2_ssh_exec",
          type: "ssh.exec",
          target: "srv-monitor-01",
          targetHref: "/server",
          args: "cmd=ansible-playbook grafana.yml",
          requestedBy: "ci_bot",
          createdAt: "2026-06-08 11:12:08",
        }}
        whyDlqItems={[
          {
            kind: "danger",
            content: (
              <>
                <b>SSH key revoked</b> — ci_bot пытался зайти deploy-ключом,
                который был revoke'нут вчера ротацией.
              </>
            ),
          },
          {
            kind: "warn",
            content: (
              <>
                3 попытки подряд — все упали с{" "}
                <span className="mono">Permission denied (publickey)</span>.
              </>
            ),
          },
          {
            kind: "info",
            content: (
              <>
                <b>Рекомендация:</b> добавить новый deploy-key в{" "}
                <a href="/secret" className="text-accent mono">
                  grafana-deploy-key
                </a>{" "}
                и Re-queue.
              </>
            ),
          },
        ]}
        lastErrorTitle="Last error (try 3/3)"
        lastErrorBody={`Worker traceback (wrk-01):
  File "/opt/dbos/worker/runner.py", line 88, in execute
    await self._ssh_connect(host, user="deploy", key=key_id)
  File "/opt/dbos/worker/transport/ssh.py", line 142, in _ssh_connect
    raise SSHAuthError("Permission denied (publickey)")
dbos_worker.exceptions.SSHAuthError: Permission denied (publickey)

ssh stderr tail:
  deploy@srv-monitor-01: Permission denied (publickey).
  Connection closed by 10.177.103.21 port 22`}
        retryHistory={[
          { attempt: "1/3", time: "11:12:08", backoff: "—", code: "ssh:authdenied", worker: "wrk-01", stderr: "Permission denied (publickey)" },
          { attempt: "2/3", time: "11:14:22", backoff: "30s", code: "ssh:authdenied", worker: "wrk-02", stderr: "Permission denied (publickey)" },
          { attempt: "3/3", time: "11:17:55", backoff: "60s", code: "ssh:authdenied", worker: "wrk-01", stderr: "Permission denied (publickey)" },
        ]}
        retryNote={
          <>
            После 3-й попытки task ушла в DLQ (политика retry:{" "}
            <span className="mono">max=3, backoff=exponential, base=30s</span>).
          </>
        }
        relatedTitle="Related (Ядро DBOS)"
        relatedRows={[
          { id: "dlq_c5a9", type: "package.install", server: "srv-node-17", error: "apt:100", when: "3 дн назад" },
          { id: "dlq_d6ba", type: "disk.resize", server: "srv-db-replica-02", error: "lvm:pvlost", when: "7 дн назад" },
        ]}
        relatedNote={<>В депе всего 3 DLQ за 14 дней — здоровый показатель.</>}
      />
    </Shell>
  );
}
