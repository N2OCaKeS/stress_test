/** История попыток с независимыми списками логов кампаний и одиночных запусков. */
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { listOsVersions } from "@/api/server/osVersions";
import { listQueueItems, type QueueItemsQuery } from "@/api/testing/queueItems";
import { getTestStand, listTestStands } from "@/api/testing/testStands";
import { formatMsk } from "@/lib/datetime";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { listCatalogue } from "@/api/catalogue";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import { listTestRuns } from "@/api/testing/testRuns";
import type { OsVersion } from "@/api/server/types";
import type { TestDefinition, TestRun } from "@/api/testing/types";
import { AttemptLogWorkzone } from "./AttemptLogWorkzone";

const PAGE_SIZE = 50;
const STATES: Record<string, string> = {
  queued: "В очереди", preparing: "Подготовка", ready: "Готов к старту",
  running: "Выполняется", succeeded: "Успешно", failed: "Ошибка",
};

export function useLogsState(enabled = true) {
  const [params, setParams] = useSearchParams();
  const standsQ = useQuery(async () => {
    const stands = await listCatalogue(listTestStands);
    const details = await Promise.allSettled(stands.map((stand) => getTestStand(stand.id)));
    return stands.map((stand, i) => {
      const result = details[i];
      const server = result.status === "fulfilled" ? result.value.server as { display_name?: string; hostname?: string } | undefined : undefined;
      return { id: stand.id, label: server?.display_name ?? server?.hostname ?? "Стенд: имя недоступно" };
    });
  }, [], { enabled });

  const kind = params.get("kind") === "campaign" ? "campaign" : params.get("kind") === "standalone" ? "standalone" : "all";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const query: QueueItemsQuery = { kind, offset, limit: PAGE_SIZE };
  for (const key of ["test_run_id", "test_id", "stand_id", "attempt_id", "retry_of_id", "q", "os_version_id", "os_version_name", "kernel"] as const) {
    const value = params.get(key);
    if (value) query[key] = value;
  }
  const from = params.get("from");
  const until = params.get("until");
  if (from) query.created_from = `${from}:00+03:00`;
  if (until) query.created_until = `${until}:00+03:00`;
  if (params.get("state")) query.states = [params.get("state")!];
  if (params.get("debug")) query.debug_mode = params.get("debug") === "true";
  const key = JSON.stringify(query);
  const queueQ = useQuery(async () => ({ key, page: await listQueueItems(query) }), [key], { enabled });
  // Не показываем строки предыдущего фильтра, пока загружается новый.
  const page = queueQ.data?.key === key ? queueQ.data.page : undefined;
  const versionsQ = useQuery(() => listCatalogue(listOsVersions), [], { enabled });
  const testsQ = useQuery(() => listCatalogue(listTestDefinitions), [], { enabled });
  const runsQ = useQuery(() => listCatalogue(listTestRuns), [], { enabled });
  const [selectedId, setSelectedId] = useState("");
  const [closed, setClosed] = useState(false);
  const selected = closed ? undefined : page?.items.find((item) => item.id === selectedId) ?? page?.items[0];
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(queueQ.refetch, 5000);
    return () => clearInterval(timer);
  }, [enabled, queueQ.refetch]);

  function navigate(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(changes)) {
      if (value) next.set(name, value); else next.delete(name);
    }
    setParams(next);
  }

  return { params, setParams, kind, offset, queueQ, page, selected, standsQ, versionsQ, testsQ, runsQ, navigate,
    select: (id: string) => { setSelectedId(id); setClosed(false); }, close: () => setClosed(true) };
}

type LogsState = ReturnType<typeof useLogsState>;

