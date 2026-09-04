/**
 * Раздел «РЦ» — каталог релиз-кандидатов (Release Candidate).
 *
 * В легаси-системе allta_app это `TestrunManager.addrc(build, rc)` /
 * `adduurc(...)` (uu — urgent update, срочный хотфикс-вариант РЦ). РЦ —
 * центральная ось всей системы тестирования: от него зависит список ядер
 * для тестов, резолв репозиториев и снимок ACS, который снимается под
 * конкретную версию. Сама функциональность снимков ACS уже реализована на
 * карточке сервера (`pages/server/tabs/acsSnapshots.tsx`) — здесь её не
 * дублируем, только показываем статус и даём переход.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Camera,
  ChevronDown,
  ChevronRight,
  Cpu,
  Package,
  Zap,
} from "lucide-react";
import { DataTable, Stat, type BadgeKind } from "./_shared";

export type RcKind = "regular" | "urgent";
export type RcStatus = "active" | "testing" | "released" | "archived";
export type AcsStatus = "ready" | "pending" | "missing";

export interface ReleaseCandidate {
  id: string;
  build: string;
  rc: string;
  kind: RcKind;
  status: RcStatus;
  kernels: string[];
  reposResolved: boolean;
  acsStatus: AcsStatus;
  acsNote: string;
  createdAt: string;
}

export const RC_LIST: ReleaseCandidate[] = [
  {
    id: "rc-1.8.7-32",
    build: "1.8.7",
    rc: "32",
    kind: "regular",
    status: "active",
    kernels: ["6.12.24-1.el11", "6.12.18-std-def"],
    reposResolved: true,
    acsStatus: "ready",
    acsNote: "снимок acs-1.8.7-32 создан 02.09, 12 серверов",
    createdAt: "29.08.2026",
  },
  {
    id: "rc-1.8.7-33",
    build: "1.8.7",
    rc: "33",
    kind: "regular",
    status: "testing",
    kernels: ["6.12.24-1.el11"],
    reposResolved: true,
    acsStatus: "pending",
    acsNote: "снимок поставлен в очередь ACS",
    createdAt: "01.09.2026",
  },
  {
    id: "uurc-1.8.7-32.1",
    build: "1.8.7",
    rc: "32.1",
    kind: "urgent",
    status: "active",
    kernels: ["6.12.24-1.el11-hotfix1"],
    reposResolved: true,
    acsStatus: "ready",
    acsNote: "срочный снимок под хотфикс, 3 сервера",
    createdAt: "03.09.2026",
  },
  {
    id: "rc-1.8.6-58",
    build: "1.8.6",
    rc: "58",
    kind: "regular",
    status: "released",
    kernels: ["6.6.63-un-def"],
    reposResolved: true,
    acsStatus: "ready",
    acsNote: "архивный снимок, только restore",
    createdAt: "10.07.2026",
  },
  {
    id: "rc-emm-0.4.0",
    build: "emm-0.4.0",
    rc: "1",
    kind: "regular",
    status: "testing",
    kernels: ["6.12.24-1.el11"],
    reposResolved: false,
    acsStatus: "missing",
    acsNote: "снимок ещё не создавался",
    createdAt: "03.09.2026",
  },
  {
    id: "rc-allta-2.16",
    build: "allta-2.16",
    rc: "4",
    kind: "regular",
    status: "archived",
    kernels: ["6.1.99-std-def"],
    reposResolved: true,
    acsStatus: "ready",
    acsNote: "архивный снимок",
    createdAt: "02.05.2026",
  },
];

export const RC_IDS = RC_LIST.map((rc) => rc.id);

const RC_STATUS_META: Record<RcStatus, { label: string; badge?: BadgeKind }> = {
  active: { label: "Активен", badge: "accent" },
  testing: { label: "На тестировании", badge: "warn" },
  released: { label: "Выпущен", badge: "ok" },
  archived: { label: "Архив" },
};

const ACS_STATUS_META: Record<AcsStatus, { label: string; badge: BadgeKind }> = {
  ready: { label: "Снимок готов", badge: "ok" },
  pending: { label: "Снимок ставится", badge: "warn" },
  missing: { label: "Снимка нет", badge: "danger" },
};

export function RcWorkzone() {
  const [openId, setOpenId] = useState<string | null>(RC_LIST[0]?.id ?? null);

  const totals = useMemo(
    () => ({
      total: RC_LIST.length,
      active: RC_LIST.filter((rc) => rc.status === "active").length,
      testing: RC_LIST.filter((rc) => rc.status === "testing").length,
      urgent: RC_LIST.filter((rc) => rc.kind === "urgent").length,
    }),
    [],
  );

  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Всего РЦ" value={String(totals.total)} icon={Package} />
        <Stat title="Активные" value={String(totals.active)} icon={Zap} kind="ok" />
        <Stat title="На тестировании" value={String(totals.testing)} icon={Cpu} kind="warn" />
        <Stat title="Срочные (uu)" value={String(totals.urgent)} icon={Zap} kind="danger" />
      </div>

      <div className="grid gap-2">
        {RC_LIST.map((rc) => {
          const open = openId === rc.id;
          const statusMeta = RC_STATUS_META[rc.status];
          const acsMeta = ACS_STATUS_META[rc.acsStatus];
          return (
            <div key={rc.id} className="surface border border-token rounded overflow-hidden">
              <button
                type="button"
                onClick={() => setOpenId(open ? null : rc.id)}
                className="w-full grid grid-cols-1 md:grid-cols-[24px_minmax(160px,1fr)_120px_160px_160px] gap-3 items-center px-4 py-3 text-left hover-bg"
              >
                {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
                <div className="min-w-0">
                  <div className="font-semibold mono truncate">{rc.id}</div>
                  <div className="text-xs text-dim truncate">
                    build {rc.build} · {rc.kind === "urgent" ? "срочный хотфикс" : "плановый РЦ"} · {rc.createdAt}
                  </div>
                </div>
                <span className={statusMeta.badge ? `badge badge-${statusMeta.badge}` : "badge"}>{statusMeta.label}</span>
                <span className={`badge badge-${acsMeta.badge} inline-flex items-center gap-1 w-fit`}>
                  <Camera className="w-3 h-3" />
                  {acsMeta.label}
                </span>
                <span className={`text-xs ${rc.reposResolved ? "text-ok" : "text-warn"}`}>
                  {rc.reposResolved ? "репозитории зарезолвлены" : "резолв репозиториев не завершён"}
                </span>
              </button>
              {open && (
                <div className="border-t border-token p-4 grid gap-3">
                  <div className="surface-2 border border-token rounded p-3">
                    <div className="text-xs text-dim mb-2 flex items-center gap-1">
                      <Cpu className="w-3.5 h-3.5" /> Ядра для тестов
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {rc.kernels.map((k) => (
                        <span key={k} className="badge mono">{k}</span>
                      ))}
                    </div>
                  </div>
                  <div className="surface-2 border border-token rounded p-3 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <div className="text-xs text-dim mb-1 flex items-center gap-1">
                        <Camera className="w-3.5 h-3.5" /> Снимок ACS
                      </div>
                      <div className="text-sm">{rc.acsNote}</div>
                    </div>
                    <Link to="/servers" className="btn btn-sm">
                      Перейти к снимкам ACS
                    </Link>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <DataTable
        title="Сводка по РЦ"
        icon={Package}
        columns={["РЦ", "Build", "Тип", "Ядра", "Статус"]}
        rows={RC_LIST.map((rc) => [
          rc.id,
          rc.build,
          rc.kind === "urgent" ? "срочный" : "плановый",
          String(rc.kernels.length),
          rc.status,
        ])}
      />
    </div>
  );
}
