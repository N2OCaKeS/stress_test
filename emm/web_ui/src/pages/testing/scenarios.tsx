/**
 * Раздел «Сценарии» — многостендовые сценарии рядом с
 * каталогом тестов.
 *
 * Сценарий — стенды пула (сервер или ВМ, ролей нет: стенды выбираются в самом
 * сценарии) и упорядоченные действия: прогнать тест на стенде, подготовить
 * стенд, подождать. Порядок действий — перетаскиванием (или стрелками).
 * Превью собирает для каждого `run_test` то же задание воркеру, что и превью
 * запуска теста, со стендом действия; адреса других стендов в
 * командах подставляет переменная с источником `stand_ref`.
 *
 * Запуск: РЦ, ядро, режим → бронь всех стендов «всё или ничего»,
 * подготовка, действия по порядку; карточки запусков со стендами, действиями,
 * ожиданием занятых стендов и остановкой.
 * Источник истины — `/api/testing/v1/scenarios`, `/api/testing/v1/scenario-runs`.
 */
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Eye, GripVertical, Loader2, Play, Plus, Square, Trash2, Workflow } from "lucide-react";

import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listOsVersions } from "@/api/server/osVersions";
import {
  createScenario,
  deleteScenario,
  getScenario,
  listScenarios,
  listScenarioRuns,
  previewScenario,
  startScenarioRun,
  stopScenarioRun,
  updateScenario,
} from "@/api/testing/scenarios";
import { listNamedTestStands, standName } from "@/api/testing/standCatalogue";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import type {
  Scenario,
  ScenarioActionKind,
  ScenarioPreparation,
  ScenarioPreview,
  ScenarioReadiness,
  ScenarioRun,
  TestStand,
} from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown } from "@/components/ui/Dropdown";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { personaDeptId } from "@/lib/rbac";

import { PreviewResult } from "./LaunchPreviewModal";
import { StandSetupFields } from "./StandSetupFields";
import {
  actionKey,
  draftFrom,
  draftToWrite,
  emptyDraft,
  moveItem,
  standDraft,
  type ActionDraft,
  type ScenarioDraft,
  type StandDraft,
} from "./scenarioDraft";

const READINESS_OPTIONS = [
  { value: "ready", label: "Рабочий" },
  { value: "review", label: "На проверке" },
  { value: "broken", label: "Неисправен" },
  { value: "development", label: "В разработке" },
];

const PREPARATION_OPTIONS: { value: ScenarioPreparation; label: string }[] = [
  { value: "full", label: "Полная (restore + подготовка)" },
  { value: "revert_only", label: "Только откат" },
  { value: "none", label: "Не готовить" },
];

const MODE_OPTIONS = [
  { value: "", label: "— режим теста —" },
  { value: "orel", label: "Орёл" },
  { value: "smolensk", label: "Смоленск" },
];

const KIND_LABELS: Record<ScenarioActionKind, string> = {
  run_test: "Прогнать тест",
  prepare_stand: "Подготовить стенд",
  wait: "Подождать",
};

const RUN_HINT =
  "Запуск берёт брони всех стендов сразу (или ждёт, пока все освободятся), готовит их параллельно "
  + "и выполняет действия по порядку. Одиночные тесты на этих стендах встают в очередь после сценария.";

const RUN_STATE_LABELS: Record<string, string> = {
  waiting_for_stands: "Ждёт стенды",
  preparing: "Подготовка стендов",
  running: "Выполняется",
  stopping: "Останавливается",
  succeeded: "Успешно",
  failed: "Провал",
  stopped: "Остановлен",
};

const ACTIVE_RUN_STATES = new Set(["waiting_for_stands", "preparing", "running", "stopping"]);

// ── раздел ───────────────────────────────────────────────────────────────

