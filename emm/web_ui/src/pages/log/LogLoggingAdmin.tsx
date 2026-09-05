import { useState } from "react";
import {
  Search,
  Filter,
  User,
  Clock,
  ChevronDown,
  Bell,
  ShieldCheck,
  Download,
  Cog,
  LockKeyhole,
  Share2,
  Flame,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  EVENT_ROWS,
  Facets,
  type EventRow,
  SEVERITY_FACETS,
  ACTION_FACETS,
  ACTOR_FACETS,
  TARGET_FACETS,
  STATUS_FACETS,
} from "./logShared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

/**
 * Logging-admin log view.
 * Workzone shows secret.master_key_rotated CRITICAL event with admin actions.
 */
export function LogLoggingAdmin() {
  const [selected, setSelected] = useState<string>("ev-002");
  const [live, setLive] = useState(true);

  return (
    <Shell breadcrumb="loging_service / events">
      <LogMiddle
        selected={selected}
        onSelect={setSelected}
        live={live}
        onLiveToggle={() => setLive((x) => !x)}
        showLive
      />
      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div
            className="w-12 h-12 rounded flex items-center justify-center"
            style={{ background: "var(--crit-bg)", color: "var(--crit-fg)" }}
          >
            <Flame className="w-6 h-6" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold truncate mono">
                secret.master_key_rotated
              </h1>
              <span className="sev sev-CRITICAL">CRITICAL</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="mono">req_a8f9c12b4e6d4711</span>
              <span>·</span>
              <span className="mono">2026-06-10 15:41:55.328 UTC</span>
              <span>·</span>
              <span>2 минуты назад</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button className="flex items-center gap-1">
              <Bell className="w-4 h-4" /> Добавить в правила
            </Button>
            <Button className="flex items-center gap-1">
              <ShieldCheck className="w-4 h-4" /> Отметить как известное
            </Button>
            <Button variant="primary" className="flex items-center gap-1">
              <Download className="w-4 h-4" /> Экспорт JSON
            </Button>
          </div>
        </div>

        <EventDetail />
      </section>
    </Shell>
  );
}

interface LogMiddleProps {
  selected: string;
  onSelect: (id: string) => void;
  live: boolean;
  onLiveToggle: () => void;
  showLive: boolean;
}

const TIME_RANGE_OPTIONS = [
  { value: "1h", label: "за 1ч" },
  { value: "24h", label: "за 24ч" },
  { value: "7d", label: "за 7д" },
  { value: "custom", label: "произвольно…" },
];

