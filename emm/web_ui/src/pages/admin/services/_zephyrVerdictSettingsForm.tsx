/**
 * Блок «Вердикт теста из Zephyr» на странице очереди
 * тестирования.
 *
 * Исход теста — статус, который скрипт сам выставил тест-кейсу в прогоне
 * Zephyr (код выхода `run.py` всегда 0). Здесь две независимые формы со
 * своими кнопками сохранения:
 *
 * - ожидание: сколько ждать итогового статуса после конца SSH-сессии, как
 *   часто опрашивать, что считать итогом, если статус так и не стал
 *   финальным, и что делать с запуском без прогона в Zephyr (debug) —
 *   поля `department_test_settings`;
 * - таблица «статус Zephyr → исход» (`/zephyr-status-mappings`): у отдела
 *   без своих строк действует набор по умолчанию.
 */

import { useMemo, useState } from "react";
import { Gavel, Plus, Trash2 } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { upsertDepartmentTestSettings } from "@/api/testing/departmentTestSettings";
import type {
  DepartmentTestSettings,
  VerdictOutcome,
  ZephyrStatusMapping,
} from "@/api/testing/types";
import {
  getZephyrStatusMapping,
  putZephyrStatusMapping,
  resetZephyrStatusMapping,
} from "@/api/testing/zephyrStatusMappings";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { useToast } from "@/contexts/ToastContext";

const OUTCOME_OPTIONS: DropdownOption[] = [
  { value: "passed", label: "Пройден" },
  { value: "failed", label: "Провален" },
  { value: "not_finished", label: "Не завершён — ждать" },
];

const UNFINISHED_OPTIONS: DropdownOption[] = [
  { value: "failed", label: "Провален (как решено в T3)" },
  { value: "passed", label: "Пройден" },
];

const WITHOUT_RUN_OPTIONS: DropdownOption[] = [
  { value: "unknown", label: "Результат не определён" },
  { value: "exit_code", label: "По коду выхода starter.sh" },
];

type VerdictFields = Pick<
  DepartmentTestSettings,
  | "zephyr_verdict_wait_seconds"
  | "zephyr_verdict_poll_seconds"
  | "zephyr_verdict_unfinished_outcome"
  | "verdict_without_zephyr_run"
>;

interface WaitDraft {
  wait: string;
  poll: string;
  unfinished: VerdictFields["zephyr_verdict_unfinished_outcome"];
  withoutRun: VerdictFields["verdict_without_zephyr_run"];
}

function toWaitDraft(s: VerdictFields): WaitDraft {
  return {
    wait: String(s.zephyr_verdict_wait_seconds),
    poll: String(s.zephyr_verdict_poll_seconds),
    unfinished: s.zephyr_verdict_unfinished_outcome,
    withoutRun: s.verdict_without_zephyr_run,
  };
}

function parseIntIn(raw: string, min: number, max: number): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= min && value <= max ? value : null;
}

/** Черновик → тело PUT; строка — первая найденная ошибка валидации. */
function waitDraftToBody(d: WaitDraft): VerdictFields | string {
  const wait = parseIntIn(d.wait, 0, 86400);
  if (wait === null) return "Ожидание итогового статуса — целое число секунд от 0 до 86400.";
  const poll = parseIntIn(d.poll, 5, 3600);
  if (poll === null) return "Интервал опроса Zephyr — целое число секунд от 5 до 3600.";
  return {
    zephyr_verdict_wait_seconds: wait,
    zephyr_verdict_poll_seconds: poll,
    zephyr_verdict_unfinished_outcome: d.unfinished,
    verdict_without_zephyr_run: d.withoutRun,
  };
}

