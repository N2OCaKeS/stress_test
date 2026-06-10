import {
  Search,
  Hourglass,
  RefreshCw,
  Trash2,
  Skull,
  Clock,
  Link as LinkIcon,
  Download,
  Info,
  HelpCircle,
  XOctagon,
  AlertTriangle,
  RotateCw,
  GitCompare,
  type LucideIcon,
} from "lucide-react";

export interface DlqRow {
  id: string;
  icon: LucideIcon;
  title: string;
  errorShort: string;
  meta: string;
}

interface DlqGroup {
  title: string;
  icon: LucideIcon;
  count: number;
  rows: DlqRow[];
  emptyLabel?: string;
}

interface DlqListProps {
  groups: DlqGroup[];
  selected: string;
  onSelect: (id: string) => void;
  searchPlaceholder: string;
  countLabel: string;
  scopeOptions: string[];
  showTypeFilter?: boolean;
}

export function DlqList({
  groups,
  selected,
  onSelect,
  searchPlaceholder,
  countLabel,
  scopeOptions,
  showTypeFilter,
}: DlqListProps) {
  return (
    <section className="w-[380px] shrink-0 border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={searchPlaceholder}
          />
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
          <span>Группировка:</span>
          <select className="surface-2 border border-token rounded px-2 py-0.5">
            {scopeOptions.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
          <span className="ml-auto">{countLabel}</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-xs flex-wrap">
          {showTypeFilter && (
            <select className="surface-2 border border-token rounded px-2 py-0.5 text-dim">
              {["type: all", "package", "ssh", "power", "db", "disk"].map(
                (s) => (
                  <option key={s}>{s}</option>
                ),
              )}
            </select>
          )}
          <span className="text-[10px] text-dim ml-auto flex items-center gap-1">
            <Hourglass className="w-3 h-3" /> retention: <b>14 дней</b>
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {groups.map((g, gi) => {
          const Icon = g.icon;
          return (
            <div key={g.title}>
              <div
                className={`group-header flex items-center gap-2 ${gi === 0 ? "" : "mt-3"}`}
              >
                <Icon className="w-3 h-3" /> {g.title} · {g.count}
              </div>
              {g.rows.length === 0 ? (
                <div className="px-3 py-2 text-xs text-dim italic">
                  {g.emptyLabel ?? "пусто"}
                </div>
              ) : (
                <div className="px-2 flex flex-col gap-0.5">
                  {g.rows.map((r) => {
                    const RowIcon = r.icon;
                    return (
                      <button
                        key={r.id}
                        onClick={() => onSelect(r.id)}
                        className={`cred-row text-left ${
                          selected === r.id ? "active" : ""
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <RowIcon className="w-4 h-4 text-danger" />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm truncate mono">
                              {r.title}
                            </div>
                            <div className="text-[11px] text-dim truncate">
                              {r.errorShort}
                            </div>
                            <div className="text-[10px] text-dim mt-0.5">
                              {r.meta}
                            </div>
                          </div>
                          <span className="badge badge-danger">DLQ</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="border-t border-token p-2 flex gap-2">
        <button className="btn flex-1 flex items-center justify-center gap-2">
          <RefreshCw className="w-4 h-4" /> Re-queue selected
        </button>
        <button className="btn btn-danger flex-1 flex items-center justify-center gap-2">
          <Trash2 className="w-4 h-4" /> Discard selected
        </button>
      </div>
    </section>
  );
}

export interface DlqDetailProps {
  title: string;
  retries: string;
  dlqWhen: string;
  originalId: string;
  dept: string;
  actor: string;
  noteContent: React.ReactNode;
  originalTask: {
    id: string;
    type: string;
    target: string;
    targetHref?: string;
    args: string;
    requestedBy: string;
    createdAt: string;
    extraRows?: { label: string; value: React.ReactNode }[];
  };
  whyDlqItems: { kind: "danger" | "warn" | "info"; content: React.ReactNode }[];
  lastErrorTitle: string;
  lastErrorBody: string;
  retryHistory: {
    attempt: string;
    time: string;
    backoff: string;
    code: string;
    worker: string;
    stderr: string;
  }[];
  retryNote: React.ReactNode;
  relatedTitle: string;
  relatedRows: {
    id: string;
    type: string;
    server: string;
    dept?: string;
    error: string;
    when: string;
  }[];
  relatedNote: React.ReactNode;
  showDept?: boolean;
}

export function DlqDetail(props: DlqDetailProps) {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
          <Skull className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">
              {props.title}
            </h1>
            <span className="badge badge-danger">DEAD-LETTER</span>
            <span className="text-xs text-dim">
              retries <b>{props.retries}</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span>
              <Clock className="w-3 h-3 inline" /> в DLQ:{" "}
              <b>{props.dlqWhen}</b>
            </span>
            <span>·</span>
            <span>
              <LinkIcon className="w-3 h-3 inline" /> original:{" "}
              <a href="#" className="text-accent mono">
                {props.originalId}
              </a>
            </span>
            <span>·</span>
            <span>
              dept: <b>{props.dept}</b>
            </span>
            <span>·</span>
            <span>
              actor: <b>{props.actor}</b>
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="btn btn-primary flex items-center gap-1">
            <RefreshCw className="w-4 h-4" /> Re-queue
          </button>
          <button className="btn btn-danger flex items-center gap-1">
            <Trash2 className="w-4 h-4" /> Discard
          </button>
          <button className="btn flex items-center gap-1">
            <Download className="w-4 h-4" /> Export for analysis
          </button>
        </div>
      </div>

      <div className="px-5 pt-4">
        <div className="info-block flex items-start gap-2">
          <Info className="w-4 h-4 text-accent shrink-0 mt-0.5" />
          <div>{props.noteContent}</div>
        </div>
      </div>

      <div className="border-b border-token px-5 flex gap-1 mt-3 shrink-0">
        <button
          className="px-3 py-2 text-sm border-b-2 -mb-px"
          style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
        >
          Overview
        </button>
        {["Original task", "Error history", "Audit"].map((t) => (
          <button
            key={t}
            className="px-3 py-2 text-sm border-b-2 -mb-px border-transparent text-dim hover-bg"
          >
            {t}
          </button>
        ))}
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <LinkIcon className="w-4 h-4" /> Original task
          </div>
          <div className="text-sm">
            <div className="stat-row">
              <span className="text-dim">Task ID</span>
              <a href="#" className="text-accent mono">
                {props.originalTask.id}
              </a>
            </div>
            <div className="stat-row">
              <span className="text-dim">Type</span>
              <span className="mono">{props.originalTask.type}</span>
            </div>
            <div className="stat-row">
              <span className="text-dim">Target server</span>
              {props.originalTask.targetHref ? (
                <a
                  href={props.originalTask.targetHref}
                  className="text-accent mono"
                >
                  {props.originalTask.target}
                </a>
              ) : (
                <span className="mono">{props.originalTask.target}</span>
              )}
            </div>
            <div className="stat-row">
              <span className="text-dim">Args</span>
              <span className="mono text-xs">{props.originalTask.args}</span>
            </div>
            {props.originalTask.extraRows?.map((row) => (
              <div className="stat-row" key={row.label}>
                <span className="text-dim">{row.label}</span>
                <span>{row.value}</span>
              </div>
            ))}
            <div className="stat-row">
              <span className="text-dim">Requested by</span>
              <span className="mono">{props.originalTask.requestedBy}</span>
            </div>
            <div className="stat-row">
              <span className="text-dim">Created at</span>
              <span className="mono text-xs">
                {props.originalTask.createdAt}
              </span>
            </div>
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <HelpCircle className="w-4 h-4 text-warn" /> Why DLQ
          </div>
          <div className="text-sm space-y-2">
            {props.whyDlqItems.map((item, i) => {
              const Icon =
                item.kind === "danger"
                  ? XOctagon
                  : item.kind === "warn"
                    ? AlertTriangle
                    : Info;
              const cls =
                item.kind === "danger"
                  ? "text-danger"
                  : item.kind === "warn"
                    ? "text-warn"
                    : "text-accent";
              return (
                <div className="flex items-start gap-2" key={i}>
                  <Icon className={`w-4 h-4 ${cls} shrink-0 mt-0.5`} />
                  <div>{item.content}</div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-danger" />{" "}
            {props.lastErrorTitle}
          </div>
          <div className="alert-block mono text-xs whitespace-pre overflow-x-auto">
            {props.lastErrorBody}
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <RotateCw className="w-4 h-4" /> Retry history (
            {props.retryHistory.length})
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">attempt</th>
                <th className="pb-2 pr-3">time</th>
                <th className="pb-2 pr-3">backoff</th>
                <th className="pb-2 pr-3">error code</th>
                <th className="pb-2 pr-3">worker</th>
                <th className="pb-2">stderr tail</th>
              </tr>
            </thead>
            <tbody>
              {props.retryHistory.map((row) => (
                <tr key={row.attempt} className="border-t border-token">
                  <td className="py-2 mono">{row.attempt}</td>
                  <td className="text-dim text-xs mono">{row.time}</td>
                  <td className="text-dim text-xs">{row.backoff}</td>
                  <td className="mono">
                    <span className="badge badge-danger">{row.code}</span>
                  </td>
                  <td className="mono">{row.worker}</td>
                  <td className="text-dim text-xs truncate">{row.stderr}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 text-xs text-dim">{props.retryNote}</div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <GitCompare className="w-4 h-4" /> {props.relatedTitle}
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">DLQ ID</th>
                <th className="pb-2 pr-3">Task type</th>
                <th className="pb-2 pr-3">Server</th>
                {props.showDept && <th className="pb-2 pr-3">dept</th>}
                <th className="pb-2 pr-3">Error</th>
                <th className="pb-2">When</th>
              </tr>
            </thead>
            <tbody>
              {props.relatedRows.map((row) => (
                <tr key={row.id} className="border-t border-token">
                  <td className="py-2 mono">
                    <a href="#" className="text-accent">
                      {row.id}
                    </a>
                  </td>
                  <td>{row.type}</td>
                  <td className="mono">{row.server}</td>
                  {props.showDept && <td>{row.dept}</td>}
                  <td>
                    <span className="badge badge-danger">{row.error}</span>
                  </td>
                  <td className="text-dim text-xs mono">{row.when}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 text-xs text-dim">{props.relatedNote}</div>
        </div>
      </div>
    </section>
  );
}
