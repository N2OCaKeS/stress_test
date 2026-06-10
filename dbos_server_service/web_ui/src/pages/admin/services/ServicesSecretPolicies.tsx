import { useState } from "react";
import { RotateCw, Edit3, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { SECRET_POLICIES, type SecretPolicy } from "@/mocks/cluster";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

export function ServicesSecretPolicies() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="secret_service (policies)"
        endpoints={[
          "GET   /secret/v1/policies",
          "POST  /secret/v1/policies",
          "PATCH /secret/v1/policies/{id}",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.secret === "admin";

  return (
    <InlineEditor
      title="Политики ротации"
      icon={RotateCw}
      hint="period · scope global / per-secret · per-secret override бьёт глобальную"
      items={SECRET_POLICIES}
      getId={(p) => p.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <RotateCw className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">
                {item.scope} · period {item.rotation_period}
              </div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(p, { editing, onClose }) => {
        if (editing) return <PolicyForm initial={p} onDone={onClose} mode="edit" />;
        return <PolicyView policy={p} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <PolicyForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function PolicyView({ policy, canEdit }: { policy: SecretPolicy; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <RotateCw className="w-4 h-4 text-accent" /> {policy.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(policy.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="policy_id" v={<span className="mono">{policy.id}</span>} />
      <StatRow k="name" v={<span className="mono">{policy.name}</span>} />
      <StatRow k="scope" v={policy.scope} />
      <StatRow k="period" v={<span className="mono">{policy.rotation_period}</span>} />
      <StatRow k="note" v={policy.note} />
    </div>
  );
}

function PolicyForm({
  initial,
  onDone,
  mode,
}: {
  initial?: SecretPolicy;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [scope, setScope] = useState<SecretPolicy["scope"]>(initial?.scope ?? "global");
  const [period, setPeriod] = useState(initial?.rotation_period ?? "30d");
  const [note, setNote] = useState(initial?.note ?? "");

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <RotateCw className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая политика" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="scope">
          <select className="input" value={scope} onChange={(e) => setScope(e.target.value as SecretPolicy["scope"])}>
            <option value="global">global</option>
            <option value="per-secret">per-secret</option>
          </select>
        </FormRow>
        <FormRow label="rotation_period" hint="например 30d, 90d, 180d">
          <input className="input mono" value={period} onChange={(e) => setPeriod(e.target.value)} />
        </FormRow>
        <FormRow label="note">
          <textarea className="input" value={note} onChange={(e) => setNote(e.target.value)} />
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
