import { useState } from "react";
import { AlertTriangle, Edit3, Filter, ShieldAlert, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import {
  createRule,
  deleteRule,
  listRules,
  updateRule,
} from "@/api/loging/rules";
import { HelpTooltip } from "@/components/ui/HelpTooltip";
import { ServiceActionSelect } from "@/components/ui/ServiceActionSelect";
import type {
  Rule,
  RuleCreateRequest,
  RuleEffect,
  Severity,
} from "@/api/loging/types";
import { LOG_RULES, type MockRule } from "@/mocks/log";
import {
  FormRow,
  InlineEditor,
  StatRow,
  useInlineState,
} from "./_inline";

const EFFECTS: RuleEffect[] = ["SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"];
const SEVERITIES: Severity[] = [
  "TRACE",
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
];
const STATUSES = ["success", "failure", "denied", "warning"] as const;

export function ServicesLogingRules() {
  const mockMode = useMockMode();
  if (!mockMode) return <LiveRules />;
  return <MockRules />;
}

// ─── live ───────────────────────────────────────────────────────────────────

function LiveRules() {
  const { persona } = usePersona();
  const toast = useToast();
  const confirm = useConfirm();
  // Весь `/rules` (включая GET-список) закрыт backend'ом строго на
  // `loging_admin`. `account_admin`, доходящий сюда по admin-каталогу, и
  // `loging_reader` ловят 403 даже на чтение — для них не дёргаем API.
  const isAdmin = persona.platform_role === "logging_admin";

  const rulesQ = useQuery(
    () => (isAdmin ? listRules({ limit: 200 }) : Promise.resolve(null)),
    [isAdmin],
    { enabled: isAdmin },
  );
  const rules = rulesQ.data?.items ?? [];

  async function handleSubmit(
    body: RuleCreateRequest,
    editingId: string | null,
  ) {
    if (editingId) {
      const updated = await updateRule(editingId, body);
      toast.success(`Правило ${updated.name} обновлено`);
    } else {
      const created = await createRule(body);
      toast.success(`Правило ${created.name} создано`);
    }
    rulesQ.refetch();
  }

  async function handleDelete(rule: Rule) {
    const ok = await confirm.confirm({
      message: `Удалить правило "${rule.name}"?`,
      danger: true,
      confirmLabel: "Удалить",
    });
    if (!ok) return;
    try {
      await deleteRule(rule.id);
      toast.success(`Правило ${rule.name} удалено`);
      rulesQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  if (!isAdmin) {
    return (
      <div className="flex-1 min-h-0 flex items-center justify-center p-8">
        <div className="empty-card max-w-md text-center">
          <ShieldAlert className="w-10 h-10 mx-auto text-dim mb-3" />
          <div className="text-sm">
            Управление правилами аудита доступно только роли{" "}
            <span className="mono">loging_admin</span>.
          </div>
          <div className="text-xs text-dim mt-2">
            Просмотр и редактирование правил закрыты для loging_reader и
            account_admin на стороне backend.
          </div>
        </div>
      </div>
    );
  }

  return (
    <InlineEditor
      title="Правила аудита"
      icon={Filter}
      hint="loging_service · severity / suppress · CRUD требует loging_admin"
      items={rules}
      getId={(r) => r.id}
      loading={rulesQ.loading}
      error={rulesQ.error ? apiErrMsg(rulesQ.error, "Список не загрузился") : null}
      onRetry={() => rulesQ.refetch()}
      canEdit
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Filter className={`w-4 h-4 ${item.is_active ? "text-accent" : "text-dim"}`} />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{item.name}</div>
              <div className="text-[11px] text-dim flex items-center gap-1 truncate">
                <span className="mono">{item.match_action ?? "*"}</span>
                <span>→</span>
                <span className="mono">{item.effect}</span>
              </div>
            </div>
            <span className={`badge ${item.is_active ? "badge-ok" : "badge-warn"}`}>
              {item.is_active ? "active" : "muted"}
            </span>
          </div>
        </button>
      )}
      renderDetail={(r, { editing, onClose }) => {
        if (editing)
          return (
            <RuleForm
              initial={r}
              onCancel={onClose}
              onSubmit={async (body) => {
                await handleSubmit(body, r.id);
                onClose();
              }}
            />
          );
        return <RuleView rule={r} onDelete={() => handleDelete(r)} />;
      }}
      renderCreate={(onClose) => (
        <RuleForm
          onCancel={onClose}
          onSubmit={async (body) => {
            await handleSubmit(body, null);
            onClose();
          }}
        />
      )}
    />
  );
}

function RuleView({ rule, onDelete }: { rule: Rule; onDelete: () => void }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <Filter className="w-4 h-4 text-accent" /> {rule.name}
          <span className={`badge ${rule.is_active ? "badge-ok" : "badge-warn"}`}>
            {rule.is_active ? "active" : "muted"}
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <button className="btn flex items-center gap-1" onClick={() => startEdit(rule.id)}>
            <Edit3 className="w-4 h-4" /> Edit
          </button>
          <button className="btn btn-danger flex items-center gap-1" onClick={onDelete}>
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        </div>
      </div>
      {rule.description && (
        <div className="text-sm text-dim mb-3">{rule.description}</div>
      )}
      <StatRow k="match_service" v={<MatchVal v={rule.match_service} />} />
      <StatRow k="match_action" v={<MatchVal v={rule.match_action} />} />
      <StatRow k="match_status" v={<MatchVal v={rule.match_status} />} />
      <StatRow k="match_severity" v={<MatchVal v={rule.match_severity} />} />
      <StatRow
        k="match_allowed"
        v={
          <MatchVal
            v={
              rule.match_allowed === null
                ? null
                : rule.match_allowed
                  ? "true"
                  : "false"
            }
          />
        }
      />
      <StatRow k="effect" v={<span className="mono">{rule.effect}</span>} />
      <StatRow k="effect_severity" v={<MatchVal v={rule.effect_severity} />} />
      <StatRow k="priority" v={<span className="mono">{rule.priority}</span>} />
      <StatRow k="id" v={<span className="mono text-xs">{rule.id}</span>} />
    </div>
  );
}

