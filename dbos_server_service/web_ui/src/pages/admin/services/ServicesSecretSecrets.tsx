import { useState } from "react";
import { KeyRound, Edit3, RotateCw, Eye, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { CREDENTIALS, type CredentialKind } from "@/mocks/secret";
import { DEPTS } from "@/mocks/auth";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { formatMskDate } from "@/lib/datetime";
import { useMockMode } from "@/api/auth/useQuery";

// secret_service ещё не подключён к UI — действия ниже остаются заглушками.
// Когда секрет-сервис появится, ожидаемые endpoint'ы:
//   GET    /secret/v1/credentials
//   POST   /secret/v1/credentials
//   PATCH  /secret/v1/credentials/{id}
//   DELETE /secret/v1/credentials/{id}
//   POST   /secret/v1/credentials/{id}/reveal
//   POST   /secret/v1/credentials/{id}/rotate
const SECRET_NOT_WIRED =
  "secret_service ещё не подключён · нужен endpoint POST /secret/v1/credentials/…";

export function ServicesSecretSecrets() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="secret_service"
        endpoints={[
          "GET    /secret/v1/credentials",
          "POST   /secret/v1/credentials",
          "PATCH  /secret/v1/credentials/{id}",
          "DELETE /secret/v1/credentials/{id}",
          "POST   /secret/v1/credentials/{id}/reveal",
          "POST   /secret/v1/credentials/{id}/rotate",
        ]}
      />
    );
  }
  const isAccountAdmin = persona.platform_role === "account_admin";
  const isDepAdmin = persona.platform_role === "dep_admin";
  const hasSecretAdminRole = persona.service_roles?.secret === "admin";
  const canEdit = isAccountAdmin || isDepAdmin || hasSecretAdminRole;

  // Dep scope: dep_admin / service-admin see only their dept; account sees all.
  const visible = isAccountAdmin
    ? CREDENTIALS
    : persona.dept_id
      ? CREDENTIALS.filter((c) => c.dept_id === persona.dept_id)
      : CREDENTIALS;

  return (
    <InlineEditor
      title="Секреты · secret_service"
      icon={KeyRound}
      hint="CRUD + reveal (audited) + rotate · 70 секретов"
      items={visible}
      getId={(c) => c.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">{item.kind} · {item.dept_id}</div>
            </div>
            <span className={`badge badge-${item.status === "active" ? "ok" : item.status === "expiring" || item.status === "rotating" ? "warn" : "danger"}`}>
              {item.status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(c, { editing, onClose }) => {
        if (editing) return <SecretForm initial={c} onDone={onClose} mode="edit" />;
        return <SecretView cred={c} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <SecretForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function SecretView({
  cred,
  canEdit,
}: {
  cred: typeof CREDENTIALS[number];
  canEdit: boolean;
}) {
  const { startEdit } = useInlineState();
  const toast = useToast();
  const notWired = () => toast.warn(SECRET_NOT_WIRED);
  return (
    <div className="card max-w-2xl">
      <div className="mb-3 alert-block text-xs">
        Раздел работает только когда подключен secret_service. Кнопки ниже —
        заглушки до появления соответствующих endpoint'ов.
      </div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <KeyRound className="w-4 h-4 text-accent" /> {cred.name}
          <span className={`badge badge-${cred.status === "active" ? "ok" : cred.status === "expiring" || cred.status === "rotating" ? "warn" : "danger"}`}>
            {cred.status}
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <button className="btn flex items-center gap-1" onClick={notWired}>
            <Eye className="w-4 h-4" /> Reveal
          </button>
          {canEdit && (
            <>
              <button className="btn flex items-center gap-1" onClick={() => startEdit(cred.id)}>
                <Edit3 className="w-4 h-4" /> Edit
              </button>
              <button className="btn flex items-center gap-1" onClick={notWired}>
                <RotateCw className="w-4 h-4" /> Rotate
              </button>
              <button className="btn btn-danger flex items-center gap-1" onClick={notWired}>
                <Trash2 className="w-4 h-4" /> Delete
              </button>
            </>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-6">
        <div>
          <StatRow k="cred_id" v={<span className="mono">{cred.id}</span>} />
          <StatRow k="name" v={<span className="mono">{cred.name}</span>} />
          <StatRow k="kind" v={cred.kind} />
          <StatRow k="dept" v={cred.dept_id} />
        </div>
        <div>
          <StatRow k="owner" v={<span className="mono">{cred.owner}</span>} />
          <StatRow k="created_at" v={<span className="mono">{formatMskDate(cred.created_at)}</span>} />
          <StatRow k="expires_at" v={<span className="mono">{formatMskDate(cred.expires_at)}</span>} />
          <StatRow k="reveal_24h" v={<span className="mono">{cred.reveal_count_24h}</span>} />
        </div>
      </div>
      <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim">
        Reveal пишется в audit с reason · rotation выполняется через worker.
      </div>
    </div>
  );
}

function SecretForm({
  initial,
  onDone,
  mode,
}: {
  initial?: typeof CREDENTIALS[number];
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [kind, setKind] = useState<CredentialKind>(initial?.kind ?? "password");
  const [dept, setDept] = useState(initial?.dept_id ?? DEPTS[0]?.id ?? "");
  const [owner, setOwner] = useState(initial?.owner ?? "");
  const toast = useToast();
  const submit = () => {
    toast.warn(SECRET_NOT_WIRED);
    onDone();
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <KeyRound className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый секрет" : `Edit · ${initial?.name}`}
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="kind">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as CredentialKind)}>
            <option value="password">password</option>
            <option value="token">token</option>
            <option value="ssh_key">ssh_key</option>
            <option value="cert">cert</option>
          </select>
        </FormRow>
        <FormRow label="dept">
          <select className="input" value={dept} onChange={(e) => setDept(e.target.value)}>
            {DEPTS.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </FormRow>
        <FormRow label="owner">
          <input className="input mono" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </FormRow>
      </div>
      <FormRow label="plaintext" hint="будет показан один раз после создания">
        <input className="input mono" placeholder="не сохраняется на сервере" />
      </FormRow>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>Отмена</button>
        <button className="btn btn-primary" onClick={submit}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
