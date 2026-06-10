import { useState } from "react";
import { Calendar, Edit3, Play, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { CRON_JOBS } from "@/mocks/cluster";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

// worker_service ещё не подключён к UI. Ожидаемые endpoint'ы для cron:
//   GET    /worker/v1/cron
//   POST   /worker/v1/cron
//   PATCH  /worker/v1/cron/{name}
//   DELETE /worker/v1/cron/{name}
//   POST   /worker/v1/cron/{name}/run
const WORKER_CRON_NOT_WIRED =
  "worker_service ещё не подключён · нужен endpoint POST /worker/v1/cron/…";

interface CronJob {
  name: string;
  schedule: string;
  last: string;
  status: string;
}

export function ServicesWorkerCron() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="server_worker (cron)"
        endpoints={[
          "GET    /worker/v1/cron",
          "POST   /worker/v1/cron",
          "PATCH  /worker/v1/cron/{name}",
          "DELETE /worker/v1/cron/{name}",
          "POST   /worker/v1/cron/{name}/run",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.worker === "admin";

  return (
    <InlineEditor
      title="Cron-задачи"
      icon={Calendar}
      hint="расписания регулярных операций · UTC · force run без disabling"
      items={CRON_JOBS}
      getId={(c) => c.name}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate mono">{item.schedule}</div>
            </div>
            <span className={`badge badge-${item.status === "ok" || item.status === "pass" ? "ok" : "warn"}`}>
              {item.status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(c, { editing, onClose }) => {
        if (editing) return <CronForm initial={c} onDone={onClose} mode="edit" />;
        return <CronView job={c} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <CronForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function CronView({ job, canEdit }: { job: CronJob; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  const toast = useToast();
  const notWired = () => toast.warn(WORKER_CRON_NOT_WIRED);
  return (
    <div className="card max-w-2xl">
      <div className="mb-3 alert-block text-xs">
        Раздел работает только когда подключен worker_service. Кнопки ниже —
        заглушки до появления соответствующих endpoint'ов.
      </div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Calendar className="w-4 h-4 text-accent" /> {job.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={notWired}>
              <Play className="w-4 h-4" /> Force run
            </button>
            <button className="btn flex items-center gap-1" onClick={() => startEdit(job.name)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1" onClick={notWired}>
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="name" v={<span className="mono">{job.name}</span>} />
      <StatRow k="schedule" v={<span className="mono">{job.schedule}</span>} />
      <StatRow k="last_run" v={<span className="mono">{job.last}</span>} />
      <StatRow k="status" v={job.status} />
    </div>
  );
}

function CronForm({
  initial,
  onDone,
  mode,
}: {
  initial?: CronJob;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [schedule, setSchedule] = useState(initial?.schedule ?? "0 3 * * *");
  const toast = useToast();
  const submit = () => {
    toast.warn(WORKER_CRON_NOT_WIRED);
    onDone();
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Calendar className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая cron-задача" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="schedule" hint="cron-выражение UTC, например 0 3 * * *">
          <input className="input mono" value={schedule} onChange={(e) => setSchedule(e.target.value)} />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>Отмена</button>
        <button className="btn btn-primary" onClick={submit}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