function MatchVal({ v }: { v: string | null }) {
  return v ? <span className="mono text-xs">{v}</span> : <span className="text-dim">любое</span>;
}

function RuleForm({
  initial,
  onCancel,
  onSubmit,
}: {
  initial?: Rule;
  onCancel: () => void;
  onSubmit: (body: RuleCreateRequest) => void | Promise<void>;
}) {
  const toast = useToast();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);
  const [priority, setPriority] = useState(String(initial?.priority ?? 100));
  const [matchService, setMatchService] = useState(initial?.match_service ?? "");
  const [matchAction, setMatchAction] = useState(initial?.match_action ?? "");
  const [matchStatus, setMatchStatus] = useState(initial?.match_status ?? "");
  const [matchSeverity, setMatchSeverity] = useState(initial?.match_severity ?? "");
  // tri-state: "" = любой, "true" = только allowed, "false" = только denied.
  const [matchAllowed, setMatchAllowed] = useState(
    initial?.match_allowed === null || initial?.match_allowed === undefined
      ? ""
      : String(initial.match_allowed),
  );
  const [effect, setEffect] = useState<RuleEffect>(
    (initial?.effect as RuleEffect) ?? "SUPPRESS",
  );
  const [effectSeverity, setEffectSeverity] = useState(
    initial?.effect_severity ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  const needsEffectSeverity = effect === "OVERRIDE_SEVERITY";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !name.trim()) return;
    const body: RuleCreateRequest = {
      name: name.trim(),
      description: description.trim() || null,
      is_active: isActive,
      priority: Number(priority) || 100,
      match_service: matchService.trim() || null,
      match_action: matchAction.trim() || null,
      match_status: (matchStatus || null) as RuleCreateRequest["match_status"],
      match_severity: (matchSeverity || null) as Severity | null,
      match_allowed: matchAllowed === "" ? null : matchAllowed === "true",
      effect,
      // effect_severity допустим ТОЛЬКО при OVERRIDE_SEVERITY — иначе backend
      // вернёт EFFECT_SEVERITY_NOT_ALLOWED. Чистим поле для прочих эффектов.
      effect_severity: needsEffectSeverity
        ? ((effectSeverity || null) as Severity | null)
        : null,
    };
    setSubmitting(true);
    try {
      await onSubmit(body);
    } catch (err) {
      toast.error(apiErrMsg(err, "Сохранение не удалось"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card w-full flex flex-col gap-3">
      <h3 className="font-semibold flex items-center gap-2">
        <Filter className="w-4 h-4 text-accent" />
        {initial ? `Edit · ${initial.name}` : "Новое правило"}
      </h3>
      <FormRow
        label="name *"
        help="Уникальное имя правила (печатные ASCII, до 128 символов)."
      >
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={128}
          placeholder="suppress-healthchecks"
        />
      </FormRow>
      <FormRow
        label="description"
        help="Произвольное пояснение к правилу. Видно в админ-UI и в CSV-экспорте."
      >
        <input
          className="input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={1024}
          placeholder="опционально"
        />
      </FormRow>
      <div className="grid grid-cols-2 gap-3">
        <ServiceActionSelect
          service={matchService}
          action={matchAction}
          onServiceChange={setMatchService}
          onActionChange={setMatchAction}
          selectClassName="input mono"
        />
        <FormRow
          label="match_status"
          help="Исход события (success / failure / denied / warning). Пусто — любой исход."
        >
          <select
            className="input"
            value={matchStatus}
            onChange={(e) => setMatchStatus(e.target.value)}
          >
            <option value="">любой</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="match_severity"
          help="Уровень важности события (TRACE…CRITICAL). Пусто — любой уровень."
        >
          <select
            className="input"
            value={matchSeverity}
            onChange={(e) => setMatchSeverity(e.target.value)}
          >
            <option value="">любая</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="match_allowed"
          help="Фильтр по флагу доступа: только разрешённые (allowed) или только отклонённые (denied) события. Пусто — оба."
        >
          <select
            className="input"
            value={matchAllowed}
            onChange={(e) => setMatchAllowed(e.target.value)}
          >
            <option value="">любой</option>
            <option value="true">только allowed</option>
            <option value="false">только denied</option>
          </select>
        </FormRow>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <FormRow
          label="effect *"
          help="Что сделать с совпавшим событием: SUPPRESS — отбросить, ALLOW — пропустить как есть, OVERRIDE_SEVERITY — переписать уровень важности."
        >
          <select
            className="input"
            value={effect}
            onChange={(e) => setEffect(e.target.value as RuleEffect)}
          >
            {EFFECTS.map((eff) => (
              <option key={eff} value={eff}>
                {eff}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label={`effect_severity${needsEffectSeverity ? " *" : ""}`}
          help="Новый уровень важности для эффекта OVERRIDE_SEVERITY. Для остальных эффектов не используется."
        >
          <select
            className="input"
            value={effectSeverity}
            onChange={(e) => setEffectSeverity(e.target.value)}
            disabled={!needsEffectSeverity}
            required={needsEffectSeverity}
          >
            <option value="">—</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow
          label="priority"
          help="Порядок применения правил: чем больше число, тем раньше срабатывает правило (1–1000)."
        >
          <input
            className="input"
            type="number"
            min={1}
            max={1000}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </FormRow>
        <label className="checkbox-row mt-5">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          <span>is_active</span>
          <HelpTooltip
            text="Включено ли правило. Неактивное правило хранится, но не применяется к событиям."
            label="Справка: is_active"
          />
        </label>
      </div>
      <div className="mt-2 flex gap-2 justify-end">
        <button type="button" className="btn" onClick={onCancel}>
          Отмена
        </button>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={
            submitting || !name.trim() || (needsEffectSeverity && !effectSeverity)
          }
        >
          {submitting ? "Сохраняем…" : initial ? "Сохранить" : "Создать"}
        </button>
      </div>
    </form>
  );
}

// ─── mock ───────────────────────────────────────────────────────────────────

type AlertRule = MockRule;
const RULES = LOG_RULES;

function MockRules() {
  const { persona } = usePersona();
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "logging_admin";

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
        if (editing) return <MockRuleForm initial={r} onDone={onClose} mode="edit" />;
        return <MockRuleView rule={r} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <MockRuleForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function MockRuleView({ rule, canEdit }: { rule: AlertRule; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  return (
    <div className="card w-full">
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

function MockRuleForm({
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
    <div className="card w-full">
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