export function ScenariosWorkzone() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);
  const listQ = useQuery(
    async () => (departmentId ? (await listScenarios(departmentId)).items : []),
    [departmentId],
  );
  const [selected, setSelected] = useState<string | "new" | null>(null);

  if (!departmentId) return <div className="text-sm text-dim">Нет привязанного отдела.</div>;
  const items = listQ.data ?? [];
  return (
    <div className="flex flex-1 min-h-0 gap-4">
      <aside className="w-72 shrink-0 flex flex-col gap-2 min-h-0" aria-label="Список сценариев">
        <Button variant="primary" size="sm" className="inline-flex items-center gap-1.5" onClick={() => setSelected("new")}>
          <Plus className="w-3.5 h-3.5" /> Новый сценарий
        </Button>
        {listQ.error != null && <div role="alert" className="text-xs text-danger">{apiErrMsg(listQ.error, "Сценарии не загрузились")}</div>}
        {listQ.loading && !listQ.data && <div className="text-xs text-dim">Загрузка…</div>}
        {listQ.data && items.length === 0 && <div className="text-xs text-dim">Сценариев пока нет.</div>}
        <div className="flex flex-col gap-1 overflow-y-auto">
          {items.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setSelected(s.id)}
              className={`text-left rounded border border-token px-2 py-1.5 ${selected === s.id ? "surface-2" : ""}`}
            >
              <div className="text-sm font-medium truncate">{s.name}</div>
              <div className="text-[11px] text-dim mono truncate">{s.code} · стендов {s.stands_count} · действий {s.actions_count}</div>
            </button>
          ))}
        </div>
      </aside>
      <section className="flex-1 min-w-0 min-h-0 overflow-y-auto">
        {selected === null && <div className="text-sm text-dim">Выберите сценарий или создайте новый.</div>}
        {selected !== null && (
          <ScenarioLoader
            key={selected}
            scenarioId={selected === "new" ? null : selected}
            departmentId={departmentId}
            onSaved={(id) => { listQ.refetch(); setSelected(id); }}
            onDeleted={() => { listQ.refetch(); setSelected(null); }}
          />
        )}
      </section>
    </div>
  );
}

function ScenarioLoader({
  scenarioId, departmentId, onSaved, onDeleted,
}: {
  scenarioId: string | null;
  departmentId: string;
  onSaved: (id: string) => void;
  onDeleted: () => void;
}) {
  const q = useQuery(async () => (scenarioId ? getScenario(scenarioId) : null), [scenarioId]);
  if (scenarioId && q.loading && !q.data) return <div className="text-xs text-dim">Загрузка…</div>;
  if (q.error != null) return <div role="alert" className="text-xs text-danger">{apiErrMsg(q.error, "Сценарий не загрузился")}</div>;
  return (
    <ScenarioEditor
      scenario={q.data ?? null}
      departmentId={departmentId}
      onSaved={onSaved}
      onDeleted={onDeleted}
    />
  );
}

