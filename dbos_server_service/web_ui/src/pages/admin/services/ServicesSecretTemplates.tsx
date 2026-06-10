import { useState } from "react";
import { LayoutTemplate, Edit3, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { SECRET_TEMPLATES, type SecretTemplate } from "@/mocks/cluster";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

export function ServicesSecretTemplates() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="secret_service (templates)"
        endpoints={[
          "GET   /secret/v1/templates",
          "POST  /secret/v1/templates",
          "PATCH /secret/v1/templates/{id}",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.secret === "admin";

  return (
    <InlineEditor
      title="Шаблоны типов секретов"
      icon={LayoutTemplate}
      hint="поля + validator · CRUD требует service_roles.secret = admin"
      items={SECRET_TEMPLATES}
      getId={(t) => t.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <LayoutTemplate className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">
                {item.fields.length} полей
              </div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(t, { editing, onClose }) => {
        if (editing) return <TplForm initial={t} onDone={onClose} mode="edit" />;
        return <TplView tpl={t} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <TplForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function TplView({ tpl, canEdit }: { tpl: SecretTemplate; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <LayoutTemplate className="w-4 h-4 text-accent" /> {tpl.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(tpl.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="template_id" v={<span className="mono">{tpl.id}</span>} />
      <StatRow k="name" v={<span className="mono">{tpl.name}</span>} />
      <StatRow
        k="fields"
        v={
          <div className="flex flex-wrap gap-1">
            {tpl.fields.map((f) => (
              <span key={f} className="badge mono">{f}</span>
            ))}
          </div>
        }
      />
      <StatRow k="validator" v={<span className="mono">{tpl.validator}</span>} />
    </div>
  );
}

function TplForm({
  initial,
  onDone,
  mode,
}: {
  initial?: SecretTemplate;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [fields, setFields] = useState(initial?.fields.join(", ") ?? "");
  const [validator, setValidator] = useState(initial?.validator ?? "");

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <LayoutTemplate className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый шаблон" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="fields" hint="через запятую: host, port, username, password">
          <input className="input mono" value={fields} onChange={(e) => setFields(e.target.value)} />
        </FormRow>
        <FormRow label="validator" hint="команда для smoke-теста новой кред'ы">
          <textarea className="input" value={validator} onChange={(e) => setValidator(e.target.value)} />
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
