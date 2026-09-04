/**
 * Раздел "Тестирование" — фронтенд-мокап для согласования с руководителем.
 * Реального `testing_service` в репозитории ещё нет (breadcrumb размечен
 * заранее); весь контент — hardcoded demo-данные, без backend-вызовов.
 *
 * Файл — тонкий роутер+shell по подразделам; сама функциональность разбита
 * по файлам того же каталога (по образцу `pages/server/tabs/*`):
 * `overview.tsx` (рабочая зона + дашборд пула), `tests.tsx` (каталог тестов),
 * `runs.tsx` (fleet-wide прогоны), `stp.tsx` (зеркало Zephyr, средняя панель
 * которого сама показывает список всех версий ОС — отдельная вкладка «РЦ» не
 * нужна). Общие типы/данные/мелкие компоненты — в `_shared.tsx`.
 */
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { Cog, FileText, ListChecks, type LucideIcon } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { TestingOverview } from "./overview";
import { TestsWorkzone } from "./tests";
import { RunsWorkzone } from "./runs";
import { StpWorkzone } from "./stp";

const SUBSECTIONS = [
  { id: "tests", label: "Тесты", icon: FileText },
  { id: "runs", label: "Прогоны", icon: ListChecks },
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

  return (
    <Shell breadcrumb={`testing_service / ${active.label}`}>
      <main className="flex-1 min-w-0 overflow-auto">
        <div className="border-b border-token px-5 py-4 flex items-center justify-between gap-4 flex-wrap">
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

        <div className="p-5">
          {activeId === "overview" && <TestingOverview />}
          {activeId === "tests" && <TestsWorkzone />}
          {activeId === "runs" && <RunsWorkzone />}
          {activeId === "stp" && <StpWorkzone />}
        </div>
      </main>
    </Shell>
  );
}
