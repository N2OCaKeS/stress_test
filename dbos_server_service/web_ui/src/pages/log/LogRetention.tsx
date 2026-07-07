import {
  Archive,
  Clock,
  Play,
  GitBranch,
  Edit3,
  Filter,
  ExternalLink,
  FileText,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useMockMode } from "@/api/auth/useQuery";
import { LogRetentionLive } from "./LogRetentionLive";

interface AuditRow {
  time: string;
  action: string;
  actor: string;
  detail: string;
  status: string;
  statusKind: "ok" | "warn" | "accent" | "";
}

const AUDIT_ROWS: AuditRow[] = [
  { time: "сегодня 03:17", action: "sweep_completed", actor: "dbos_bot_audit_export", detail: "удалено 14 312 строк · освобождено 94 MB", status: "ok", statusKind: "ok" },
  { time: "сегодня 03:15", action: "sweep_started", actor: "dbos_bot_audit_export", detail: "по расписанию · cron 0 3 * * *", status: "info", statusKind: "" },
  { time: "2 дн назад", action: "rule_updated", actor: "carol", detail: "CRITICAL → 365 дней (было 180)", status: "изменение", statusKind: "warn" },
  { time: "2 дн назад 03:18", action: "sweep_completed", actor: "dbos_bot_audit_export", detail: "удалено 13 891 строк · освобождено 88 MB", status: "ok", statusKind: "ok" },
  { time: "5 дн назад", action: "manual_sweep", actor: "carol", detail: 'причина: "before quarterly export"', status: "ok", statusKind: "ok" },
  { time: "9 дн назад", action: "rule_added", actor: "carol", detail: "INFO worker/healthcheck → 30 дней", status: "новое", statusKind: "accent" },
  { time: "14 дн назад", action: "sweep_failed", actor: "dbos_bot_audit_export", detail: "таймаут блокировки · повтор 03:35 → ok", status: "warn", statusKind: "warn" },
  { time: "21 дн назад", action: "rule_removed", actor: "carol", detail: '"TRACE → 1 day" удалено (нет источников)', status: "изменение", statusKind: "" },
  { time: "28 дн назад", action: "policy_changed", actor: "carol", detail: "default 60 → 90 дней", status: "изменение", statusKind: "warn" },
  { time: "42 дн назад", action: "partition_plan_deferred", actor: "carol", detail: "решение: hold (sweep укладывается в SLA)", status: "решение", statusKind: "" },
];