export function ZephyrVerdictSettingsForm({
  departmentId,
  loaded,
  onSaved,
}: {
  departmentId: string;
  loaded: DepartmentTestSettings;
  onSaved: () => void;
}) {
  return (
    <section className="card flex flex-col gap-4 max-w-2xl" aria-labelledby="zephyr-verdict-title">
      <div className="text-sm font-semibold flex items-center gap-2" id="zephyr-verdict-title">
        <Gavel className="w-4 h-4 text-accent" /> Вердикт теста из Zephyr
      </div>
      <div className="text-xs text-dim">
        Итог теста — статус, который скрипт сам выставил тест-кейсу в прогоне Zephyr. После конца
        сессии на стенде тест ждёт этот статус («Ожидание вердикта»): стенд остаётся занят,
        следующий тест не стартует.
      </div>
      <WaitSettings departmentId={departmentId} loaded={loaded} onSaved={onSaved} />
      <StatusMappingEditor departmentId={departmentId} />
    </section>
  );
}

function WaitSettings({
  departmentId,
  loaded,
  onSaved,
}: {
  departmentId: string;
  loaded: VerdictFields;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<WaitDraft>(() => toWaitDraft(loaded));
  const [pending, setPending] = useState(false);
  // Свежие данные с сервера сбрасывают черновик — во время рендера, как в
  // соседних формах страницы.
  const [source, setSource] = useState(loaded);
  if (source !== loaded) {
    setSource(loaded);
    setDraft(toWaitDraft(loaded));
  }

  const dirty = useMemo(() => {
    const parsed = waitDraftToBody(draft);
    if (typeof parsed === "string") return true;
    return (Object.keys(parsed) as (keyof VerdictFields)[]).some((key) => parsed[key] !== loaded[key]);
  }, [draft, loaded]);

  function patch(changes: Partial<WaitDraft>) {
    setDraft((prev) => ({ ...prev, ...changes }));
  }

  async function handleSave() {
    if (pending) return;
    const parsed = waitDraftToBody(draft);
    if (typeof parsed === "string") {
      toast.error(parsed);
      return;
    }
    setPending(true);
    try {
      await upsertDepartmentTestSettings(departmentId, parsed);
      toast.success("Ожидание вердикта сохранено");
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить ожидание вердикта"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Ждать итогового статуса, с</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.wait}
            onChange={(e) => patch({ wait: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Опрашивать Zephyr раз в, с</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.poll}
            onChange={(e) => patch({ poll: e.target.value })}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="field-label">Не дождались итогового статуса</span>
          <Dropdown
            mode="single"
            searchable={false}
            sortOptions={false}
            options={UNFINISHED_OPTIONS}
            value={draft.unfinished}
            onChange={(value) => patch({ unfinished: value as WaitDraft["unfinished"] })}
          />
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <span className="field-label">Запуск без прогона в Zephyr (debug)</span>
          <Dropdown
            mode="single"
            searchable={false}
            sortOptions={false}
            options={WITHOUT_RUN_OPTIONS}
            value={draft.withoutRun}
            onChange={(value) => patch({ withoutRun: value as WaitDraft["withoutRun"] })}
          />
        </div>
      </div>
      <div className="text-xs text-dim">
        По умолчанию — 2100 с (35 минут), опрос раз в 60 с: скрипты публикуют статус асинхронно,
        с повторами. Код выхода run.py всегда 0, поэтому debug-запуск по умолчанию не засчитывается
        пройденным.
      </div>
      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={pending || !dirty}>
          {pending ? "Сохраняем…" : "Сохранить ожидание"}
        </Button>
        {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
      </div>
    </div>
  );
}

interface RowDraft {
  key: number;
  status: string;
  outcome: VerdictOutcome;
}

let rowKeySeq = 0;
function nextKey(): number {
  rowKeySeq += 1;
  return rowKeySeq;
}

function toRows(mapping: ZephyrStatusMapping): RowDraft[] {
  return mapping.items.map((item) => ({ key: nextKey(), status: item.zephyr_status, outcome: item.outcome }));
}

function rowsToItems(rows: RowDraft[]) {
  return rows
    .map((row) => ({ zephyr_status: row.status.trim(), outcome: row.outcome }))
    .filter((item) => item.zephyr_status !== "");
}

function StatusMappingEditor({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const mappingQ = useQuery(() => getZephyrStatusMapping(departmentId), [departmentId]);
  const loaded = mappingQ.data;
  const [rows, setRows] = useState<RowDraft[]>([]);
  const [pending, setPending] = useState(false);
  const [source, setSource] = useState(loaded);
  if (source !== loaded) {
    setSource(loaded);
    if (loaded) setRows(toRows(loaded));
  }

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return JSON.stringify(rowsToItems(rows)) !== JSON.stringify(loaded.items);
  }, [rows, loaded]);

  function patchRow(key: number, changes: Partial<RowDraft>) {
    setRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...changes } : row)));
  }

  async function run(action: () => Promise<ZephyrStatusMapping>, successText: string) {
    setPending(true);
    try {
      await action();
      toast.success(successText);
      mappingQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить маппинг статусов Zephyr"));
    } finally {
      setPending(false);
    }
  }

  async function handleSave() {
    if (pending) return;
    const items = rowsToItems(rows);
    if (items.length === 0) {
      toast.error("Нужен хотя бы один статус. Чтобы вернуть набор по умолчанию — «Сбросить».");
      return;
    }
    const seen = new Set<string>();
    for (const item of items) {
      const norm = item.zephyr_status.toLowerCase();
      if (seen.has(norm)) {
        toast.error(`Статус «${item.zephyr_status}» указан дважды`);
        return;
      }
      seen.add(norm);
    }
    await run(() => putZephyrStatusMapping(departmentId, items), "Маппинг статусов Zephyr сохранён");
  }

  async function handleReset() {
    if (pending) return;
    await run(() => resetZephyrStatusMapping(departmentId), "Маппинг статусов Zephyr сброшен к набору по умолчанию");
  }

  if (mappingQ.loading && !loaded) return <div className="text-xs text-dim">Загрузка маппинга…</div>;
  if (mappingQ.error != null && !loaded) {
    return <div className="alert-danger text-sm">{apiErrMsg(mappingQ.error, "Маппинг статусов не загрузился")}</div>;
  }
  if (!loaded) return null;

  return (
    <div className="flex flex-col gap-2" aria-label="Маппинг статусов Zephyr">
      <div className="text-sm font-semibold">Статус в Zephyr → итог теста</div>
      <div className="text-xs text-dim">
        Статус сравнивается без учёта регистра: имя статуса («Pass») или числовой id легаси («91»).
        Статус, которого нет в таблице, считается незавершённым.{" "}
        {loaded.is_default ? "Сейчас действует набор по умолчанию." : "У отдела свой набор."}
      </div>
      {rows.map((row, index) => (
        <div key={row.key} className="flex items-center gap-2" data-testid="zephyr-mapping-row">
          <input
            className="field-input mono flex-1 min-w-0"
            aria-label={`Статус Zephyr ${index + 1}`}
            value={row.status}
            maxLength={64}
            onChange={(e) => patchRow(row.key, { status: e.target.value })}
          />
          <Dropdown
            mode="single"
            searchable={false}
            sortOptions={false}
            options={OUTCOME_OPTIONS}
            value={row.outcome}
            onChange={(value) => patchRow(row.key, { outcome: value as VerdictOutcome })}
          />
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Удалить статус ${index + 1}`}
            onClick={() => setRows((prev) => prev.filter((r) => r.key !== row.key))}
          >
            <Trash2 className="w-4 h-4" />
          </Button>
        </div>
      ))}
      <div>
        <Button
          size="sm"
          className="flex items-center gap-1"
          onClick={() => setRows((prev) => [...prev, { key: nextKey(), status: "", outcome: "failed" }])}
        >
          <Plus className="w-3 h-3" /> Добавить статус
        </Button>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={pending || !dirty}>
          {pending ? "Сохраняем…" : "Сохранить маппинг"}
        </Button>
        <Button variant="ghost" onClick={handleReset} disabled={pending || loaded.is_default}>
          Сбросить к набору по умолчанию
        </Button>
        {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
      </div>
    </div>
  );
}
