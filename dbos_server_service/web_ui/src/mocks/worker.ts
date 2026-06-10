/* worker_service mock: 50 tasks + 12 DLQ. */

export type TaskState =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "retry"
  | "dlq";

export interface MockTask {
  id: string;
  kind: string;
  state: TaskState;
  dept_id: string;
  enqueued_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  payload_preview: string;
  error: string | null;
}

const KINDS = [
  "rotate_credential", "reboot_server", "scan_audit",
  "backup_db", "sync_users", "send_notification",
  "rebuild_cache", "verify_ipmi",
];
const DEPTS = ["core", "dtkk", "infra", "ops"];

function genTasks(count: number, baseState: TaskState | null = null): MockTask[] {
  const out: MockTask[] = [];
  for (let i = 0; i < count; i++) {
    const kind = KINDS[i % KINDS.length];
    let state: TaskState = baseState ?? "success";
    if (!baseState) {
      if (i % 13 === 0) state = "failed";
      else if (i % 9 === 0) state = "retry";
      else if (i % 7 === 0) state = "running";
      else if (i % 11 === 0) state = "pending";
    }
    const enqueued = `2026-06-10T${String(i % 24).padStart(2, "0")}:00:00Z`;
    out.push({
      id: `tsk_${(0xfa12 + i).toString(16)}`,
      kind,
      state,
      dept_id: DEPTS[i % DEPTS.length],
      enqueued_at: enqueued,
      started_at: state === "pending" ? null : enqueued,
      finished_at: state === "success" || state === "failed" ? `2026-06-10T${String((i % 24) + 1).padStart(2, "0")}:00:00Z` : null,
      attempts: state === "retry" || state === "failed" ? 1 + (i % 3) : 1,
      payload_preview: `{"target":"srv-${String((i % 90) + 1).padStart(2, "0")}"}`,
      error: state === "failed" || state === "dlq" ? "connection refused" : null,
    });
  }
  return out;
}

export const TASKS: MockTask[] = genTasks(50);
export const DLQ_TASKS: MockTask[] = genTasks(12, "dlq");
