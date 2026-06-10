import { useState } from "react";
import { Inbox, Edit3, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { DLQ_POLICIES } from "@/mocks/cluster";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

interface DlqPolicy {
  name: string;
  max_retries: number;
  backoff: string;
  target: string;
}

export function ServicesWorkerDlq() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="server_worker (DLQ policies)"
        endpoints={[
          "GET   /worker/v1/dlq/policies",
          "POST  /worker/v1/dlq/policies",
          "PATCH /worker/v1/dlq/policies/{name}",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.worker === "admin";

  return (
    <InlineEditor
      title="DLQ-политики"
      icon={Inbox}
      hint="retries + backoff + target queue · age > 24h → WARN-алёрт"
      items={DLQ_POLICIES}
      getId={(d) => d.name}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Inbox className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">retries {item.max_retries} · {item.target}</div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(d, { editing, onClose }) => {
        if (editing) return <DlqForm initial={d} onDone={onClose} mode="edit" />;
        return <DlqView policy={d} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <DlqForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function DlqView({ policy, canEdit }: { policy: DlqPolicy; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Inbox className="w-4 h-4 text-accent" /> {policy.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(policy.name)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="name" v={<span className="mono">{policy.name}</span>} />
      <StatRow k="max_retries" v={<span className="mono">{policy.max_retries}</span>} />
      <StatRow k="backoff" v={<span className="mono">{policy.backoff}</span>} />
      <StatRow k="target" v={<span className="mono">{policy.target}</span>} />
    </div>
  );
}

function DlqForm({
  initial,
  onDone,
  mode,
}: {
  initial?: DlqPolicy;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [retries, setRetries] = useState(initial?.max_retries ?? 5);
  const [backoff, setBackoff] = useState(initial?.backoff ?? "exp(1,2,4,8,16)m");
  const [target, setTarget] = useState(initial?.target ?? "");

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Inbox className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая DLQ-политика" : `Edit · ${initial?.name}`}
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="max_retries">
          <input
            type="number"
            className="input mono"
            value={retries}
            onChange={(e) => setRetries(Number(e.target.value) || 0)}
          />
        </FormRow>
        <FormRow label="backoff" hint="exp(1,2,4,8)m | linear 30s">
          <input className="input mono" value={backoff} onChange={(e) => setBackoff(e.target.value)} />
        </FormRow>
        <FormRow label="target">
          <input className="input mono" value={target} onChange={(e) => setTarget(e.target.value)} />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>Отмена</button>
        <button className="btn btn-primary" onClick={onDone}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