export function LogsMiddlePanel({ state: s }: { state: LogsState }) {
  const [filtersOpen, setFiltersOpen] = useState(true);
  return <aside className="flex flex-col min-h-0 border-r border-token surface">
    <div className="shrink-0 border-b border-token p-3">
      <h2 className="text-sm font-semibold mb-2">Все логи тестирования</h2>
      <Button size="sm" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>{filtersOpen ? "Свернуть фильтры" : "Развернуть фильтры"}</Button>
    </div>
    <div hidden={!filtersOpen} className="shrink-0 border-b border-token p-3 max-h-[55vh] overflow-auto">
      <LogFilters key={s.params.toString()} params={s.params} onApply={s.setParams}
        stands={s.standsQ.data ?? []} standsError={!!s.standsQ.error}
        versions={s.versionsQ.data ?? []} tests={s.testsQ.data ?? []} runs={s.runsQ.data ?? []} />
    </div>
    <div className="flex-1 min-h-0 overflow-auto">
      {s.queueQ.loading && <p className="p-3 text-xs">Загрузка логов…</p>}
      {!!s.queueQ.error && <p role="alert" className="p-3 text-xs text-danger">{apiErrMsg(s.queueQ.error, "Не удалось загрузить историю логов")}</p>}
      {s.page?.items.map((item) => <button key={item.id} onClick={() => s.select(item.id)} aria-label={`Открыть лог ${item.id}`}
        className={`w-full text-left px-3 py-3 border-b border-token hover-bg ${s.selected?.id === item.id ? "surface-2 border-l-2 border-l-accent" : ""}`}>
        <div className="text-sm font-medium">{item.test_name ?? item.test_code ?? "Тест удалён"}</div>
        <div className="text-xs mt-1">{STATES[item.state] ?? item.state} · {item.debug_mode ? "Debug" : item.test_run_id ? "Прогон" : "Одиночный"}</div>
        <div className="text-xs text-dim mt-1">{s.standsQ.data?.find((stand) => stand.id === item.stand_id)?.label ?? "Имя стенда недоступно"}</div>
        <div className="text-xs text-dim">{s.versionsQ.data?.find((v) => v.id === item.rc)?.name ?? item.rc} · {item.kernel}</div>
        <div className="text-[11px] text-dim mt-1">{formatMsk(item.created_at)} · {item.is_current === false ? "Предыдущая попытка" : item.retry_of_id ? "Повтор" : "Первая попытка"}</div>
        {item.log_status === "rotated" && <div className="text-xs text-dim mt-1">Лог ротирован</div>}
      </button>)}
      {s.page?.items.length === 0 && <p className="p-3 text-xs text-dim">Нет попыток по выбранным фильтрам</p>}
    </div>
    <div className="p-3 border-t border-token shrink-0 flex items-center gap-2 text-xs">
      <Button size="sm" aria-label="Предыдущая страница" disabled={s.queueQ.isFetching || s.offset === 0} onClick={() => s.navigate({ offset: String(Math.max(0, s.offset - PAGE_SIZE)) })}>Назад</Button>
      <span>{s.page?.total ?? "…"} записей · стр. {Math.floor(s.offset / PAGE_SIZE) + 1}</span>
      <Button size="sm" aria-label="Следующая страница" disabled={s.queueQ.isFetching || !s.page || s.offset + PAGE_SIZE >= s.page.total} onClick={() => s.navigate({ offset: String(s.offset + PAGE_SIZE) })}>Далее</Button>
    </div>
  </aside>;
}

export function LogsWorkzone({ state: s }: { state: LogsState }) {
  const item = s.selected;
  const osName = (value: string | null) => {
    if (!value) return "—";
    return s.versionsQ.data?.find((version) => version.id === value)?.name ?? (value.startsWith("os_") ? "Имя ОС недоступно" : value);
  };
  if (!item) return <div className="p-8 text-center text-dim">Выберите лог в списке слева</div>;
  return <div className="flex flex-1 min-h-0 flex-col gap-3">
    <div className="flex gap-2 flex-wrap shrink-0">
      {item.retry_of_id && <Button size="sm" onClick={() => s.setParams({ kind: s.kind, attempt_id: item.retry_of_id! })}>Предыдущая попытка</Button>}
      {item.is_current === false && <Button size="sm" onClick={() => s.setParams({ kind: s.kind, retry_of_id: item.id })}>Следующая попытка</Button>}
    </div>
    <AttemptLogWorkzone key={item.id} id={item.id} state={item.state} logStatus={item.log_status}
      title={`Лог · ${item.test_name ?? item.test_code ?? "Тест удалён"}`}
      subtitle={`${item.test_run_id ? "Прогон" : "Одиночный запуск"} · ${osName(item.rc)} · ${item.kernel ?? "—"} · ${item.mode ?? "—"} · ${formatMsk(item.created_at)}`}
      canRetry={item.is_current !== false && ["succeeded", "failed"].includes(item.state)} onClose={s.close} onRetried={s.queueQ.refetch} />
  </div>;
}

export function TestingLogs() {
  const state = useLogsState();
  return <div className="flex min-h-0"><LogsMiddlePanel state={state} /><LogsWorkzone state={state} /></div>;
}

