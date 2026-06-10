import { useState } from "react";
import { Layers, Edit3, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { SERVER_GROUPS } from "@/mocks/cluster";
import { DEPTS } from "@/mocks/auth";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

// server_service ещё не подключён к UI. Ожидаемые endpoint'ы для групп:
//   GET    /server/v1/groups
//   POST   /server/v1/groups
//   PATCH  /server/v1/groups/{id}
//   DELETE /server/v1/groups/{id}
const SERVER_GROUPS_NOT_WIRED =
  "server_service ещё не подключён · нужен endpoint POST /server/v1/groups";

export function ServicesServerGroups() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="server_service (groups)"
        endpoints={[
          "GET    /server/v1/groups",
          "POST   /server/v1/groups",
          "PATCH  /server/v1/groups/{id}",
          "DELETE /server/v1/groups/{id}",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.server === "admin";

  return (
    <InlineEditor
      title="Группы серверов"
      icon={Layers}
      hint="label-наборы поверх инвентаря · используются для bulk-операций"
      items={SERVER_GROUPS}
      getId={(g) => g.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">{item.dept} · {item.size} серверов</div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(g, { editing, onClose }) => {
        if (editing) return <GroupForm initial={g} onDone={onClose} mode="edit" />;
        return <GroupView group={g} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <GroupForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function GroupView({
  group,
  canEdit,
}: {
  group: typeof SERVER_GROUPS[number];
  canEdit: boolean;
}) {
  const { startEdit } = useInlineState();
  const toast = useToast();
  const notWired = () => toast.warn(SERVER_GROUPS_NOT_WIRED);
  return (
    <div className="card max-w-2xl">
      <div className="mb-3 alert-block text-xs">
        Раздел работает только когда подключен server_service. Кнопки ниже —
        заглушки до появления соответствующих endpoint'ов.
      </div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Layers className="w-4 h-4 text-accent" /> {group.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(group.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1" onClick={notWired}>
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="group_id" v={<span className="mono">{group.id}</span>} />
      <StatRow k="name" v={<span className="mono">{group.name}</span>} />
      <StatRow k="dept" v={group.dept} />
      <StatRow k="size" v={<span className="mono">{group.size}</span>} />
      <StatRow k="note" v={group.note} />
    </div>
  );
}

function GroupForm({
  initial,
  onDone,
  mode,
}: {
  initial?: typeof SERVER_GROUPS[number];
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [dept, setDept] = useState(initial?.dept ?? DEPTS[0]?.id ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const toast = useToast();
  const submit = () => {
    toast.warn(SERVER_GROUPS_NOT_WIRED);
    onDone();
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Layers className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая группа" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="dept">
          <select className="input" value={dept} onChange={(e) => setDept(e.target.value)}>
            {DEPTS.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </FormRow>
        <FormRow label="note">
          <textarea className="input" value={note} onChange={(e) => setNote(e.target.value)} />
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
