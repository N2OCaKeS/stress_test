/**
 * Шаги многоступенчатого теста в конструкторе команды.
 *
 * Тест исполняется шагами по порядку на одной брони стенда; у каждого шага
 * свои слоты команды (их показывает конструктор под этой панелью), `$5`
 * starter.sh, способ запуска и настройка стенда перед шагом. Одношаговый
 * тест — один шаг; «Добавить шаг» копирует слоты текущего шага, чтобы фаза
 * отличалась от соседней парой параметров (`-q 16`, `-sf end`).
 *
 * Выбранный шаг хранит родитель (`stepId`): `null` — первый шаг, так же его
 * понимает API слотов.
 */

import { useState } from "react";
import { ChevronLeft, ChevronRight, Layers, Plus, Trash2 } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import {
  createTestStep,
  deleteTestStep,
  listTestSteps,
  reorderTestSteps,
  updateTestStep,
} from "@/api/testing/testSteps";
import type { StandSetup, StepRunMode, TestDefinition, TestStep } from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown } from "@/components/ui/Dropdown";
import { useToast } from "@/contexts/ToastContext";
import { StandSetupFields } from "./StandSetupFields";
import { draftToStandSetup, standSetupDraft } from "./standSetupDraft";
import { stepSummary, stepTitle } from "./stepLabels";

const RUN_MODE_OPTIONS: { value: StepRunMode; label: string }[] = [
  { value: "full", label: "Полный запуск: starter.sh с клонированием ветки" },
  { value: "rerun", label: "Повторный запуск склонированного кода" },
];

