/**
 * Live-управление правилами аудита loging_service.
 *
 * Список правил слева, карточка/форма справа. CRUD идёт в loging_service
 * через `@/api/loging/rules`. Весь раздел правил — только `loging_admin`:
 * backend закрывает и чтение (`GET /rules`), и запись одной dependency
 * `require_admin`. `loging_reader` / `account_admin` получают 403 даже на
 * список, поэтому таким ролям показываем заглушку и не дёргаем API.
 *
 * Контракт формы повторяет валидацию backend'а:
 *   - `effect` обязателен;
 *   - `effect_severity` обязателен и допустим ТОЛЬКО при OVERRIDE_SEVERITY;
 *   - `match_*` опциональны (пусто = «любое»).
 */
import { useState } from "react";
import {
  Filter,
  Plus,
  Trash2,
  Edit3,
  AlertCircle,
  ArrowLeft,
  ShieldAlert,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
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

export function LogRulesLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const { confirm } = useConfirm();
  // Backend `/rules` (вкл. GET-список) закрыт `require_admin` строго на
  // `loging_admin`. `account_admin` и `loging_reader` ловят 403 даже на
  // чтение — для них вместо API-ошибки показываем явную заглушку.
  const isAdmin = persona.platform_role === "logging_admin";
  const canWrite = isAdmin;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "new" | "edit">("view");

  const rulesQ = useQuery(
    () => (isAdmin ? listRules({ limit: 200 }) : Promise.resolve(null)),
    [isAdmin],
  );
  const rules = rulesQ.data?.items ?? [];
  const selected = rules.find((r) => r.id === selectedId) ?? null;

  async function handleSubmit(
    body: RuleCreateRequest,
    editingId: string | null,
  ) {
    try {
      if (editingId) {
        const updated = await updateRule(editingId, body);
        toast.success(`Правило ${updated.name} обновлено`);
        setSelectedId(updated.id);
      } else {
        const created = await createRule(body);
        toast.success(`Правило ${created.name} создано`);
        setSelectedId(created.id);
      }
      setMode("view");
      rulesQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Сохранение не удалось"));
    }
  }

  async function handleDelete(rule: Rule) {
    const ok = await confirm({
      title: "Удалить правило",
      message: `Удалить правило "${rule.name}"?`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteRule(rule.id);
      toast.success(`Правило ${rule.name} удалено`);
      setSelectedId(null);
      setMode("view");
      rulesQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0 text-xs text-dim flex items-center justify-between">
        <span>Правила аудита</span>
        <span>{rulesQ.data?.total ?? rules.length} шт</span>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {rulesQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {rulesQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(rulesQ.error, "Список не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => rulesQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!rulesQ.loading && !rulesQ.error && rules.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Правил нет.
          </div>
        )}
        <div className="px-2 flex flex-col gap-0.5">
          {rules.map((r) => (
            <button
              key={r.id}
              onClick={() => {
                setSelectedId(r.id);
                setMode("view");
              }}
              className={`cred-row text-left ${
                selectedId === r.id ? "active" : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <Filter
                  className={`w-4 h-4 ${
                    selectedId === r.id ? "text-accent" : "text-dim"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">{r.name}</div>
                  <div className="text-[11px] text-dim flex items-center gap-1 truncate">
                    <span className="mono">{r.match_action ?? "*"}</span>
                    <span>→</span>
                    <span className="mono">{r.effect}</span>
                  </div>
                </div>
                <span
                  className={`badge ${r.is_active ? "badge-ok" : "badge-warn"}`}
                >
                  {r.is_active ? "active" : "muted"}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
      {canWrite && (
        <div className="border-t border-token p-3 shrink-0">
          <button
            className="btn btn-primary w-full flex items-center justify-center gap-2"
            onClick={() => {
              setMode("new");
              setSelectedId(null);
            }}
          >
            <Plus className="w-4 h-4" /> Создать правило
          </button>
        </div>
      )}
    </aside>
  );

  if (!isAdmin) {
    return (
      <Shell breadcrumb="loging_service / rules">
        <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
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
        </section>
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="loging_service / rules" middle={aside}>
      {mode === "new" && canWrite ? (
        <RuleForm
          key="new"
          rule={null}
          onCancel={() => setMode("view")}
          onSubmit={(body) => handleSubmit(body, null)}
        />
      ) : mode === "edit" && selected && canWrite ? (
        <RuleForm
          key={selected.id}
          rule={selected}
          onCancel={() => setMode("view")}
          onSubmit={(body) => handleSubmit(body, selected.id)}
        />
      ) : selected ? (
        <RuleView
          rule={selected}
          canWrite={canWrite}
          onEdit={() => setMode("edit")}
          onDelete={() => handleDelete(selected)}
        />
      ) : (
        <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
          <div className="empty-card max-w-md text-center">
            <Filter className="w-10 h-10 mx-auto text-dim mb-3" />
            <div className="text-sm text-dim">
              Выберите правило слева
              {canWrite ? " или создайте новое." : "."}
            </div>
          </div>
        </section>
      )}
    </Shell>
  );
}

function RuleView({
  rule,
  canWrite,
  onEdit,
  onDelete,
}: {
  rule: Rule;
  canWrite: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{rule.name}</h1>
            <span className={`badge ${rule.is_active ? "badge-ok" : "badge-warn"}`}>
              {rule.is_active ? "active" : "muted"}
            </span>
            <span className="badge">{rule.effect}</span>
          </div>
          {rule.description && (
            <div className="text-sm text-dim mt-1">{rule.description}</div>
          )}
        </div>
        {canWrite && (
          <div className="flex items-center gap-2 shrink-0">
            <button className="btn flex items-center gap-1" onClick={onEdit}>
              <Edit3 className="w-4 h-4" /> Изменить
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Удалить
            </button>
          </div>
        )}
      </div>
      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            Совпадение
          </div>
          <div className="text-sm">
            <Row label="service" value={rule.match_service} />
            <Row label="action" value={rule.match_action} />
            <Row label="status" value={rule.match_status} />
            <Row label="severity" value={rule.match_severity} />
            <Row
              label="allowed"
              value={
                rule.match_allowed === null
                  ? null
                  : rule.match_allowed
                    ? "true"
                    : "false"
              }
            />
          </div>
        </div>
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            Эффект
          </div>
          <div className="text-sm">
            <Row label="effect" value={rule.effect} />
            <Row label="effect_severity" value={rule.effect_severity} />
            <Row label="priority" value={String(rule.priority)} />
            <Row label="id" value={rule.id} />
          </div>
        </div>
      </div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="stat-row">
      <span className="text-dim">{label}</span>
      {value ? (
        <span className="mono text-xs">{value}</span>
      ) : (
        <span className="text-dim">любое</span>
      )}
    </div>
  );
}

function RuleForm({
  rule,
  onCancel,
  onSubmit,
}: {
  rule: Rule | null;
  onCancel: () => void;
  onSubmit: (body: RuleCreateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState(rule?.name ?? "");
  const [description, setDescription] = useState(rule?.description ?? "");
  const [isActive, setIsActive] = useState(rule?.is_active ?? true);
  const [priority, setPriority] = useState(String(rule?.priority ?? 100));
  const [matchService, setMatchService] = useState(rule?.match_service ?? "");
  const [matchAction, setMatchAction] = useState(rule?.match_action ?? "");
  const [matchStatus, setMatchStatus] = useState(rule?.match_status ?? "");
  const [matchSeverity, setMatchSeverity] = useState(rule?.match_severity ?? "");
  // tri-state: "" = любой, "true" = только allowed, "false" = только denied.
  const [matchAllowed, setMatchAllowed] = useState(
    rule?.match_allowed === null || rule?.match_allowed === undefined
      ? ""
      : String(rule.match_allowed),
  );
  const [effect, setEffect] = useState<RuleEffect>(
    (rule?.effect as RuleEffect) ?? "SUPPRESS",
  );
  const [effectSeverity, setEffectSeverity] = useState(
    rule?.effect_severity ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  const needsEffectSeverity = effect === "OVERRIDE_SEVERITY";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    if (!name.trim()) return;
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
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 w-full">
        <div className="flex items-center gap-2 mb-4">
          <button
            className="btn btn-ghost flex items-center gap-1"
            onClick={onCancel}
            type="button"
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </button>
          <div className="text-sm text-dim">
            {rule ? "Редактирование правила" : "Создание правила"}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Field
            label="name *"
            help="Уникальное имя правила (печатные ASCII, до 128 символов)."
          >
            <input
              className="surface-2 border border-token rounded px-2 py-1 w-full"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={128}
              placeholder="suppress-healthchecks"
            />
          </Field>
          <Field
            label="description"
            help="Произвольное пояснение к правилу. Видно в админ-UI и в CSV-экспорте."
          >
            <input
              className="surface-2 border border-token rounded px-2 py-1 w-full"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={1024}
              placeholder="опционально"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <ServiceActionSelect
              service={matchService}
              action={matchAction}
              onServiceChange={setMatchService}
              onActionChange={setMatchAction}
              selectClassName="surface-2 border border-token rounded px-2 py-1 w-full mono text-sm"
            />
            <Field
              label="match_status"
              help="Исход события (success / failure / denied / warning). Пусто — любой исход."
            >
              <select
                className="surface-2 border border-token rounded px-2 py-1 w-full"
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
            </Field>
            <Field
              label="match_severity"
              help="Уровень важности события (TRACE…CRITICAL). Пусто — любой уровень."
            >
              <select
                className="surface-2 border border-token rounded px-2 py-1 w-full"
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
            </Field>
            <Field
              label="match_allowed"
              help="Фильтр по флагу доступа: только разрешённые (allowed) или только отклонённые (denied) события. Пусто — оба."
            >
              <select
                className="surface-2 border border-token rounded px-2 py-1 w-full"
                value={matchAllowed}
                onChange={(e) => setMatchAllowed(e.target.value)}
              >
                <option value="">любой</option>
                <option value="true">только allowed</option>
                <option value="false">только denied</option>
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="effect *"
              help="Что сделать с совпавшим событием: SUPPRESS — отбросить, ALLOW — пропустить как есть, OVERRIDE_SEVERITY — переписать уровень важности."
            >
              <select
                className="surface-2 border border-token rounded px-2 py-1 w-full"
                value={effect}
                onChange={(e) => setEffect(e.target.value as RuleEffect)}
              >
                {EFFECTS.map((eff) => (
                  <option key={eff} value={eff}>
                    {eff}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label={`effect_severity${needsEffectSeverity ? " *" : ""}`}
              help="Новый уровень важности для эффекта OVERRIDE_SEVERITY. Для остальных эффектов не используется."
            >
              <select
                className="surface-2 border border-token rounded px-2 py-1 w-full"
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
            </Field>
            <Field
              label="priority"
              help="Порядок применения правил: чем больше число, тем раньше срабатывает правило (1–1000)."
            >
              <input
                className="surface-2 border border-token rounded px-2 py-1 w-full"
                type="number"
                min={1}
                max={1000}
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              />
            </Field>
            <label className="flex items-center gap-2 text-sm mt-5">
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

          <div className="flex items-center gap-2 mt-2">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={
                submitting ||
                !name.trim() ||
                (needsEffectSeverity && !effectSeverity)
              }
            >
              {submitting ? "Сохраняем…" : "Сохранить"}
            </button>
            <button type="button" className="btn" onClick={onCancel}>
              Отмена
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-dim text-xs flex items-center gap-1">
        {label}
        {help && <HelpTooltip text={help} label={`Справка: ${label}`} />}
      </span>
      {children}
    </label>
  );
}
