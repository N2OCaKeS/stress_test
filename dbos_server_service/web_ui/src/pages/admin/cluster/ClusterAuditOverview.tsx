import { useState } from "react";
import { ChevronRight, Download, FileText, ShieldAlert, Sliders } from "lucide-react";
import { Link } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { isReadOnlyForCluster } from "@/lib/rbac";

export function ClusterAuditOverview() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();
  const [toast, setToast] = useState<string | null>(null);

  const triggerExport = () => {
    setToast("Экспорт за 24ч начался — файл придёт в /log/exports");
    window.setTimeout(() => setToast(null), 3500);
  };

  return (
    <div className="space-y-4 max-w-3xl relative">
      {toast && (
        <div className="toast toast-success absolute top-2 right-2 z-30">
          <span className="text-sm font-medium">{toast}</span>
        </div>
      )}
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Доступен только просмотр статистики
            и переход в полный лог. Менять правила и retention нельзя.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <FileText className="w-4 h-4 text-dim" /> Audit overview
            </h3>
            <Link to="/log" className="text-xs text-accent flex items-center gap-1">
              открыть полный лог <ChevronRight className="w-3 h-3" />
            </Link>
          </div>
          <div className="empty-card text-xs">
            Endpoint статистики audit-канала ещё не подключён. Используй
            <Link to="/log" className="text-accent mx-1">/log</Link>
            для полной выборки.
          </div>
          <div className="mt-3 flex gap-2 flex-wrap items-center">
            <Link to="/log" className="btn btn-sm flex items-center gap-1">
              <FileText className="w-3.5 h-3.5" /> Открыть полную выборку
            </Link>
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              onClick={triggerExport}
            >
              <Download className="w-3.5 h-3.5" /> Экспорт за 24ч
            </button>
            {!readonly && (
              <Link
                to="/admin/services.loging.rules"
                className="btn btn-sm flex items-center gap-1"
              >
                <Sliders className="w-3.5 h-3.5" /> Настроить правила
              </Link>
            )}
          </div>
        </div>
      )}
      {mockMode && (
        <>
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2">
                <FileText className="w-4 h-4 text-accent" /> Audit overview
              </h3>
              <Link to="/log" className="text-xs text-accent flex items-center gap-1">
                открыть полный лог <ChevronRight className="w-3 h-3" />
              </Link>
            </div>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <div className="stat-label">events / 24h</div>
                <div className="stat-big">12 487</div>
              </div>
              <div>
                <div className="stat-label">CRITICAL / week</div>
                <div className="stat-big text-danger">14</div>
              </div>
            </div>
            <div className="mt-3 pt-3 border-t border-token grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
              <div className="text-dim">top action</div>
              <div className="mono">credential.read</div>
              <div className="text-dim">count</div>
              <div>4 192</div>
              <div className="text-dim">top actor</div>
              <div className="mono">worker_bot</div>
              <div className="text-dim">count</div>
              <div>2 871</div>
            </div>
            <div className="mt-3 pt-3 border-t border-token flex gap-2 flex-wrap items-center">
              <Link to="/log" className="btn btn-sm flex items-center gap-1">
                <FileText className="w-3.5 h-3.5" /> Открыть полную выборку
              </Link>
              <button
                type="button"
                className="btn btn-sm flex items-center gap-1"
                onClick={triggerExport}
              >
                <Download className="w-3.5 h-3.5" /> Экспорт за 24ч
              </button>
              {!readonly && (
                <Link
                  to="/admin/services.loging.rules"
                  className="btn btn-sm flex items-center gap-1"
                >
                  <Sliders className="w-3.5 h-3.5" /> Настроить правила
                </Link>
              )}
            </div>
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Severity distribution · 24h</h3>
              <span className="text-xs text-dim">live</span>
            </div>
            {[
              { label: "CRITICAL", count: "14", width: "2%", color: "var(--danger)", opacity: 1, textClass: "text-danger" },
              { label: "ERROR", count: "102", width: "8%", color: "var(--danger)", opacity: 0.7 },
              { label: "WARN", count: "228", width: "18%", color: "var(--warn)", opacity: 1 },
              { label: "INFO", count: "9 678", width: "76%", color: "var(--accent)", opacity: 1 },
              { label: "DEBUG", count: "2 465", width: "22%", color: "var(--text-dim)", opacity: 1 },
            ].map((s) => (
              <div key={s.label} className="sev-bar">
                <div>{s.label}</div>
                <div className="bar">
                  <span
                    style={{ width: s.width, background: s.color, opacity: s.opacity }}
                  />
                </div>
                <div className={`text-right mono ${s.textClass ?? ""}`}>{s.count}</div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