export function TestStepsPanel({
  test,
  stepId,
  onStepChange,
  disabled = false,
}: {
  test: TestDefinition;
  stepId: string | null;
  onStepChange: (stepId: string | null) => void;
  disabled?: boolean;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const stepsQ = useQuery(() => listTestSteps(test.id), [test.id]);
  const [busy, setBusy] = useState(false);
  const steps = stepsQ.data ?? [];
  const activeIndex = Math.max(0, steps.findIndex((s) => s.id === stepId));
  const active = steps[activeIndex];
  const locked = disabled || busy || stepsQ.isFetching;

  async function run(action: () => Promise<void>, failure: string) {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      toast.error(apiErrMsg(e, failure));
    } finally {
      setBusy(false);
    }
  }

  const addStep = () => run(async () => {
    const created = await createTestStep(test.id, {
      name: `Шаг ${steps.length + 1}`,
      run_mode: "rerun",
      copy_args_from_step_id: active?.id ?? null,
    });
    stepsQ.refetch();
    onStepChange(created.id);
    toast.success("Шаг добавлен — параметры скопированы из текущего шага");
  }, "Не удалось добавить шаг");

  const moveStep = (dir: -1 | 1) => run(async () => {
    const ids = steps.map((s) => s.id);
    const target = activeIndex + dir;
    [ids[activeIndex], ids[target]] = [ids[target], ids[activeIndex]];
    await reorderTestSteps(test.id, ids);
    stepsQ.refetch();
  }, "Не удалось переставить шаги");

  const removeStep = async () => {
    if (!active) return;
    const ok = await confirm({
      title: "Удалить шаг",
      message: `Удалить «${stepTitle(active, activeIndex)}» вместе с его параметрами команды?`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    await run(async () => {
      await deleteTestStep(test.id, active.id);
      onStepChange(null);
      stepsQ.refetch();
      toast.success("Шаг удалён");
    }, "Не удалось удалить шаг");
  };

  if (stepsQ.error && steps.length === 0) {
    return (
      <div className="alert alert-danger text-xs flex items-center gap-2">
        <span className="flex-1">{apiErrMsg(stepsQ.error, "Шаги теста не загрузились")}</span>
        <Button size="sm" onClick={() => stepsQ.refetch()}>Повторить</Button>
      </div>
    );
  }

  return (
    <section className="surface-2 border border-token rounded p-2 flex flex-col gap-2" aria-label="Шаги теста">
      <div className="flex items-center gap-2 flex-wrap">
        <Layers className="w-4 h-4 text-accent" />
        <span className="text-sm font-medium">Шаги теста</span>
        {steps.map((step, index) => (
          <Button
            key={step.id}
            size="sm"
            variant={index === activeIndex ? "primary" : "default"}
            aria-pressed={index === activeIndex}
            disabled={locked}
            onClick={() => onStepChange(index === 0 ? null : step.id)}
            title={stepSummary(step)}
          >
            {stepTitle(step, index)}
          </Button>
        ))}
        <Button size="sm" className="inline-flex items-center gap-1" disabled={locked || !stepsQ.data} onClick={addStep}>
          <Plus className="w-3.5 h-3.5" /> Добавить шаг
        </Button>
      </div>
      <div className="text-xs text-dim">
        {steps.length <= 1
          ? "Одношаговый тест. Шаги выполняются по порядку на одной брони стенда, вердикт — после последнего; перед шагом можно задать параметры ядра и скрипт настройки."
          : `Параметры команды ниже — шага «${active ? stepTitle(active, activeIndex) : ""}». Перед шагом с настройкой стенд настраивается без восстановления образа.`}
      </div>
      {active && (
        <StepSettings
          key={`${active.id}:${active.updated_at}`}
          test={test}
          step={active}
          index={activeIndex}
          count={steps.length}
          disabled={locked}
          onSave={(body) => run(async () => {
            await updateTestStep(test.id, active.id, body);
            stepsQ.refetch();
            toast.success("Шаг сохранён");
          }, "Не удалось сохранить шаг")}
          onMove={moveStep}
          onDelete={removeStep}
        />
      )}
    </section>
  );
}

function StepSettings({
  test,
  step,
  index,
  count,
  disabled,
  onSave,
  onMove,
  onDelete,
}: {
  test: TestDefinition;
  step: TestStep;
  index: number;
  count: number;
  disabled: boolean;
  onSave: (body: { name: string; run_mode: StepRunMode; starter_suffix: string | null; stand_setup: StandSetup | null }) => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(step.name);
  const [runMode, setRunMode] = useState<StepRunMode>(step.run_mode);
  const [suffix, setSuffix] = useState(step.starter_suffix ?? "");
  const [setup, setSetup] = useState(() => standSetupDraft(step.stand_setup));
  const setupValue = draftToStandSetup(setup);
  const firstIsRerun = index === 0 && runMode === "rerun";
  const valid = typeof setupValue !== "string" && !firstIsRerun;

  return (
    <details className="flex flex-col gap-2" open={count > 1}>
      <summary className="text-xs cursor-pointer select-none">
        Настройки шага <Badge kind={step.run_mode === "rerun" ? "warn" : "accent"}>{stepSummary(step)}</Badge>
      </summary>
      <div className="flex flex-col gap-2 mt-2">
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя шага</span>
            <input
              aria-label="Имя шага"
              className="surface border border-token rounded px-2 py-1 text-sm"
              value={name}
              maxLength={128}
              onChange={(e) => setName(e.target.value)}
              placeholder="maxcpus=16"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">$5 starter.sh (kernel/balance/oom)</span>
            <input
              aria-label="Суффикс запуска шага"
              className="surface border border-token rounded px-2 py-1 mono text-sm"
              value={suffix}
              maxLength={16}
              onChange={(e) => setSuffix(e.target.value)}
            />
          </label>
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Как запускать</span>
          <Dropdown
            mode="single" searchable={false} sortOptions={false}
            options={RUN_MODE_OPTIONS}
            value={runMode}
            onChange={(v) => setRunMode(v as StepRunMode)}
          />
          {firstIsRerun && (
            <span className="text-danger text-xs">
              Первый шаг идёт после восстановления стенда — кода на стенде ещё нет, нужен полный запуск.
            </span>
          )}
        </div>
        <StandSetupFields
          draft={setup}
          onChange={setSetup}
          departmentId={test.department_id}
          legend={index === 0 ? "Настройка стенда (при подготовке)" : "Настройка стенда перед шагом (без восстановления образа)"}
        />
        {typeof setupValue === "string" && <span className="text-danger text-xs">{setupValue}</span>}
        <div className="flex items-center gap-1 flex-wrap">
          <Button size="sm" aria-label="Шаг раньше" disabled={disabled || index === 0} onClick={() => onMove(-1)}>
            <ChevronLeft className="w-3.5 h-3.5" />
          </Button>
          <Button size="sm" aria-label="Шаг позже" disabled={disabled || index === count - 1} onClick={() => onMove(1)}>
            <ChevronRight className="w-3.5 h-3.5" />
          </Button>
          <Button
            size="sm" variant="danger" className="inline-flex items-center gap-1"
            disabled={disabled || count <= 1} onClick={onDelete}
          >
            <Trash2 className="w-3.5 h-3.5" /> Удалить шаг
          </Button>
          <Button
            size="sm" variant="primary" className="ml-auto"
            disabled={disabled || !valid}
            onClick={() => onSave({
              name: name.trim(),
              run_mode: runMode,
              starter_suffix: suffix.trim() || null,
              stand_setup: typeof setupValue === "string" ? null : setupValue,
            })}
          >
            Сохранить шаг
          </Button>
        </div>
      </div>
    </details>
  );
}