export function LogMiddle({
  selected,
  onSelect,
  live,
  onLiveToggle,
  showLive,
}: LogMiddleProps) {
  const [timeRange, setTimeRange] = useState("24h");
  return (
    <section className="w-[400px] shrink-0 border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder="Поиск по action / actor / target..."
          />
        </div>
        <div className="flex items-center gap-2 text-xs">
          <Clock className="w-3.5 h-3.5 text-dim" />
          <Dropdown
            mode="single"
            options={TIME_RANGE_OPTIONS}
            value={timeRange}
            onChange={setTimeRange}
          />
          {showLive ? (
            <Button
              onClick={onLiveToggle}
              className="ml-auto flex items-center gap-1.5 text-xs py-0.5 px-2"
            >
              {live && <span className="live-dot" />}
              <span>{live ? "Онлайн" : "Пауза"}</span>
            </Button>
          ) : (
            <span className="ml-auto text-[10px] text-dim flex items-center gap-1">
              статично
            </span>
          )}
        </div>
      </div>

      <div className="border-b border-token px-2 py-2 flex flex-col gap-1 text-xs">
        <Facets title="Важность" items={SEVERITY_FACETS} severityFacets />
        <Facets title="Действие" hint="(топ-8)" items={ACTION_FACETS} mono />
        <Facets title="Инициатор" hint="(топ-5)" items={ACTOR_FACETS} icon="user" />
        <Facets title="Тип объекта" items={TARGET_FACETS} />
        <Facets title="Статус" items={STATUS_FACETS} />
      </div>

      <div className="flex-1 overflow-y-auto">
        {EVENT_ROWS.map((row: EventRow) => (
          <button
            key={row.id}
            onClick={() => onSelect(row.id)}
            className={`ev-row text-left w-full ${
              selected === row.id ? "active" : ""
            }`}
          >
            <div className="ev-time">{row.time}</div>
            <div>
              <div className="ev-action">{row.action}</div>
              <div className="ev-meta">{row.meta}</div>
            </div>
            <span className={`sev sev-${row.severity}`}>{row.label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function EventDetail() {
  return (
    <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
      <div className="surface border border-token rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <User className="w-4 h-4 text-accent" />
          <div className="text-xs uppercase tracking-wider text-dim">Инициатор</div>
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">Имя пользователя</span>
            <span>carol</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">ID</span>
            <span className="mono text-xs">usr_c4a2b1e9f8d34721</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">IP</span>
            <span className="mono">10.177.103.42</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">User-Agent</span>
            <span className="text-xs">DBOS-CLI/1.4.2 · Linux</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Тип субъекта</span>
            <span>
              <Badge>user</Badge>
            </span>
          </div>
        </div>
      </div>

      <div className="surface border border-token rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <LockKeyhole className="w-4 h-4 text-warn" />
          <div className="text-xs uppercase tracking-wider text-dim">
            Объект
          </div>
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">Тип</span>
            <span>
              <Badge>master_key</Badge>
            </span>
          </div>
          <div className="stat-row">
            <span className="text-dim">ID</span>
            <span className="mono text-xs">mkey_v3_b1aef02c</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Название</span>
            <span>master_key (текущий)</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Отдел</span>
            <span>Разработка</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Предыдущая версия</span>
            <span className="mono text-xs">mkey_v2_47ab819f</span>
          </div>
        </div>
      </div>

      <div className="surface border border-token rounded-lg p-4 col-span-2">
        <div className="flex items-center gap-2 mb-3">
          <Filter className="w-4 h-4 text-dim" />
          <div className="text-xs uppercase tracking-wider text-dim">
            Детали JSON
          </div>
        </div>
        <pre className="json-block mono whitespace-pre">{`{
  "event_id": "evt_98a2c41eb7f04812",
  "action": "secret.master_key_rotated",
  "severity": "CRITICAL",
  "status": "success",
  "new_key_version": 3,
  "prev_key_version": 2,
  "reencrypted_credentials": 412,
  "duration_ms": 8417,
  "reason": "scheduled_rotation_180d",
  "approval_request_id": "appr_3f1a82c9"
}`}</pre>
      </div>

      <div className="surface border border-token rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <Cog className="w-4 h-4 text-dim" />
          <div className="text-xs uppercase tracking-wider text-dim">
            Сервис
          </div>
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">Источник</span>
            <span>
              <Badge kind="warn">secret_service</Badge>
            </span>
          </div>
          <div className="stat-row">
            <span className="text-dim">X-Service-Identity</span>
            <span className="mono text-xs">secret_service@dbos-srv-01</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">X-Request-ID</span>
            <span className="mono text-xs">req_a8f9c12b4e6d4711</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Версия</span>
            <span className="mono">1.5.2</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Trace</span>
            <span className="mono text-xs text-accent">trace_8c1f0a92</span>
          </div>
        </div>
      </div>

      <div className="surface border border-token rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <Share2 className="w-4 h-4 text-dim" />
          <div className="text-xs uppercase tracking-wider text-dim">
            Связанные события
          </div>
          <span className="text-[10px] text-dim ml-auto">по request_id</span>
        </div>
        <table className="w-full related-tbl">
          <thead>
            <tr>
              <th>Время</th>
              <th>Действие</th>
              <th>Важн.</th>
            </tr>
          </thead>
          <tbody>
            {[
              { t: "15:41:54.211", a: "approval.granted", s: "INFO" },
              { t: "15:41:55.012", a: "service.rotation_started", s: "CRITICAL" },
              { t: "15:41:55.328", a: "secret.master_key_rotated", s: "CRITICAL" },
              { t: "15:42:03.745", a: "credential.reencrypted_bulk", s: "INFO" },
              { t: "15:42:11.108", a: "service.rotation_completed", s: "INFO" },
            ].map((r) => (
              <tr key={r.t}>
                <td className="mono">{r.t}</td>
                <td className="mono">{r.a}</td>
                <td>
                  <span className={`sev sev-${r.s}`}>
                    {r.s === "CRITICAL" ? "CRIT" : r.s}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="col-span-2 text-[11px] text-dim flex items-center gap-1">
        <ChevronDown className="w-3 h-3" /> прокручиваемая зона события
      </div>
    </div>
  );
}