export function LogRetention() {
  const mockMode = useMockMode();
  if (!mockMode) {
    return <LogRetentionLive />;
  }
  return (
    <Shell breadcrumb="loging_service / retention">
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="scroll-block w-full px-8 py-8">
          <section className="mb-6">
            <div className="text-2xl font-bold mb-1 flex items-center gap-2">
              <Archive className="w-6 h-6 text-accent" /> Политика хранения
            </div>
            <div className="text-dim text-sm">
              Управление сроком хранения audit-событий. Каждый sweep сам пишется
              в audit-канал.
            </div>
          </section>

          <section className="grid gap-4 md:grid-cols-2 mb-6">
            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Archive className="w-3 h-3" /> Текущая политика
              </div>
              <div className="stat-big">default 90 дней</div>
              <div className="text-xs text-dim mt-3 mb-2">свои правила:</div>
              <div className="text-sm flex flex-col gap-2">
                <div className="surface-2 border border-token rounded px-3 py-2 flex items-center justify-between">
                  <span>
                    <span className="badge badge-danger">CRITICAL</span> любого
                    сервиса
                  </span>
                  <span className="mono">→ 365 дней</span>
                </div>
                <div className="surface-2 border border-token rounded px-3 py-2 flex items-center justify-between">
                  <span>
                    <span className="badge">INFO</span> в worker / healthcheck
                  </span>
                  <span className="mono">→ 30 дней</span>
                </div>
                <div className="surface-2 border border-token rounded px-3 py-2 flex items-center justify-between">
                  <span>
                    <span className="badge">DEBUG</span> любого сервиса
                  </span>
                  <span className="mono">→ 7 дней</span>
                </div>
              </div>
              <div className="mt-4 flex gap-2">
                <button className="btn flex items-center gap-1">
                  <Edit3 className="w-4 h-4" /> Изменить политику
                </button>
                <button className="btn flex items-center gap-1">
                  <Filter className="w-4 h-4" /> Добавить правило
                </button>
              </div>
            </div>

            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Clock className="w-3 h-3" /> Последняя очистка
              </div>
              <div className="stat-big">03:17</div>
              <div className="text-xs text-dim mt-2">2026-06-10 · ok</div>
              <div className="text-sm mt-3">
                <div className="stat-row">
                  <span className="text-dim">rows_removed</span>
                  <span className="mono">14 312</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">duration</span>
                  <span className="mono">1m 42s</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">disk_freed</span>
                  <span className="mono">94 MB</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">audit_event_id</span>
                  <span className="mono text-xs">evt_8a3f2d1c</span>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Play className="w-3 h-3" /> Следующая очистка
              </div>
              <div className="stat-big">завтра · 03:00 UTC</div>
              <div className="text-xs text-dim mt-2">
                расписание: ежедневно 03:00 UTC · исполнитель{" "}
                <span className="mono">dbos_bot_audit_export</span>
              </div>
              <div className="text-sm mt-3">
                <div className="stat-row">
                  <span className="text-dim">оценка строк</span>
                  <span className="mono">~12 800</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">cron</span>
                  <span className="mono">0 3 * * *</span>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Archive className="w-3 h-3" /> Размер
              </div>
              <div className="stat-big">3.42 GB</div>
              <div className="text-xs text-dim mt-2">
                таблица events · 8 472 строки за 24ч
              </div>
              <div className="text-sm mt-3">
                <div className="stat-row">
                  <span className="text-dim">всего строк</span>
                  <span className="mono">2 184 119</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">старейшее событие</span>
                  <span className="mono">2026-03-10</span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">использование диска</span>
                  <span>
                    <span className="mono">3.42 GB</span> / 50 GB
                  </span>
                </div>
                <div className="stat-row">
                  <span className="text-dim">рост</span>
                  <span>
                    <span className="text-ok">+38 MB / день</span>
                  </span>
                </div>
              </div>
            </div>
          </section>

          <section className="grid gap-4 md:grid-cols-2 mb-6">
            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <GitBranch className="w-3 h-3" /> План партиционирования
              </div>
              <div className="text-sm mt-1">
                помесячные партиции{" "}
                <span className="badge badge-warn">отложено</span>
              </div>
              <div className="text-xs text-dim mt-2">
                При росте &gt;10 GB / месяц планируется разбить таблицу events на
                helpers по месяцу для ускорения sweep. Сейчас sweep укладывается
                в 2 минуты — переключение не нужно.
              </div>
              <a
                href="https://confluence.local/dbos/runbook/log-partition"
                className="btn mt-3 inline-flex items-center gap-2 text-xs"
              >
                <ExternalLink className="w-3 h-3" /> runbook: log-partition
              </a>
            </div>

            <div className="card">
              <div className="stat-label flex items-center gap-2">
                <Play className="w-3 h-3" /> Ручная очистка
              </div>
              <div className="text-sm mt-1">Запустить вне расписания</div>
              <div className="text-xs text-dim mt-2">
                Сразу применит текущий policy. Удалённые строки попадут в
                audit-канал. Требуется явное подтверждение и причина (audit
                reason).
              </div>
              <div className="mt-3 flex flex-col gap-2">
                <input
                  className="surface-2 border border-token rounded px-3 py-2 text-sm"
                  placeholder="причина для аудита: 'before quarterly export'"
                />
                <button className="btn btn-primary flex items-center justify-center gap-2">
                  <Play className="w-4 h-4" /> Запустить очистку сейчас
                </button>
              </div>
            </div>
          </section>

          <section>
            <div className="card">
              <div className="stat-label flex items-center gap-2 mb-3">
                <FileText className="w-3 h-3" /> Аудит — события хранения
                (последние 10)
              </div>
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">время</th>
                    <th className="pb-2 pr-3">действие</th>
                    <th className="pb-2 pr-3">инициатор</th>
                    <th className="pb-2 pr-3">детали</th>
                    <th className="pb-2">статус</th>
                  </tr>
                </thead>
                <tbody>
                  {AUDIT_ROWS.map((r, i) => (
                    <tr key={i} className="border-t border-token">
                      <td className="py-2 text-dim text-xs">{r.time}</td>
                      <td className="mono">{r.action}</td>
                      <td className="mono">{r.actor}</td>
                      <td className="text-xs text-dim">{r.detail}</td>
                      <td>
                        <span
                          className={`badge${r.statusKind ? ` badge-${r.statusKind}` : ""}`}
                        >
                          {r.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </Shell>
  );
}
