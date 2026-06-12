/**
 * Карточка сервера справа от Aside-листа.
 *
 * Заголовок: name + dept + status badge + busy chip + power state. Под
 * заголовком — Tabs (наш контролируемый `@/components/ui/Tabs`), внутри —
 * по одному файлу-заглушке на вкладку из `./tabs/`. Заглушки заполняют
 * параллельно C2..C9 — здесь только маршрутизация вкладок и общий header.
 */
import { useEffect, useState } from "react";
import { Server as ServerIcon } from "lucide-react";
import { Tabs } from "@/components/ui/Tabs";
import { useQuery } from "@/api/auth/useQuery";
import { getServer } from "@/api/server/servers";
import { useDeptLabel } from "@/lib/labels";
import { ApiError } from "@/api/client";
import type { Server, ServerStatus, BusyState } from "@/api/server/types";
import { OverviewTab } from "./tabs/overview";
import { HardwareTab } from "./tabs/hardware";
import { IpmiTab } from "./tabs/ipmi";
import { AccountsTab } from "./tabs/accounts";
import { ConsoleTab } from "./tabs/console";
import { DriftTab } from "./tabs/drift";
import { PackagesTab } from "./tabs/packages";
import { ManageTab } from "./tabs/manage";

type TabId =
  | "overview"
  | "hardware"
  | "ipmi"
  | "accounts"
  | "console"
  | "drift"
  | "packages"
  | "manage";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "hardware", label: "Железо" },
  { id: "ipmi", label: "IPMI" },
  { id: "accounts", label: "Аккаунты" },
  { id: "console", label: "Консоль" },
  { id: "drift", label: "Drift" },
  { id: "packages", label: "Пакеты" },
  { id: "manage", label: "Управление" },
];

const STATUS_LABEL: Record<ServerStatus, string> = {
  unknown: "Unknown",
  online: "Online",
  offline: "Offline",
  maintenance: "Maintenance",
  decommissioned: "Decommissioned",
};

const STATUS_KIND: Record<ServerStatus, "ok" | "warn" | "danger" | ""> = {
  unknown: "",
  online: "ok",
  offline: "danger",
  maintenance: "warn",
  decommissioned: "",
};

const BUSY_LABEL: Record<BusyState, string> = {
  free: "Свободен",
  busy: "Занят",
  testing: "В тесте",
};

const BUSY_KIND: Record<BusyState, "ok" | "warn" | "danger"> = {
  free: "ok",
  busy: "warn",
  testing: "warn",
};

interface ServerDetailProps {
  serverId: string;
  onDeleted?: () => void;
}

export function ServerDetail({ serverId, onDeleted }: ServerDetailProps) {
  const [tab, setTab] = useState<TabId>("overview");
  const q = useQuery<Server>(() => getServer(serverId), [serverId]);

  // Локальная копия карточки: переключение вкладок не перемонтирует
  // ServerDetail, поэтому мутации внутри табов (PATCH overview/hardware)
  // должны подняться сюда, чтобы header и соседние вкладки увидели свежий
  // объект без перезагрузки. Сидируется из query, обновляется через колбэк.
  const [server, setServer] = useState<Server | undefined>(q.data);
  useEffect(() => {
    setServer(q.data);
  }, [q.data]);

  if (q.loading) {
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="p-8 text-sm text-dim">Загружаем сервер…</div>
      </section>
    );
  }
  if (q.error || !q.data) {
    const msg =
      q.error instanceof ApiError
        ? `${q.error.errorCode}: ${q.error.message}`
        : q.error?.message ?? "Сервер не найден";
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="m-5 alert alert-danger flex items-center gap-3">
          <span className="text-sm">{msg}</span>
          <button className="btn btn-ghost" onClick={() => q.refetch()}>
            Повторить
          </button>
        </div>
      </section>
    );
  }

  const current = server ?? q.data;
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <ServerHeader server={current} />
      <Tabs
        active={tab}
        onChange={(id) => setTab(id as TabId)}
        tabs={TABS.map((t) => ({ id: t.id, label: t.label }))}
      />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "overview" && (
          <OverviewTab
            serverId={current.id}
            server={current}
            onServerUpdated={setServer}
          />
        )}
        {tab === "hardware" && (
          <HardwareTab
            serverId={current.id}
            server={current}
            onServerUpdated={setServer}
          />
        )}
        {tab === "ipmi" && <IpmiTab serverId={current.id} server={current} />}
        {tab === "accounts" && (
          <AccountsTab serverId={current.id} server={current} />
        )}
        {tab === "console" && (
          <ConsoleTab serverId={current.id} server={current} />
        )}
        {tab === "drift" && <DriftTab serverId={current.id} server={current} />}
        {tab === "packages" && (
          <PackagesTab serverId={current.id} server={current} />
        )}
        {tab === "manage" && (
          <ManageTab
            serverId={current.id}
            server={current}
            onServerUpdated={setServer}
            onDeleted={onDeleted}
          />
        )}
      </div>
    </section>
  );
}

function ServerHeader({ server }: { server: Server }) {
  const deptLabel = useDeptLabel(server.department_id);
  const statusKind = STATUS_KIND[server.status];
  const busyKind = BUSY_KIND[server.busy_state];
  const name = server.display_name ?? server.hostname;
  return (
    <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
      <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
        <ServerIcon className="w-7 h-7" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-xl font-semibold truncate">{name}</h1>
          <span
            className={`badge${statusKind ? ` badge-${statusKind}` : ""}`}
          >
            {STATUS_LABEL[server.status]}
          </span>
          <span className={`badge badge-${busyKind}`}>
            {BUSY_LABEL[server.busy_state]}
          </span>
          <span className="text-xs text-dim">
            power: <b>{server.power_state}</b>
          </span>
        </div>
        <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
          <span className="mono">{server.id}</span>
          <span>·</span>
          <span>
            hostname: <b className="mono">{server.hostname}</b>
          </span>
          <span>·</span>
          <span>
            IP: <span className="mono">{server.ip_address}</span>
          </span>
          <span>·</span>
          <span>
            dept: <b>{deptLabel}</b>
          </span>
        </div>
      </div>
    </div>
  );
}