export function ScenarioEditor({
  scenario, departmentId, onSaved, onDeleted,
}: {
  scenario: Scenario | null;
  departmentId: string;
  onSaved: (id: string) => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const standsQ = useQuery(() => listNamedTestStands({ department_id: departmentId }), [departmentId]);
  const testsQ = useQuery(async () => (await listTestDefinitions({ limit: 500 })).items, []);
  const [draft, setDraft] = useState<ScenarioDraft>(() => (scenario ? draftFrom(scenario) : emptyDraft()));
  const [addStand, setAddStand] = useState("");
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const pool = useMemo(() => new Map((standsQ.data ?? []).map((s) => [s.id, s])), [standsQ.data]);
  const standLabel = (standId: string): string => {
    const row = draft.stands.find((s) => s.stand_id === standId);
    const base = pool.get(standId) ? standName(pool.get(standId) as TestStand) : standId;
    return row?.label ? `${row.label} · ${base}` : base;
  };
  const scenarioStandOptions = draft.stands.map((s) => ({ value: s.stand_id, label: standLabel(s.stand_id) }));
  const testOptions = (testsQ.data ?? []).map((t) => ({ value: t.id, label: `${t.code} — ${t.full_name}` }));

  const patch = (changes: Partial<ScenarioDraft>) => setDraft((d) => ({ ...d, ...changes }));
  const patchStand = (index: number, changes: Partial<StandDraft>) =>
    setDraft((d) => ({ ...d, stands: d.stands.map((s, i) => (i === index ? { ...s, ...changes } : s)) }));
  const patchAction = (index: number, changes: Partial<ActionDraft>) =>
    setDraft((d) => ({ ...d, actions: d.actions.map((a, i) => (i === index ? { ...a, ...changes } : a)) }));
  const moveAction = (from: number, to: number) => setDraft((d) => ({ ...d, actions: moveItem(d.actions, from, to) }));

  function removeStand(index: number) {
    const standId = draft.stands[index].stand_id;
    setDraft((d) => ({
      ...d,
      stands: d.stands.filter((_, i) => i !== index),
      actions: d.actions.map((a) => (a.stand_id === standId ? { ...a, stand_id: "" } : a)),
    }));
  }

  function addAction(kind: ScenarioActionKind) {
    setDraft((d) => ({
      ...d,
      actions: [...d.actions, {
        key: actionKey(), kind, stand_id: kind === "wait" ? "" : d.stands[0]?.stand_id ?? "",
        test_id: "", is_verdict: false, seconds: kind === "wait" ? "60" : "",
      }],
    }));
  }

  async function save() {
    const body = draftToWrite(draft, departmentId);
    if (typeof body === "string") {
      setError(body);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = scenario ? await updateScenario(scenario.id, body) : await createScenario(body);
      toast.success("Сценарий сохранён");
      onSaved(saved.id);
    } catch (e) {
      setError(apiErrMsg(e, "Не удалось сохранить сценарий"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!scenario) return;
    const ok = await confirm.confirm({
      title: "Удалить сценарий?",
      message: `Сценарий «${scenario.name}» будет удалён.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteScenario(scenario.id);
      toast.success("Сценарий удалён");
      onDeleted();
    } catch (e) {
      setError(apiErrMsg(e, "Не удалось удалить сценарий"));
    } finally {
      setBusy(false);
    }
  }

  const freeStands = (standsQ.data ?? []).filter((s) => !draft.stands.some((row) => row.stand_id === s.id));

  return (
    <div className="flex flex-col gap-4 max-w-4xl" aria-label="Редактор сценария">
      <div className="flex items-center gap-2">
        <Workflow className="w-5 h-5 text-accent" />
        <h2 className="text-base font-semibold flex-1">{scenario ? scenario.name : "Новый сценарий"}</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Код *</span>
          <input aria-label="Код сценария" className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
            value={draft.code} onChange={(e) => patch({ code: e.target.value })} placeholder="freeipa.basic" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Название *</span>
          <input aria-label="Название сценария" className="surface-2 border border-token rounded px-2 py-1 text-sm"
            value={draft.name} onChange={(e) => patch({ name: e.target.value })} placeholder="FreeIPA: КД + клиент" />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Статус</span>
          <Dropdown mode="single" searchable={false} sortOptions={false} options={READINESS_OPTIONS}
            value={draft.readiness} onChange={(v) => patch({ readiness: v as ScenarioReadiness })} />
        </div>
      </div>

      <fieldset className="flex flex-col gap-3 border border-token rounded p-3" aria-label="Стенды сценария">
        <legend className="text-dim text-xs px-1">Стенды · {draft.stands.length}</legend>
        {standsQ.error != null && <div className="text-xs text-warn">{apiErrMsg(standsQ.error, "Стенды не загрузились")}</div>}
        {draft.stands.map((s, index) => {
          const stand = pool.get(s.stand_id);
          const target = (stand as (TestStand & { target_type?: string }) | undefined)?.target_type
            ?? scenario?.stands.find((row) => row.stand_id === s.stand_id)?.target_type ?? "server";
          return (
            <div key={s.stand_id} className="flex flex-col gap-2 border border-token rounded p-2" data-testid="scenario-stand">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">{stand ? standName(stand) : s.stand_id}</span>
                <Badge kind={target === "vm" ? "accent" : "neutral"}>{target === "vm" ? "ВМ" : "сервер"}</Badge>
                <input aria-label={`Подпись стенда ${index + 1}`} className="surface-2 border border-token rounded px-2 py-0.5 text-xs flex-1"
                  placeholder="подпись: КД, клиент…" value={s.label} onChange={(e) => patchStand(index, { label: e.target.value })} />
                <button type="button" aria-label={`Убрать стенд ${index + 1}`} className="text-dim hover:text-danger" onClick={() => removeStand(index)}>
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-sm">
                <div className="flex flex-col gap-1">
                  <span className="text-dim text-xs">Подготовка</span>
                  <Dropdown mode="single" searchable={false} sortOptions={false} options={PREPARATION_OPTIONS}
                    value={s.preparation} onChange={(v) => patchStand(index, { preparation: v as ScenarioPreparation })} />
                </div>
                <label className="flex flex-col gap-1">
                  <span className="text-dim text-xs">Ядро (пусто — ядро запуска)</span>
                  <input className="surface-2 border border-token rounded px-2 py-1 mono text-sm" value={s.kernel_override}
                    onChange={(e) => patchStand(index, { kernel_override: e.target.value })} />
                </label>
                <div className="flex flex-col gap-1">
                  <span className="text-dim text-xs">Режим</span>
                  <Dropdown mode="single" searchable={false} sortOptions={false} options={MODE_OPTIONS}
                    value={s.mode_override} onChange={(v) => patchStand(index, { mode_override: v })} />
                </div>
              </div>
              <StandSetupFields
                draft={s.setup}
                onChange={(setup) => patchStand(index, { setup })}
                departmentId={departmentId}
                provisioningProfileId={s.provisioning_profile_id}
                onProvisioningProfileChange={(id) => patchStand(index, { provisioning_profile_id: id })}
              />
            </div>
          );
        })}
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1 text-sm flex-1">
            <span className="text-dim text-xs">Добавить стенд пула (сервер или ВМ)</span>
            <Dropdown mode="single" searchable placeholder="— стенд —"
              options={freeStands.map((s) => ({ value: s.id, label: standName(s) }))}
              value={addStand} onChange={setAddStand} />
          </div>
          <Button size="sm" disabled={!addStand} onClick={() => {
            patch({ stands: [...draft.stands, standDraft(addStand)] });
            setAddStand("");
          }}>
            Добавить
          </Button>
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2 border border-token rounded p-3" aria-label="Действия сценария">
        <legend className="text-dim text-xs px-1">Действия · {draft.actions.length} (порядок — перетаскиванием)</legend>
        <ol className="flex flex-col gap-1.5">
          {draft.actions.map((a, index) => (
            <li
              key={a.key}
              data-testid="scenario-action"
              draggable
              onDragStart={(e) => {
                setDragIndex(index);
                e.dataTransfer?.setData("text/plain", String(index));
                if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const from = dragIndex ?? Number(e.dataTransfer?.getData("text/plain"));
                if (Number.isInteger(from)) moveAction(from, index);
                setDragIndex(null);
              }}
              onDragEnd={() => setDragIndex(null)}
              className={`flex items-center gap-2 border border-token rounded px-2 py-1.5 text-sm ${dragIndex === index ? "opacity-50" : ""}`}
            >
              <GripVertical className="w-4 h-4 text-dim cursor-grab shrink-0" aria-hidden />
              <span className="mono text-xs text-dim w-5">{index + 1}</span>
              <div className="w-44 shrink-0">
                <Dropdown mode="single" searchable={false} sortOptions={false}
                  options={(Object.keys(KIND_LABELS) as ScenarioActionKind[]).map((k) => ({ value: k, label: KIND_LABELS[k] }))}
                  value={a.kind} onChange={(v) => patchAction(index, { kind: v as ScenarioActionKind, is_verdict: false })} />
              </div>
              {a.kind !== "wait" && (
                <div className="w-48 shrink-0">
                  <Dropdown mode="single" searchable={false} placeholder="— стенд —" options={scenarioStandOptions}
                    value={a.stand_id} onChange={(v) => patchAction(index, { stand_id: v })} />
                </div>
              )}
              {a.kind === "run_test" && (
                <div className="flex-1 min-w-0">
                  <Dropdown mode="single" searchable placeholder="— тест —" options={testOptions}
                    value={a.test_id} onChange={(v) => patchAction(index, { test_id: v })} />
                </div>
              )}
              {a.kind === "wait" && (
                <label className="flex items-center gap-1 flex-1">
                  <input aria-label={`Секунд ожидания ${index + 1}`} className="surface-2 border border-token rounded px-2 py-1 mono text-sm w-24"
                    inputMode="numeric" value={a.seconds} onChange={(e) => patchAction(index, { seconds: e.target.value })} />
                  <span className="text-xs text-dim">с</span>
                </label>
              )}
              {a.kind === "prepare_stand" && <div className="flex-1 text-xs text-dim">по настройкам стенда выше</div>}
              {a.kind === "run_test" && (
                <label className="flex items-center gap-1 text-xs shrink-0" title="Вердикт сценария берётся из отмеченных тестов">
                  <Checkbox aria-label={`Вердикт действия ${index + 1}`} checked={a.is_verdict}
                    onChange={(e) => patchAction(index, { is_verdict: e.target.checked })} /> вердикт
                </label>
              )}
              <button type="button" aria-label={`Выше ${index + 1}`} disabled={index === 0} onClick={() => moveAction(index, index - 1)}>
                <ArrowUp className="w-3.5 h-3.5" />
              </button>
              <button type="button" aria-label={`Ниже ${index + 1}`} disabled={index === draft.actions.length - 1}
                onClick={() => moveAction(index, index + 1)}>
                <ArrowDown className="w-3.5 h-3.5" />
              </button>
              <button type="button" aria-label={`Удалить действие ${index + 1}`} className="text-dim hover:text-danger"
                onClick={() => patch({ actions: draft.actions.filter((_, i) => i !== index) })}>
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </li>
          ))}
        </ol>
        <div className="flex gap-2 flex-wrap">
          {(Object.keys(KIND_LABELS) as ScenarioActionKind[]).map((kind) => (
            <Button key={kind} size="sm" className="inline-flex items-center gap-1" onClick={() => addAction(kind)}>
              <Plus className="w-3.5 h-3.5" /> {KIND_LABELS[kind]}
            </Button>
          ))}
        </div>
      </fieldset>

      {error && <div role="alert" className="text-xs text-danger">{error}</div>}
      <div className="flex gap-2">
        <Button variant="primary" disabled={busy} onClick={() => void save()}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null} Сохранить
        </Button>
        {scenario && <Button disabled={busy} onClick={() => void remove()}>Удалить</Button>}
      </div>

      {scenario && <ScenarioRunsBlock scenario={scenario} standLabel={standLabel} />}
      {scenario && <ScenarioPreviewBlock scenario={scenario} standLabel={standLabel} />}
    </div>
  );
}

function ScenarioPreviewBlock({ scenario, standLabel }: { scenario: Scenario; standLabel: (id: string) => string }) {
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  const [rc, setRc] = useState("");
  const [kernel, setKernel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ScenarioPreview | null>(null);
  const tests = new Map(scenario.actions.map((a) => [a.test_id, a.test_code]));

  async function run() {
    setBusy(true);
    setError("");
    try {
      setPreview(await previewScenario(scenario.id, { os_version_id: rc, kernel: kernel.trim() }));
    } catch (e) {
      setPreview(null);
      setError(apiErrMsg(e, "Не удалось собрать превью сценария"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 border border-token rounded p-3" aria-label="Превью сценария">
      <div className="text-sm font-medium">Превью сохранённого сценария</div>
      <div className="flex items-end gap-2 flex-wrap">
        <div className="flex flex-col gap-1 text-sm min-w-[200px]">
          <span className="text-dim text-xs">РЦ</span>
          <Dropdown mode="single" searchable placeholder="Выберите РЦ" value={rc}
            onChange={(v) => {
              setRc(v);
              setKernel(versionsQ.data?.items.find((item) => item.id === v)?.kernels?.[0] ?? "");
            }}
            options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))} />
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Ядро</span>
          <input aria-label="Ядро превью" className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
            value={kernel} onChange={(e) => setKernel(e.target.value)} />
        </label>
        <Button size="sm" variant="primary" className="inline-flex items-center gap-1.5"
          disabled={busy || !rc || !kernel.trim()} onClick={() => void run()}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />} Показать
        </Button>
      </div>
      {error && <div role="alert" className="text-xs text-danger">{error}</div>}
      {preview?.actions.map((a) => (
        <div key={a.position} className="flex flex-col gap-2 border-t border-token pt-2" data-testid="scenario-preview-action">
          <div className="text-sm font-medium flex items-center gap-2 flex-wrap">
            <span className="mono text-dim">{a.position + 1}.</span>
            {KIND_LABELS[a.kind]}
            {a.stand_id && <span className="text-dim">· {standLabel(a.stand_id)}</span>}
            {a.test_id && <span className="mono">· {tests.get(a.test_id) ?? a.test_id}</span>}
            {a.kind === "wait" && <span className="text-dim">· {String(a.params.seconds)} с</span>}
            {a.is_verdict && <Badge kind="accent">вердикт</Badge>}
          </div>
          {a.errors.map((e, i) => (
            <div key={i} role="alert" className="text-xs text-danger">
              <span className="mono">{e.error_code}</span>: {e.message}
            </div>
          ))}
          {a.stand && (
            <div className="text-xs text-dim">
              Подготовка: {PREPARATION_OPTIONS.find((p) => p.value === a.stand?.preparation)?.label} · ядро {a.stand.kernel}
              {a.stand.stand_setup?.script && (
                <pre className="surface-2 border border-token rounded p-2 mono text-[11px] whitespace-pre-wrap mt-1">
                  {a.stand.stand_setup.script}
                </pre>
              )}
            </div>
          )}
          {a.launch && <PreviewResult preview={a.launch} />}
        </div>
      ))}
    </section>
  );
}

function ScenarioRunsBlock({ scenario, standLabel }: { scenario: Scenario; standLabel: (id: string) => string }) {
  const toast = useToast();
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  const runsQ = useQuery(async () => (await listScenarioRuns(scenario.id)).items, [scenario.id]);
  const [rc, setRc] = useState("");
  const [kernel, setKernel] = useState("");
  const [mode, setMode] = useState("orel");
  const [debug, setDebug] = useState(scenario.readiness !== "ready");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const runs = runsQ.data ?? [];
  const active = runs.some((r) => ACTIVE_RUN_STATES.has(r.state));
  const tests = new Map(scenario.actions.map((a) => [a.test_id, a.test_code]));
  const { refetch } = runsQ;

  // Пока запуск идёт — обновлять карточку.
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => refetch(), 5000);
    return () => window.clearInterval(timer);
  }, [active, refetch]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      await startScenarioRun(scenario.id, { os_version_id: rc, kernel: kernel.trim(), mode, debug });
      toast.success("Сценарий запущен");
      refetch();
    } catch (e) {
      setError(apiErrMsg(e, "Не удалось запустить сценарий"));
    } finally {
      setBusy(false);
    }
  }

  async function stop(run: ScenarioRun) {
    setBusy(true);
    try {
      await stopScenarioRun(run.id);
      refetch();
    } catch (e) {
      setError(apiErrMsg(e, "Не удалось остановить сценарий"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 border border-token rounded p-3" aria-label="Запуски сценария">
      <div className="text-sm font-medium">Запуск</div>
      <div className="text-[11px] text-dim">{RUN_HINT}</div>
      <div className="flex items-end gap-2 flex-wrap">
        <div className="flex flex-col gap-1 text-sm min-w-[200px]">
          <span className="text-dim text-xs">РЦ</span>
          <Dropdown mode="single" searchable placeholder="Выберите РЦ для запуска" value={rc}
            onChange={(v) => {
              setRc(v);
              setKernel(versionsQ.data?.items.find((item) => item.id === v)?.kernels?.[0] ?? "");
            }}
            options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))} />
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Ядро</span>
          <input aria-label="Ядро запуска" className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
            value={kernel} onChange={(e) => setKernel(e.target.value)} />
        </label>
        <div className="flex flex-col gap-1 text-sm w-36">
          <span className="text-dim text-xs">Режим</span>
          <Dropdown mode="single" searchable={false} sortOptions={false} options={MODE_OPTIONS.slice(1)}
            value={mode} onChange={(v) => v && setMode(v)} />
        </div>
        <label className="flex items-center gap-1.5 text-sm pb-1.5">
          <Checkbox aria-label="Debug-запуск" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Debug
        </label>
        <Button size="sm" variant="primary" className="inline-flex items-center gap-1.5" aria-label="Запустить сценарий"
          disabled={busy || !rc || !kernel.trim()} onClick={() => void start()}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />} Запустить
        </Button>
      </div>
      {error && <div role="alert" className="text-xs text-danger">{error}</div>}
      {runs.map((run) => (
        <div key={run.id} className="flex flex-col gap-1.5 border-t border-token pt-2 text-sm" data-testid="scenario-run">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge kind={run.state === "succeeded" ? "ok" : run.state === "failed" ? "danger" : ACTIVE_RUN_STATES.has(run.state) ? "accent" : "neutral"}>
              {RUN_STATE_LABELS[run.state] ?? run.state}
            </Badge>
            {run.verdict && <span className="text-xs">вердикт: {run.verdict}</span>}
            <span className="mono text-xs text-dim">{run.launch_context.RC} · {run.launch_context.KERNEL}</span>
            {run.debug_mode && <Badge kind="warn">debug</Badge>}
            {ACTIVE_RUN_STATES.has(run.state) && run.state !== "stopping" && (
              <Button size="sm" className="ml-auto inline-flex items-center gap-1" disabled={busy}
                aria-label="Остановить сценарий" onClick={() => void stop(run)}>
                <Square className="w-3 h-3" /> Остановить
              </Button>
            )}
          </div>
          {run.state === "waiting_for_stands" && run.blocked_by.length > 0 && (
            <div className="text-xs text-warn">
              Заняты: {run.blocked_by.map((b) => `${standLabel(b.stand_id)} (${b.reason})`).join(", ")}
            </div>
          )}
          {run.error && <div className="text-xs text-danger">{run.error}</div>}
          <div className="text-xs text-dim flex flex-wrap gap-x-3">
            {run.stands.map((st) => (
              <span key={st.stand_id}>{standLabel(st.stand_id)}: {st.state}</span>
            ))}
          </div>
          <ol className="text-xs flex flex-col gap-0.5">
            {run.actions.map((a) => (
              <li key={a.position} className={run.current_position === a.position && ACTIVE_RUN_STATES.has(run.state) ? "font-medium" : ""}>
                {a.position + 1}. {KIND_LABELS[a.kind]}
                {a.stand_id && ` · ${standLabel(a.stand_id)}`}
                {a.test_id && ` · ${tests.get(a.test_id) ?? a.test_id}`}
                {a.kind === "wait" && ` · ${String(a.params.seconds)} с`}
                {a.state && <span className="text-dim"> — {a.state}{a.verdict ? ` (${a.verdict})` : ""}</span>}
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}

