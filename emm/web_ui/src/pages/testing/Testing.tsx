import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { FileText, ListChecks, Cog, Package } from "lucide-react";
import { Shell } from "@/components/shell/Shell";

const SECTIONS = [
  { id: "tests", label: "Тесты", icon: FileText },
  { id: "runs", label: "Прогоны", icon: ListChecks },
  { id: "stp", label: "СТП", icon: Cog },
  { id: "rc", label: "РЦ", icon: Package },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

function sectionId(value: string | undefined): SectionId {
  if (value && SECTIONS.some((s) => s.id === value)) return value as SectionId;
  return "tests";
}

export function Testing() {
  const params = useParams();
  const activeId = sectionId(params.section);
  const active = useMemo(
    () => SECTIONS.find((s) => s.id === activeId) ?? SECTIONS[0],
    [activeId],
  );
  const ActiveIcon = active.icon;

  const middle = (
    <aside className="surface border-r border-token min-h-0 flex flex-col">
      <div className="p-3 border-b border-token">
        <div className="text-sm font-medium">Тестирование</div>
      </div>
      <div className="p-2 flex flex-col gap-1">
        {SECTIONS.map((s) => {
          const Icon = s.icon;
          const to = s.id === "tests" ? "/testing/tests" : `/testing/${s.id}`;
          const activeSection = s.id === activeId;
          return (
            <Link
              key={s.id}
              to={to}
              className={`chip ${activeSection ? "active" : ""}`}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="text-sm">{s.label}</span>
            </Link>
          );
        })}
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb={`testing_service / ${active.label}`} middle={middle}>
      <main className="flex-1 min-w-0 overflow-auto">
        <div className="p-5">
          <div className="surface border border-token rounded p-5">
            <div className="flex items-center gap-3">
              <div className="h-9 w-9 rounded bg-accent/10 border border-token flex items-center justify-center">
                <ActiveIcon className="w-5 h-5 text-accent" />
              </div>
              <div>
                <h1 className="text-lg font-semibold">{active.label}</h1>
                <div className="text-xs text-dim">testing_service</div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </Shell>
  );
}
