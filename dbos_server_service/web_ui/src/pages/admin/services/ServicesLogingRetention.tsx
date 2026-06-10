import { useState } from "react";
import { Clock, Edit3 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { LOG_RETENTION, type MockRetention } from "@/mocks/log";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

type RetentionPolicy = MockRetention;
const POLICIES = LOG_RETENTION;

export function ServicesLogingRetention() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "logging_admin";
  if (!mockMode) {
    return (
      <NotWiredInline
        service="loging_service (retention)"
        endpoints={[
          "GET   /loging/v1/retention/policy",
          "PATCH /loging/v1/retention/policy",
          "POST  /loging/v1/retention/sweep",
        ]}
      />
    );
  }

  return (
    <InlineEditor
      title="Retention · loging_service"
      icon={Clock}
      hint="периоды хранения и sweep · per-scope политика"
      items={POLICIES}
      getId={(p) => p.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "CRUD retention требует loging_admin" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.id}</div>
              <div className="text-[11px] text-dim truncate">{item.scope}</div>
            </div>
            <span className="badge">{item.days}d</span>
          </div>
        </button>
      )}
      renderDetail={(p, { editing, onClose }) => {
        if (editing) return <RetentionForm initial={p} onDone={onClose} mode="edit" />;
        return <RetentionView policy={p} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <RetentionForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function RetentionView({ policy, canEdit }: { policy: RetentionPolicy; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Clock className="w-4 h-4 text-accent" /> {policy.id}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(policy.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn">Force sweep</button>
          </div>
        )}
      </div>
      <StatRow k="scope" v={policy.scope} />
      <StatRow k="retention_days" v={<span className="mono">{policy.days}</span>} />
      <StatRow k="last_sweep" v={<span className="mono">{policy.lastSweep}</span>} />
      <StatRow k="next_sweep" v={<span className="mono">{policy.nextSweep}</span>} />
      <StatRow k="tablespace_size" v={<span className="mono">{policy.size}</span>} />
    </div>
  );
}

function RetentionForm({
  initial,
  onDone,
  mode,
}: {
  initial?: RetentionPolicy;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [scope, setScope] = useState(initial?.scope ?? "");
  const [days, setDays] = useState(initial?.days ?? 30);

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Clock className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая политика retention" : `Edit · ${initial?.id}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="scope" hint="например audit · severity=ERROR или metrics · 1h aggregated">
          <input className="input" value={scope} onChange={(e) => setScope(e.target.value)} />
        </FormRow>
        <FormRow label="retention_days">
          <input
            type="number"
            className="input mono"
            value={days}
            onChange={(e) => setDays(Number(e.target.value) || 0)}
          />
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
