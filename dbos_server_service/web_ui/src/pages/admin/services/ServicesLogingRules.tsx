import { useState } from "react";
import { AlertTriangle, Edit3, Pause, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { LOG_RULES, type MockRule } from "@/mocks/log";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

type AlertRule = MockRule;
const RULES = LOG_RULES;

export function ServicesLogingRules() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "logging_admin";
  if (!mockMode) {
    return (
      <NotWiredInline
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
    <InlineEditor
      title="Правила алёртов"
      icon={AlertTriangle}
      hint="loging_service · evaluator проходит каждые 60s · CRITICAL → on-call"
      items={RULES}
      getId={(r) => r.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения · CRUD требует loging_admin" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <AlertTriangle className={`w-4 h-4 ${item.enabled ? "text-warn" : "text-dim"}`} />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.expr}</div>
              <div className="text-[11px] text-dim truncate">{item.severity} · {item.updated}</div>
            </div>
            <span className={`badge badge-${item.enabled ? "ok" : ""}`}>
              {item.enabled ? "on" : "off"}
            </span>
          </div>
        </button>
      )}
      renderDetail={(r, { editing, onClose }) => {
        if (editing) return <RuleForm initial={r} onDone={onClose} mode="edit" />;
        return <RuleView rule={r} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <RuleForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function RuleView({ rule, canEdit }: { rule: AlertRule; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-warn" />
          <span className="mono text-sm">{rule.expr}</span>
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(rule.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn flex items-center gap-1">
              <Pause className="w-4 h-4" /> {rule.enabled ? "Disable" : "Enable"}
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="rule_id" v={<span className="mono">{rule.id}</span>} />
      <StatRow k="expression" v={<span className="mono">{rule.expr}</span>} />
      <StatRow k="severity" v={<span className={`sev sev-${rule.severity}`}>{rule.severity}</span>} />
      <StatRow k="enabled" v={rule.enabled ? <span className="text-ok">on</span> : <span className="text-dim">off</span>} />
      <StatRow k="updated" v={rule.updated} />
    </div>
  );
}

function RuleForm({
  initial,
  onDone,
  mode,
}: {
  initial?: AlertRule;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [expr, setExpr] = useState(initial?.expr ?? "");
  const [severity, setSeverity] = useState<AlertRule["severity"]>(initial?.severity ?? "WARNING");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-warn" />
        {mode === "new" ? "Новое правило" : `Edit · ${initial?.expr}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="expression" hint="DSL: action OP value [WINDOW] · напр. login.failed × 5">
          <textarea className="input" value={expr} onChange={(e) => setExpr(e.target.value)} />
        </FormRow>
        <FormRow label="severity">
          <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value as AlertRule["severity"])}>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="CRITICAL">CRITICAL</option>
          </select>
        </FormRow>
        <FormRow label="enabled">
          <label className="checkbox-row">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>выполнять</span>
          </label>
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
