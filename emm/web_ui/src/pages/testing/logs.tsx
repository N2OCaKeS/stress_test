/** История попыток с независимыми списками логов кампаний и одиночных запусков. */
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { listQueueItems, type QueueItemsQuery } from "@/api/testing/queueItems";
import { getTestStand, listTestStands } from "@/api/testing/testStands";
import { formatMsk } from "@/lib/datetime";
import { Button } from "@/components/ui/Button";
import { AttemptLogViewer } from "./AttemptLogViewer";

const PAGE_SIZE = 50;
const STATES: Record<string, string> = {
  queued: "В очереди", preparing: "Подготовка", ready: "Готов к старту",
  running: "Выполняется", succeeded: "Успешно", failed: "Ошибка",
};

export function TestingLogs() {
  const [params, setParams] = useSearchParams();
  const standsQ = useQuery(async () => {
    const page = await listTestStands({ limit: 500 });
    const details = await Promise.allSettled(page.items.map((stand) => getTestStand(stand.id)));
    return page.items.map((stand, i) => {
      const result = details[i];
      const server = result.status === "fulfilled" ? result.value.server as { display_name?: string; hostname?: string } | undefined : undefined;
      return { id: stand.id, label: server?.display_name ?? server?.hostname ?? stand.server_id };
    });
  }, []);

  const kind = params.get("kind") === "campaign" ? "campaign" : "standalone";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const query: QueueItemsQuery = { kind, offset, limit: PAGE_SIZE };
  for (const key of ["test_run_id", "test_id", "stand_id", "attempt_id", "retry_of_id", "q"] as const) {
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
  const queueQ = useQuery(async () => ({ key, page: await listQueueItems(query) }), [key]);
  // Не показываем строки предыдущего фильтра, пока загружается новый.
  const page = queueQ.data?.key === key ? queueQ.data.page : undefined;
  const [selectedId, setSelectedId] = useState("");
  const selected = page?.items.find((item) => item.id === selectedId) ?? page?.items[0];
  useEffect(() => {
    const timer = setInterval(queueQ.refetch, 5000);
    return () => clearInterval(timer);
  }, [queueQ.refetch]);

  function navigate(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(changes)) {
      if (value) next.set(name, value); else next.delete(name);
    }
    setParams(next);
  }

  return <div className="grid gap-4">
    <div className="flex gap-2 flex-wrap">
      <Button variant={kind === "campaign" ? "primary" : "default"} onClick={() => setParams({ kind: "campaign" })}>Логи прогонов</Button>
      <Button variant={kind === "standalone" ? "primary" : "default"} onClick={() => setParams({ kind: "standalone" })}>Логи одиночных запусков</Button>
    </div>
    <p className="text-sm text-dim">Каждая строка — отдельная попытка. Предыдущие результаты и логи сохраняются после повтора. Список обновляется каждые 5 секунд.</p>
    <LogFilters key={params.toString()} params={params} campaign={kind === "campaign"} onApply={setParams} stands={standsQ.data ?? []} standsError={!!standsQ.error} />
    <div className="flex gap-3 items-center text-sm">
      <Button onClick={queueQ.refetch} disabled={queueQ.isFetching}>Обновить логи</Button>
      <span>Найдено попыток: {page?.total ?? "…"}</span>
      {queueQ.isFetching && <span className="text-dim">Загрузка…</span>}
    </div>
    {!!queueQ.error && <div role="alert" className="text-danger">{apiErrMsg(queueQ.error, "Не удалось загрузить историю логов")}</div>}
    <div className="surface border border-token rounded overflow-auto max-h-[420px]">
      <table className="mini w-full text-sm">
        <thead className="sticky top-0 surface"><tr><th>Попытка</th><th>Тест / стенд</th>{kind === "campaign" && <th>Прогон</th>}<th>Режим</th><th>Состояние</th><th>Создана (MSK)</th><th>Лог</th></tr></thead>
        <tbody>{page?.items.map((item) => <tr key={item.id} className={selected?.id === item.id ? "surface-2" : ""}>
          <td className="mono text-xs">{item.id}<div className="text-dim">{item.is_current === false ? "Предыдущая" : item.retry_of_id ? "Повтор" : "Первая"}</div></td>
          <td><div>{item.test_code ?? item.test_id}</div><div className="text-xs text-dim">{item.test_name} · {standsQ.data?.find((stand) => stand.id === item.stand_id)?.label ?? item.stand_id}</div></td>
          {kind === "campaign" && <td className="mono text-xs">{item.test_run_id}</td>}
          <td>{item.debug_mode ? "Debug" : "Обычный"}</td>
          <td>{STATES[item.state] ?? item.state}</td><td className="mono text-xs">{formatMsk(item.created_at)}</td>
          <td><Button size="sm" onClick={() => setSelectedId(item.id)} aria-label={`Открыть лог ${item.id}`}>Открыть</Button></td>
        </tr>)}</tbody>
      </table>
      {page?.items.length === 0 && <div className="p-6 text-dim">Нет попыток по выбранным фильтрам</div>}
    </div>
    <div className="flex items-center gap-3 text-sm">
      <Button disabled={queueQ.isFetching || offset === 0} onClick={() => navigate({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}>Предыдущая страница</Button>
      <span>Страница {Math.floor(offset / PAGE_SIZE) + 1}</span>
      <Button disabled={queueQ.isFetching || !page || offset + PAGE_SIZE >= page.total} onClick={() => navigate({ offset: String(offset + PAGE_SIZE) })}>Следующая страница</Button>
    </div>
    {selected && <section className="grid gap-3" aria-label="Лог выбранной попытки">
      <h2 className="font-semibold">Лог · {selected.test_code ?? selected.test_id} · {selected.id}</h2>
      <div className="text-sm text-dim">{kind === "campaign" ? `Прогон ${selected.test_run_id}` : "Одиночный запуск"} · {selected.debug_mode ? "Debug" : "Обычный"} · РЦ {selected.rc} · ядро {selected.kernel} · {selected.mode} · {standsQ.data?.find((stand) => stand.id === selected.stand_id)?.label ?? selected.stand_id}</div>
      <div className="flex gap-2 flex-wrap">
        {selected.retry_of_id && <Button size="sm" onClick={() => setParams({ kind, attempt_id: selected.retry_of_id! })}>Предыдущая попытка</Button>}
        {selected.is_current === false && <Button size="sm" onClick={() => setParams({ kind, retry_of_id: selected.id })}>Следующая попытка</Button>}
        <a className="text-accent text-sm" href={`/testing/logs?kind=${kind}&attempt_id=${encodeURIComponent(selected.id)}`}>Ссылка на эту попытку</a>
      </div>
      {selected.error && <div className="text-danger text-sm">{selected.error}</div>}
      <AttemptLogViewer key={selected.id} queueItemId={selected.id} state={selected.state} />
    </section>}
  </div>;
}

function LogFilters({ params, campaign, onApply, stands, standsError }: { params: URLSearchParams; campaign: boolean; onApply: (params: URLSearchParams) => void; stands: { id: string; label: string }[]; standsError: boolean }) {
  const [error, setError] = useState("");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const from = String(data.get("from") ?? "");
    const until = String(data.get("until") ?? "");
    if (from && until && from >= until) { setError("Начало периода должно быть раньше конца"); return; }
    setError("");
    const next = new URLSearchParams({ kind: campaign ? "campaign" : "standalone" });
    for (const [name, value] of data.entries()) if (String(value).trim()) next.set(name, String(value).trim());
    onApply(next);
  }
  return <form onSubmit={submit} className="surface border border-token rounded p-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    <label className="text-xs grid gap-1">Тест (код или название)<input className="input" name="q" defaultValue={params.get("q") ?? ""} /></label>
    <label className="text-xs grid gap-1">Стенд<input className="input" name="stand_id" list="log-stands" defaultValue={params.get("stand_id") ?? ""} placeholder="Все стенды" /></label>
    <datalist id="log-stands">{stands.map((stand) => <option key={stand.id} value={stand.id}>{stand.label}</option>)}</datalist>
    {campaign && <label className="text-xs grid gap-1">ID прогона<input className="input" name="test_run_id" defaultValue={params.get("test_run_id") ?? ""} /></label>}
    <label className="text-xs grid gap-1">ID попытки<input className="input" name="attempt_id" defaultValue={params.get("attempt_id") ?? ""} /></label>
    <label className="text-xs grid gap-1">Создана с (MSK)<input className="input" type="datetime-local" name="from" defaultValue={params.get("from") ?? ""} /></label>
    <label className="text-xs grid gap-1">Создана до (не включая, MSK)<input className="input" type="datetime-local" name="until" defaultValue={params.get("until") ?? ""} /></label>
    <label className="text-xs grid gap-1">Состояние<select className="input" name="state" defaultValue={params.get("state") ?? ""}><option value="">Все состояния</option>{Object.entries(STATES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label className="text-xs grid gap-1">Режим запуска<select className="input" name="debug" defaultValue={params.get("debug") ?? ""}><option value="">Все режимы</option><option value="false">Обычный</option><option value="true">Debug</option></select></label>
    {params.get("test_id") && <input type="hidden" name="test_id" value={params.get("test_id")!} />}
    {params.get("retry_of_id") && <div className="text-xs">Повтор попытки: {params.get("retry_of_id")}<input type="hidden" name="retry_of_id" value={params.get("retry_of_id")!} /></div>}
    {standsError && <div className="text-xs text-danger">Не удалось загрузить подсказки стендов. Можно указать ID.</div>}
    <div className="flex items-end gap-2"><Button type="submit" variant="primary">Применить фильтры</Button><Button type="button" onClick={() => onApply(new URLSearchParams({ kind: campaign ? "campaign" : "standalone" }))}>Сбросить</Button></div>
    {error && <div role="alert" className="text-danger text-xs">{error}</div>}
  </form>;
}
