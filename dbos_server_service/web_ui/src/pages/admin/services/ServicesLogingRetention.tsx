import { useState } from "react";
import {
  AlertCircle,
  Archive,
  Clock,
  Edit3,
  Save,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import {
  disableRetention,
  getRetention,
  setRetention,
} from "@/api/loging/retention";
import type { RetentionPolicyPutRequest, Severity } from "@/api/loging/types";
import { LOG_RETENTION, type MockRetention } from "@/mocks/log";
import {
  FormRow,
  InlineEditor,
  StatRow,
  useInlineState,
} from "./_inline";

const SEVERITIES: Severity[] = [
  "TRACE",
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
];

export function ServicesLogingRetention() {
  const mockMode = useMockMode();
  if (!mockMode) return <LiveRetention />;
  return <MockRetention_ />;
}

// ─── live ───────────────────────────────────────────────────────────────────

function LiveRetention() {
  const { persona } = usePersona();
  const toast = useToast();
  const confirm = useConfirm();
  // Весь `/retention` (включая GET) закрыт router-level `require_admin` на
  // `loging_admin`. account_admin доходит сюда по admin-каталогу, но даже
  // чтение отдаёт 403 — для него не дёргаем API.
  const canWrite = persona.platform_role === "logging_admin";

  const policyQ = useQuery(
    () => (canWrite ? getRetention() : Promise.resolve(null)),
    [canWrite],
    { enabled: canWrite },
  );
  const policy = policyQ.data ?? null;

  const [retainDays, setRetainDays] = useState("");
  const [description, setDescription] = useState("");
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [serviceFilter, setServiceFilter] = useState("");
  const [busy, setBusy] = useState(false);

  // Подхватываем текущее значение в форму, когда GET вернул политику и поле
  // ещё не трогали. retain_days вне диапазона невозможен — backend гарантирует
  // [30, 3650].
  if (policy && retainDays === "") {
    setRetainDays(String(policy.retain_days));
    if (policy.description) setDescription(policy.description);
  }

  async function handleSave() {
    if (busy) return;
    const days = Number(retainDays);
    if (!Number.isFinite(days) || days < 30 || days > 3650) {
      toast.warn("retain_days должен быть в диапазоне [30, 3650]");
      return;
    }
    const body: RetentionPolicyPutRequest = {
      retain_days: days,
      description: description.trim() || null,
      severity_filter: severityFilter.length ? severityFilter : null,
      service_filter: parseServices(serviceFilter),
    };
    setBusy(true);
    try {
      await setRetention(body);
      toast.success("Retention-политика сохранена");
      policyQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Сохранение не удалось"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    if (busy) return;
    const ok = await confirm.confirm({
      message:
        "Отключить ротацию? События будут храниться вечно, пока не задать новую политику.",
      danger: true,
      confirmLabel: "Отключить",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await disableRetention();
      toast.success("Ротация отключена");
      setRetainDays("");
      setDescription("");
      setSeverityFilter([]);
      setServiceFilter("");
      policyQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Отключение не удалось"));
    } finally {
      setBusy(false);
    }
  }

  function toggleSeverity(s: Severity) {
    setSeverityFilter((cur) =>
      cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s],
    );
  }

  if (!canWrite) {
    return (
      <div className="flex-1 min-h-0 flex items-center justify-center p-8">
        <div className="empty-card max-w-md text-center">
          <ShieldAlert className="w-10 h-10 mx-auto text-dim mb-3" />
          <div className="text-sm">
            Управление retention-политикой доступно только роли{" "}
            <span className="mono">loging_admin</span>.
          </div>
          <div className="text-xs text-dim mt-2">
            Backend закрывает весь раздел retention (включая просмотр) для
            остальных ролей.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Archive className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Retention · loging_service
          </h1>
          <div className="text-xs text-dim">
            срок хранения audit-событий · фоновая ротация 00:00 MSK · события
            loging_service защищены всегда
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {policyQ.loading && (
          <div className="card text-sm text-dim max-w-3xl">Загрузка…</div>
        )}
        {policyQ.error && (
          <div className="alert alert-danger flex items-start gap-2 mb-4 max-w-3xl">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-sm">
              <div>{apiErrMsg(policyQ.error, "Политика не загрузилась")}</div>
              <button className="btn btn-ghost mt-2" onClick={() => policyQ.refetch()}>
                Повторить
              </button>
            </div>
          </div>
        )}

        {!policyQ.loading && !policyQ.error && (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Clock className="w-3 h-3" /> Активная политика
              </div>
              {policy ? (
                <>
                  <div className="stat-big">{policy.retain_days} days</div>
                  <div className="text-sm mt-3">
                    <StatRow k="severity" v={<span className="mono">{policy.severity ?? "все"}</span>} />
                    <StatRow k="service" v={<span className="mono">{policy.service ?? "все"}</span>} />
                    <StatRow k="id" v={<span className="mono text-xs">{policy.id}</span>} />
                  </div>
                </>
              ) : (
                <>
                  <div className="stat-big text-dim">ротация выключена</div>
                  <div className="text-xs text-dim mt-2">
                    События хранятся бессрочно. Задайте политику справа.
                  </div>
                </>
              )}
            </div>

            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Save className="w-3 h-3" /> Задать политику
              </div>
              <div className="flex flex-col gap-3 mt-3">
                <FormRow label="retain_days * (30…3650)">
                  <input
                    className="input"
                    type="number"
                    min={30}
                    max={3650}
                    value={retainDays}
                    onChange={(e) => setRetainDays(e.target.value)}
                    placeholder="90"
                  />
                </FormRow>
                <FormRow label="description">
                  <input
                    className="input"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    maxLength={256}
                    placeholder="опционально"
                  />
                </FormRow>
                <FormRow label="severity_filter (пусто = все)">
                  <div className="flex flex-wrap gap-1">
                    {SEVERITIES.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => toggleSeverity(s)}
                        className={`badge ${severityFilter.includes(s) ? "badge-ok" : ""}`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </FormRow>
                <FormRow label="service_filter (через запятую, пусто = все)">
                  <input
                    className="input mono"
                    value={serviceFilter}
                    onChange={(e) => setServiceFilter(e.target.value)}
                    placeholder="auth_service, server_service"
                  />
                </FormRow>
                <div className="flex items-center gap-2 mt-1">
                  <button
                    className="btn btn-primary flex items-center gap-1"
                    onClick={handleSave}
                    disabled={busy}
                  >
                    <Save className="w-4 h-4" /> {busy ? "Сохраняем…" : "Сохранить"}
                  </button>
                  {policy && (
                    <button
                      className="btn btn-danger flex items-center gap-1"
                      onClick={handleDisable}
                      disabled={busy}
                    >
                      <Trash2 className="w-4 h-4" /> Отключить
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Парсит CSV-строку сервисов в массив (или null, если пусто). */
function parseServices(raw: string): string[] | null {
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length ? parts : null;
}

// ─── mock ───────────────────────────────────────────────────────────────────

type RetentionPolicyMock = MockRetention;
const POLICIES = LOG_RETENTION;

function MockRetention_() {
  const { persona } = usePersona();
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "logging_admin";

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
        if (editing) return <MockRetentionForm initial={p} onDone={onClose} mode="edit" />;
        return <MockRetentionView policy={p} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <MockRetentionForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function MockRetentionView({ policy, canEdit }: { policy: RetentionPolicyMock; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card w-full">
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

function MockRetentionForm({
  initial,
  onDone,
  mode,
}: {
  initial?: RetentionPolicyMock;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [scope, setScope] = useState(initial?.scope ?? "");
  const [days, setDays] = useState(initial?.days ?? 30);

  return (
    <div className="card w-full">
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
