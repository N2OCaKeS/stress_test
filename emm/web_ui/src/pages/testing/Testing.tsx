/**
 * Раздел "Тестирование" — `testing_service` реализован и закоммичен
 * (каталог тестов, стенды, очередь, прогоны, СТП, отчёты по отделу, план —
 * `ALLTA MIGRATION.md`); страницы этого раздела вызывают его API напрямую,
 * demo-данные из `_shared.tsx` остаются только для mock-режима разработки
 * (`VITE_USE_MOCK_AUTH=true`).
 *
 * Файл — тонкий роутер+shell по подразделам; сама функциональность разбита
 * по файлам того же каталога (по образцу `pages/server/tabs/*`):
 * `overview.tsx` (рабочая зона + дашборд пула), `tests.tsx` (каталог тестов),
 * `runs.tsx` (fleet-wide прогоны), `debug.tsx` (разовые запуски вне
 * прогона — debug-режим, `ALLTA MIGRATION.md` §5.5), `stp.tsx` (зеркало
 * Zephyr). Для «Прогонов», «Отладки» и СТП средняя панель Shell — список
 * (по образцу `pages/server/Server.tsx` `aside`), поэтому их состояние
 * держим здесь через `useRunsState`/`useAdhocState`/`useStpVersionState` и
 * пробрасываем в панель и в рабочую зону — обе стороны должны видеть один и
 * тот же выбор. `overview.tsx` тоже переиспользует `runsState` (число
 * прогонов на дашборде пула + модалка запуска прогона), поэтому получает
 * его тем же способом, что и панель/рабочая зона «Прогонов». Отдельная
 * вкладка «РЦ» не нужна, эту роль закрывает панель СТП. Общие
 * типы/данные/мелкие компоненты — в `_shared.tsx`.
 */
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { Bug, Cog, FileText, ListChecks, type LucideIcon } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { TestingOverview } from "./overview";
import { TestsWorkzone } from "./tests";
import { RunsMiddlePanel, RunsWorkzone, useRunsState } from "./runs";
import { AdhocMiddlePanel, AdhocWorkzone, useAdhocState } from "./debug";
import { StpMiddlePanel, StpWorkzone, useStpVersionState } from "./stp";

const SUBSECTIONS = [
  { id: "tests", label: "Тесты", icon: FileText },
  { id: "runs", label: "Прогоны", icon: ListChecks },
  { id: "debug", label: "Одиночные запуски", icon: Bug },
  { id: "stp", label: "СТП", icon: Cog },
] as const;

type SubsectionId = (typeof SUBSECTIONS)[number]["id"];
type PageId = "overview" | SubsectionId;

function pageId(value: string | undefined): PageId {
  if (value && SUBSECTIONS.some((s) => s.id === value)) return value as SubsectionId;
  return "overview";
}

export function Testing() {
  const params = useParams();
  const activeId = pageId(params.section);
  const active = useMemo<{ label: string; icon: LucideIcon }>(() => {
    if (activeId === "overview") return { label: "Тестирование", icon: ListChecks };
    return SUBSECTIONS.find((s) => s.id === activeId) ?? SUBSECTIONS[0];
  }, [activeId]);
  const ActiveIcon = active.icon;
  // Хуки всегда вызываются, чтобы не нарушать порядок хуков при переключении
  // подраздела — конкретное состояние нужно только своей вкладке, но само
  // по себе оно дешёвое.
  const runsState = useRunsState();
  const adhocState = useAdhocState(activeId === "debug");
  const stpState = useStpVersionState();

  const middle =
    activeId === "runs" ? (
      <RunsMiddlePanel state={runsState} />
    ) : activeId === "debug" ? (
      <AdhocMiddlePanel state={adhocState} />
    ) : activeId === "stp" ? (
      <StpMiddlePanel state={stpState} />
    ) : undefined;

  return (
    <Shell breadcrumb={`testing_service / ${active.label}`} middle={middle}>
      <main className={activeId === "tests" ? "flex flex-1 min-w-0 min-h-0 flex-col overflow-hidden" : "flex-1 min-w-0 overflow-auto"}>
        <div className="border-b border-token px-5 py-4 flex items-center justify-between gap-4 flex-wrap shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-10 w-10 rounded surface-2 border border-token flex items-center justify-center shrink-0">
              <ActiveIcon className="w-5 h-5 text-accent" />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold truncate">{active.label}</h1>
              <div className="text-xs text-dim">testing_service</div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap">
            <Link to="/testing" className={`btn btn-sm inline-flex items-center gap-2 ${activeId === "overview" ? "btn-primary" : ""}`}>
              <ListChecks className="w-4 h-4" />
              <span>Рабочая зона</span>
            </Link>
            {SUBSECTIONS.map((s) => {
              const Icon = s.icon;
              return (
                <Link
                  key={s.id}
                  to={`/testing/${s.id}`}
                  className={`btn btn-sm inline-flex items-center gap-2 ${s.id === activeId ? "btn-primary" : ""}`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{s.label}</span>
                </Link>
              );
            })}
          </div>
        </div>

        <div className={activeId === "tests" ? "p-5 flex flex-1 min-h-0 flex-col" : "p-5"}>
          {activeId === "overview" && <TestingOverview runsState={runsState} />}
          {activeId === "tests" && <TestsWorkzone />}
          {activeId === "runs" && <RunsWorkzone state={runsState} />}
          {activeId === "debug" && <AdhocWorkzone state={adhocState} />}
          {activeId === "stp" && <StpWorkzone state={stpState} />}
        </div>
      </main>
    </Shell>
  );
}