function LogFilters({ params, onApply, stands, standsError, versions, tests, runs }: {
  params: URLSearchParams; onApply: (params: URLSearchParams) => void;
  stands: { id: string; label: string }[]; standsError: boolean;
  versions: OsVersion[]; tests: TestDefinition[]; runs: TestRun[];
}) {
  const [error, setError] = useState("");
  const [draft, setDraft] = useState(() => Object.fromEntries(params));
  const value = (name: string, fallback = "") => draft[name] ?? fallback;
  const change = (name: string, selected: string) => setDraft((previous) => ({ ...previous, [name]: selected, ...(name === "os_version_id" ? { kernel: "" } : {}) }));
  const selectedOs = versions.find((version) => version.id === value("os_version_id") || version.name === value("os_version_id"));
  const kernels = Array.from(new Set((selectedOs ? [selectedOs] : versions).flatMap((version) => version.kernels)));
  function select(name: string, label: string, options: DropdownOption[], placeholder: string, fallback = "") {
    const selected = value(name, fallback);
    const shown = selected && !options.some((option) => option.value === selected)
      ? [...options, { value: selected, label: "Недоступное значение", disabled: true }] : options;
    return <div className="grid gap-1 text-xs">
      <Dropdown mode="single" label={label} searchable className="w-full" options={shown} value={selected}
        onChange={(next) => change(name, next)} placeholder={placeholder} />
      <input type="hidden" name={name} value={selected} />
    </div>;
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const from = String(data.get("from") ?? "");
    const until = String(data.get("until") ?? "");
    if (from && until && from >= until) { setError("Начало периода должно быть раньше конца"); return; }
    setError("");
    const next = new URLSearchParams();
    for (const [name, entry] of data.entries()) if (String(entry).trim()) next.set(name, String(entry).trim());
    if (selectedOs && next.has("os_version_id")) next.set("os_version_name", selectedOs.name);
    if (next.get("kind") === "standalone") next.delete("test_run_id");
    onApply(next);
  }
  return <form onSubmit={submit} className="grid gap-2">
    {select("kind", "Вид запуска", [{ value: "all", label: "Все логи" }, { value: "campaign", label: "Прогоны" }, { value: "standalone", label: "Одиночные запуски" }], "Все логи", "all")}
    {select("os_version_id", "ОС", versions.map((v) => ({ value: v.id, label: v.name })), "Все ОС")}
    {select("kernel", "Ядро", kernels.map((kernel) => ({ value: kernel, label: kernel })), selectedOs ? "Все ядра ОС" : "Все ядра")}
    {select("test_id", "Тест", tests.map((test) => ({ value: test.id, label: `${test.full_name} · ${test.code}` })), "Все тесты")}
    {select("stand_id", "Стенд", stands.map((stand) => ({ value: stand.id, label: stand.label })), "Все стенды")}
    {select("state", "Состояние", Object.entries(STATES).map(([id, label]) => ({ value: id, label })), "Все состояния")}
    <details className="text-xs"><summary className="cursor-pointer text-dim">Дополнительно: период, прогон, поиск, debug</summary><div className="grid gap-2 mt-2">
      {value("kind", "all") !== "standalone" && select("test_run_id", "Прогон", runs.map((run) => ({ value: run.id, label: `${versions.find((v) => v.id === run.os_version_id)?.name ?? (run.os_version_id.startsWith("os_") ? "Имя ОС недоступно" : run.os_version_id)} · ${run.mode} · ${formatMsk(run.created_at)}` })), "Все прогоны")}
      <label className="text-xs grid gap-1">Поиск по названию или коду теста<input className="input" name="q" defaultValue={params.get("q") ?? ""} /></label>
      <label className="text-xs grid gap-1">Создана с (MSK)<input className="input" type="datetime-local" name="from" defaultValue={params.get("from") ?? ""} /></label>
      <label className="text-xs grid gap-1">Создана до (не включая, MSK)<input className="input" type="datetime-local" name="until" defaultValue={params.get("until") ?? ""} /></label>
      {select("debug", "Режим запуска", [{ value: "false", label: "Обычный" }, { value: "true", label: "Debug" }], "Все режимы")}
      {["attempt_id", "retry_of_id"].map((key) => params.get(key) ? <div key={key} className="text-xs text-dim">Выбрана конкретная попытка<input type="hidden" name={key} value={params.get(key)!} /></div> : null)}
    </div></details>
    {standsError && <div className="text-xs text-danger">Не удалось загрузить имена стендов. Повторите загрузку страницы.</div>}
    <div className="flex items-end gap-2"><Button type="submit" variant="primary">Применить фильтры</Button><Button type="button" onClick={() => onApply(new URLSearchParams())}>Сбросить</Button></div>
    {error && <div role="alert" className="text-danger text-xs">{error}</div>}
  </form>;
}
