/**
 * Раздел «СТП» — отражение результатов из внешней системы Zephyr Scale
 * (test-cycle: pass/fail/blocked по тест-кейсам для конкретного РЦ).
 *
 * Это НЕ самостоятельная очередь emm — данные синхронизируются из внешней
 * интеграции, поэтому раздел размечен как read-only витрина с явной
 * пометкой источника и demo-ссылкой на цикл в Zephyr.
 */
import { useMemo, useState } from "react";
import { CheckCircle2, Clock3, ExternalLink, ListChecks, Octagon, XCircle, type LucideIcon } from "lucide-react";
import { RC_IDS } from "./rc";
import { Stat } from "./_shared";

export type StpStatus = "pass" | "fail" | "blocked" | "not_run";

export interface StpCase {
  id: string;
  title: string;
  rcId: string;
  status: StpStatus;
  cycleUrl: string;
}

const STP_CYCLE_URL = "https://zephyr.astralinux.ru/cycles/ASTRA-C123";

export const STP_CASES: StpCase[] = [
  { id: "ASTRA-T101", title: "Установка с загрузочного носителя", rcId: RC_IDS[0], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T102", title: "Настройка режима Смоленск (МРД+МКЦ)", rcId: RC_IDS[0], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T103", title: "Присоединение к домену FreeIPA", rcId: RC_IDS[0], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T104", title: "PostgreSQL: базовое резервное копирование", rcId: RC_IDS[0], status: "fail", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T105", title: "Сетевой стек: iptables + nftables совместимость", rcId: RC_IDS[0], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T106", title: "Аудит: пересылка событий в syslog", rcId: RC_IDS[0], status: "blocked", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T107", title: "Файловая система: квоты на ext4", rcId: RC_IDS[0], status: "not_run", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T108", title: "Docker: push/pull через внутренний registry", rcId: RC_IDS[0], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T201", title: "Установка с загрузочного носителя", rcId: RC_IDS[1], status: "not_run", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T202", title: "Обновление с предыдущего РЦ", rcId: RC_IDS[1], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T203", title: "Ceph: rados bench базовый прогон", rcId: RC_IDS[1], status: "not_run", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T301", title: "Хотфикс: регресс сетевого драйвера", rcId: RC_IDS[2], status: "pass", cycleUrl: STP_CYCLE_URL },
  { id: "ASTRA-T302", title: "Хотфикс: проверка совместимости с предыдущим ядром", rcId: RC_IDS[2], status: "pass", cycleUrl: STP_CYCLE_URL },
];

const STATUS_META: Record<StpStatus, { label: string; icon: LucideIcon; badge: "ok" | "danger" | "warn" | "accent" }> = {
  pass: { label: "Пройден", icon: CheckCircle2, badge: "ok" },
  fail: { label: "Провален", icon: XCircle, badge: "danger" },
  blocked: { label: "Заблокирован", icon: Octagon, badge: "warn" },
  not_run: { label: "Не выполнялся", icon: Clock3, badge: "accent" },
};

export function StpWorkzone() {
  const [rcFilter, setRcFilter] = useState<string | "all">("all");

  const filtered = useMemo(
    () => STP_CASES.filter((c) => rcFilter === "all" || c.rcId === rcFilter),
    [rcFilter],
  );

  const totals = useMemo(
    () => ({
      total: filtered.length,
      pass: filtered.filter((c) => c.status === "pass").length,
      fail: filtered.filter((c) => c.status === "fail").length,
      blocked: filtered.filter((c) => c.status === "blocked").length,
    }),
    [filtered],
  );

  return (
    <div className="grid gap-4">
      <div className="alert-warn text-xs">
        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
        <span>
          Данные СТП — зеркало внешнего цикла Zephyr Scale, синхронизируются автоматически.
          Управлять статусами тест-кейсов здесь нельзя — редактирование только в Zephyr.
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Тест-кейсов" value={String(totals.total)} icon={ListChecks} />
        <Stat title="Пройдено" value={String(totals.pass)} icon={CheckCircle2} kind="ok" />
        <Stat title="Провалено" value={String(totals.fail)} icon={XCircle} kind="danger" />
        <Stat title="Заблокировано" value={String(totals.blocked)} icon={Octagon} kind="warn" />
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-2 flex-wrap">
        <button
          type="button"
          className={`btn btn-sm ${rcFilter === "all" ? "btn-primary" : ""}`}
          onClick={() => setRcFilter("all")}
        >
          Все РЦ
        </button>
        {RC_IDS.map((rc) => (
          <button
            key={rc}
            type="button"
            className={`btn btn-sm mono ${rcFilter === rc ? "btn-primary" : ""}`}
            onClick={() => setRcFilter(rc)}
          >
            {rc}
          </button>
        ))}
      </div>

      <div className="surface border border-token rounded overflow-hidden">
        <div className="border-b border-token p-3 flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-accent" />
          <div className="text-sm font-medium">Тест-кейсы цикла · {filtered.length}</div>
        </div>
        <div className="overflow-auto">
          <table className="mini">
            <thead>
              <tr>
                <th>ID</th>
                <th>Название</th>
                <th>РЦ</th>
                <th>Статус</th>
                <th>Источник</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const meta = STATUS_META[c.status];
                const Icon = meta.icon;
                return (
                  <tr key={c.id}>
                    <td className="mono">{c.id}</td>
                    <td>{c.title}</td>
                    <td className="mono text-xs text-dim">{c.rcId}</td>
                    <td>
                      <span className={`badge badge-${meta.badge} inline-flex items-center gap-1`}>
                        <Icon className="w-3 h-3" />
                        {meta.label}
                      </span>
                    </td>
                    <td>
                      <a
                        href={c.cycleUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-accent text-xs inline-flex items-center gap-1 hover:underline"
                      >
                        <ExternalLink className="w-3 h-3" />
                        Zephyr
                      </a>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-center text-dim py-6">Нет тест-кейсов для выбранного РЦ</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
