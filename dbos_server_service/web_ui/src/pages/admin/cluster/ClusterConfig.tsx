import { useMemo, useState } from "react";
import { Cog, ShieldAlert, Pencil, Check, X, History } from "lucide-react";
import {
  GLOBAL_CONFIG_ITEMS,
  CLUSTER_CONFIG_AUDIT,
  type GlobalConfigItem,
  type ClusterConfigAuditEntry,
} from "@/mocks/cluster";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { isReadOnlyForCluster } from "@/lib/rbac";
import { formatMskShort } from "@/lib/datetime";

interface PendingEdit {
  key: string;
  old_value: string;
  new_value: string;
}

export function ClusterConfig() {
  const { persona } = usePersona();
  const toast = useToast();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();

  const [items, setItems] = useState<GlobalConfigItem[]>(() =>
    GLOBAL_CONFIG_ITEMS.map((c) => ({ ...c })),
  );
  const [audit, setAudit] = useState<ClusterConfigAuditEntry[]>(() => [
    ...CLUSTER_CONFIG_AUDIT,
  ]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draftValue, setDraftValue] = useState<string>("");
  const [pending, setPending] = useState<PendingEdit | null>(null);
  const [applying, setApplying] = useState(false);

  const startEdit = (item: GlobalConfigItem) => {
    setEditingKey(item.key);
    setDraftValue(item.value);
  };

  const cancelEdit = () => {
    setEditingKey(null);
    setDraftValue("");
  };

  const requestSave = (item: GlobalConfigItem) => {
    if (draftValue === item.value) {
      cancelEdit();
      return;
    }
    setPending({
      key: item.key,
      old_value: item.value,
      new_value: draftValue,
    });
  };

  const confirmApply = () => {
    if (!pending) return;
    setApplying(true);
    // real impl will call PATCH /api/cluster/config/{key};
    // mock keeps state in-memory and pushes audit entry.
    window.setTimeout(() => {
      const entry: ClusterConfigAuditEntry = {
        ts: new Date().toISOString(),
        key: pending.key,
        old_value: pending.old_value,
        new_value: pending.new_value,
        applied_by: persona.username,
      };
      setItems((prev) =>
        prev.map((c) =>
          c.key === pending.key ? { ...c, value: pending.new_value } : c,
        ),
      );
      setAudit((prev) => [entry, ...prev].slice(0, 50));
      CLUSTER_CONFIG_AUDIT.unshift(entry);
      const target = GLOBAL_CONFIG_ITEMS.find((c) => c.key === pending.key);
      if (target) target.value = pending.new_value;
      toast.success(`${pending.key} обновлён`);
      setApplying(false);
      setPending(null);
      setEditingKey(null);
      setDraftValue("");
    }, 800);
  };

  const cancelConfirm = () => {
    if (applying) return;
    setPending(null);
  };

  const auditTail = useMemo(() => audit.slice(0, 10), [audit]);

  if (!mockMode) {
    return (
      <div className="space-y-4 max-w-3xl">
        {readonly && (
          <div className="readonly-bar">
            <ShieldAlert className="w-3.5 h-3.5" />
            <span>
              <b>Read-only · logging_reader.</b> Эта секция в любом случае
              read-only — здесь только просмотр.
            </span>
          </div>
        )}
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <Cog className="w-4 h-4 text-dim" /> Глобальные настройки env /
            NetworkPolicy
          </h3>
          <div className="empty-card text-xs">
            Endpoint live-patch конфига ещё не подключён к UI. Сейчас правка
            возможна только через Helm-values + PR в инфра-репо.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 max-w-3xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Эта секция в любом случае
            read-only — здесь только просмотр.
          </span>
        </div>
      )}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Cog className="w-4 h-4 text-accent" /> Глобальные настройки env /
            NetworkPolicy
          </h3>
          <span className="badge">{readonly ? "read-only" : "live patch"}</span>
        </div>
        <table className="mini">
          <thead>
            <tr>
              <th>key</th>
              <th>value</th>
              <th>note</th>
              {!readonly && <th style={{ width: 110 }}></th>}
            </tr>
          </thead>
          <tbody>
            {items.map((c) => {
              const isEditing = editingKey === c.key;
              return (
                <tr key={c.key}>
                  <td className="mono">{c.key}</td>
                  <td className="mono">
                    {isEditing ? (
                      <input
                        className="input"
                        style={{ height: 26, padding: "2px 8px", fontSize: 12 }}
                        value={draftValue}
                        onChange={(e) => setDraftValue(e.target.value)}
                        autoFocus
                      />
                    ) : (
                      c.value
                    )}
                  </td>
                  <td className="text-dim">{c.note}</td>
                  {!readonly && (
                    <td>
                      {isEditing ? (
                        <div className="flex gap-1">
                          <button
                            className="btn btn-sm btn-primary"
                            onClick={() => requestSave(c)}
                            title="Save"
                          >
                            <Check className="w-3 h-3" />
                          </button>
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={cancelEdit}
                            title="Cancel"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      ) : (
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => startEdit(c)}
                          disabled={editingKey !== null}
                        >
                          <Pencil className="w-3 h-3" /> Edit
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 text-[11px] text-dim">
          Эти значения раньше выставлялись только через Helm-values + PR в репо
          инфры. Live-patch применяется к работающему кластеру без рестарта.
        </div>
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <History className="w-4 h-4 text-accent" /> Последние изменения
          </h3>
          <span className="badge">{auditTail.length}</span>
        </div>
        {auditTail.length === 0 ? (
          <div className="text-[12px] text-dim">
            Изменений ещё не было — после первого Save запись появится тут.
          </div>
        ) : (
          <table className="mini">
            <thead>
              <tr>
                <th>ts</th>
                <th>key</th>
                <th>old → new</th>
                <th>by</th>
              </tr>
            </thead>
            <tbody>
              {auditTail.map((e, idx) => (
                <tr key={`${e.ts}-${e.key}-${idx}`}>
                  <td className="mono text-dim">{formatMskShort(e.ts)}</td>
                  <td className="mono">{e.key}</td>
                  <td className="mono">
                    <span className="text-dim">{e.old_value}</span>
                    {" → "}
                    <span>{e.new_value}</span>
                  </td>
                  <td className="mono">{e.applied_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {pending && (
        <div className="modal-overlay" onClick={cancelConfirm}>
          <div
            className="modal-content"
            style={{ maxWidth: 480 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h3 className="font-semibold">Применить изменение?</h3>
            </div>
            <div className="modal-body space-y-3">
              <div className="text-[12px]">
                <span className="mono">{pending.key}</span>
              </div>
              <div className="mono text-[12px]">
                <span className="text-dim">{pending.old_value}</span>
                {" → "}
                <span>{pending.new_value}</span>
              </div>
              <div
                className="text-[12px]"
                style={{ color: "var(--warn, var(--danger))" }}
              >
                Применится к live cluster немедленно. Откат — только повторным
                патчем.
              </div>
            </div>
            <div className="modal-footer">
              <button
                className="btn btn-sm"
                onClick={cancelConfirm}
                disabled={applying}
              >
                Отмена
              </button>
              <button
                className="btn btn-sm btn-primary"
                onClick={confirmApply}
                disabled={applying}
              >
                {applying ? "Применяю…" : "Применить"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
