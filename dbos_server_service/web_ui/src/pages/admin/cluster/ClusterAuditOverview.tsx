import { useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ChevronRight,
  Download,
  FileText,
  ShieldAlert,
  Sliders,
} from "lucide-react";
import { Link } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { hasAuditLogAccess, isReadOnlyForCluster } from "@/lib/rbac";
import { formatMsk } from "@/lib/datetime";
import { exportEvents, getEventStats } from "@/api/loging/events";
import type { EventStatsResponse, Severity } from "@/api/loging/types";

const SEV_ORDER: Severity[] = [
  "CRITICAL",
  "ERROR",
  "WARNING",
  "INFO",
  "DEBUG",
  "TRACE",
];

const SEV_LABEL: Record<Severity, string> = {
  CRITICAL: "CRITICAL",
  ERROR: "ERROR",
  WARNING: "WARN",
  INFO: "INFO",
  DEBUG: "DEBUG",
  TRACE: "TRACE",
};

const numFmt = new Intl.NumberFormat("ru-RU");

export function ClusterAuditOverview() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();

  const canAudit = hasAuditLogAccess(persona);
  const [mockToast, setMockToast] = useState<string | null>(null);

  const triggerMockExport = () => {
    setMockToast("Экспорт за 24ч начался — файл придёт в /log/exports");
    window.setTimeout(() => setMockToast(null), 3500);
  };

  return (
    <div className="space-y-4 max-w-3xl relative">
      {mockMode && mockToast && (
        <div className="toast toast-success absolute top-2 right-2 z-30">
          <span className="text-sm font-medium">{mockToast}</span>
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
      {!mockMode && (canAudit ? <LiveAudit readonly={readonly} /> : <NoAuditRole />)}
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
                onClick={triggerMockExport}
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

/**
 * Графа для персон без роли аудита (`account_admin` / `dep_admin` доходят сюда
 * через /admin, но loging_service ответит им 403). Эндпоинты статистики и
 * экспорта НЕ вызываются — показываем объяснение и ссылку, чтобы не словить
 * 403-на-загрузке.
 */
function NoAuditRole() {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <FileText className="w-4 h-4 text-dim" /> Audit overview
        </h3>
      </div>
      <div className="empty-card text-xs flex items-start gap-2">
        <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          Статистика и экспорт аудита доступны только ролям{" "}
          <b>logging_admin</b> / <b>logging_reader</b>. Для вашей роли
          loging_service закрывает чтение событий.
        </span>
      </div>
    </div>
  );
}

/**
 * Реальная сводка для персоны с доступом к аудиту (logging_admin под /admin).
 * Тянет `GET /events/stats` за 24ч, кнопка «Экспорт за 24ч» дёргает
 * `GET /events/export`.
 */
function LiveAudit({ readonly }: { readonly: boolean }) {
  const statsQ = useQuery<EventStatsResponse>(
    () => getEventStats({ window_hours: 24 }),
    [],
  );
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(
    null,
  );

  async function onExport() {
    setExporting(true);
    setNotice(null);
    try {
      const res = await exportEvents({ window_hours: 24 });
      setNotice(
        res.truncated
          ? {
              kind: "warn",
              text: `Экспорт ${res.filename} скачан, но усечён до 50 000 строк — сузьте окно или фильтр.`,
            }
          : { kind: "ok", text: `Экспорт ${res.filename} скачан.` },
      );
    } catch (e) {
      setNotice({ kind: "err", text: apiErrMsg(e, "Экспорт не удался") });
    } finally {
      setExporting(false);
    }
  }

  const stats = statsQ.data;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <FileText className="w-4 h-4 text-accent" /> Audit overview
        </h3>
        <Link to="/log" className="text-xs text-accent flex items-center gap-1">
          открыть полный лог <ChevronRight className="w-3 h-3" />
        </Link>
      </div>

      {statsQ.loading && (
        <div className="empty-card text-xs text-center">Загрузка статистики…</div>
      )}

      {statsQ.error && (
        <div className="alert alert-danger flex items-start gap-2 text-xs">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1">
            <div>{apiErrMsg(statsQ.error, "Статистика не загрузилась")}</div>
            <button className="btn btn-ghost mt-2" onClick={() => statsQ.refetch()}>
              Повторить
            </button>
          </div>
        </div>
      )}

      {!statsQ.loading && !statsQ.error && stats && (
        <AuditStatsBody stats={stats} />
      )}

      {notice && (
        <div
          className={`mt-3 text-xs ${
            notice.kind === "err"
              ? "alert alert-danger"
              : notice.kind === "warn"
                ? "alert-warn"
                : "alert alert-success"
          }`}
          role="status"
        >
          {notice.text}
        </div>
      )}

      <div className="mt-3 pt-3 border-t border-token flex gap-2 flex-wrap items-center">
        <Link to="/log" className="btn btn-sm flex items-center gap-1">
          <FileText className="w-3.5 h-3.5" /> Открыть полную выборку
        </Link>
        <button
          type="button"
          className="btn btn-sm flex items-center gap-1"
          onClick={onExport}
          disabled={exporting}
        >
          <Download className="w-3.5 h-3.5" /> {exporting ? "Экспорт…" : "Экспорт за 24ч"}
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
  );
}

function AuditStatsBody({ stats }: { stats: EventStatsResponse }) {
  const sevRows = SEV_ORDER.map((s) => ({ sev: s, count: stats.by_severity[s] ?? 0 })).filter(
    (r) => r.count > 0,
  );
  const sevMax = Math.max(1, ...sevRows.map((r) => r.count));
  const success = stats.by_status.success ?? 0;
  const failure = stats.by_status.failure ?? 0;

  if (stats.total === 0) {
    return <div className="empty-card text-xs text-center">За окно событий нет.</div>;
  }

  return (
    <>
      <div className="text-xs text-dim mb-3">
        {formatMsk(stats.from_time)} → {formatMsk(stats.to_time)}
      </div>

      {stats.truncated && (
        <div className="alert-warn text-xs mb-3" role="status">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">Выборка усечена — счётчики неполные.</span>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3 mb-3">
        <div>
          <div className="stat-label">events / 24h</div>
          <div className="stat-big">{numFmt.format(stats.total)}</div>
        </div>
        <div>
          <div className="stat-label">success</div>
          <div className="stat-big">{numFmt.format(success)}</div>
        </div>
        <div>
          <div className="stat-label">failure</div>
          <div className={`stat-big ${failure > 0 ? "text-danger" : ""}`}>
            {numFmt.format(failure)}
          </div>
        </div>
      </div>

      {sevRows.length > 0 && (
        <div className="mt-3 pt-3 border-t border-token">
          <div className="text-xs uppercase tracking-wider text-dim mb-2">
            Severity distribution
          </div>
          {sevRows.map((r) => (
            <div key={r.sev} className="sev-bar">
              <div>{SEV_LABEL[r.sev]}</div>
              <div className="bar">
                <span
                  style={{
                    width: `${Math.round((r.count / sevMax) * 100)}%`,
                    background:
                      r.sev === "CRITICAL" || r.sev === "ERROR"
                        ? "var(--danger)"
                        : r.sev === "WARNING"
                          ? "var(--warn)"
                          : "var(--accent)",
                  }}
                />
              </div>
              <div
                className={`text-right mono ${
                  r.sev === "CRITICAL" || r.sev === "ERROR" ? "text-danger" : ""
                }`}
              >
                {numFmt.format(r.count)}
              </div>
            </div>
          ))}
        </div>
      )}

      {Object.keys(stats.by_service).length > 0 && (
        <div className="mt-3 pt-3 border-t border-token">
          <div className="text-xs uppercase tracking-wider text-dim mb-2">По сервисам</div>
          <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            {Object.entries(stats.by_service)
              .sort((a, b) => b[1] - a[1])
              .map(([svc, count]) => (
                <div key={svc} className="contents">
                  <div className="mono text-dim truncate">{svc}</div>
                  <div className="text-right mono">{numFmt.format(count)}</div>
                </div>
              ))}
          </div>
        </div>
      )}
    </>
  );
}
