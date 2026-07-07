import { useState } from "react";
import {
  Search,
  Filter,
  Building2,
  Database,
  Github,
  KeyRound,
  Server as ServerIcon,
  Lock,
  Webhook,
  Cloud,
  Key,
  Plus,
  Edit3,
  RotateCw,
  Trash2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { SecretDetailPanel } from "./SecretDepAdmin";

interface Row {
  id: string;
  name: string;
  kindLabel: string;
  tail: string;
  badge: string;
  badgeKind?: "ok" | "warn" | "danger" | "";
  icon: typeof Key;
}

const CORE: Row[] = [
  { id: "core-postgres-replica", name: "core-postgres-replica", kindLabel: "db-password", tail: "владелец: alice", badge: "active", badgeKind: "ok", icon: Database },
  { id: "github-deploy-token", name: "github-deploy-token", kindLabel: "api-token", tail: "bot: ci_runner", badge: "active", badgeKind: "ok", icon: Github },
  { id: "vault-bootstrap-root", name: "vault-bootstrap-root", kindLabel: "root-token", tail: "bot: bootstrap", badge: "expired", badgeKind: "danger", icon: KeyRound },
  { id: "bmc-rack-A-ipmi", name: "bmc-rack-A-ipmi", kindLabel: "ipmi-pass", tail: "общий", badge: "active", badgeKind: "ok", icon: ServerIcon },
  { id: "grafana-admin", name: "grafana-admin", kindLabel: "password", tail: "2 нед назад", badge: "must_rotate", badgeKind: "warn", icon: Lock },
];

const DTKK: Row[] = [
  { id: "prod-postgres-master", name: "prod-postgres-master", kindLabel: "db-password", tail: "владелец: carol", badge: "истекает через 3д", badgeKind: "warn", icon: Database },
  { id: "dtkk-jira-api", name: "dtkk-jira-api", kindLabel: "api-token", tail: "владелец: igor", badge: "active", badgeKind: "ok", icon: Key },
  { id: "dtkk-pagerduty-webhook", name: "dtkk-pagerduty-webhook", kindLabel: "webhook", tail: "2 мес назад", badge: "active", badgeKind: "ok", icon: Webhook },
  { id: "dtkk-s3-backups", name: "dtkk-s3-backups", kindLabel: "aws-key", tail: "общий", badge: "active", badgeKind: "ok", icon: Cloud },
];

const INFRA: Row[] = [
  { id: "aws-readonly-monitoring", name: "aws-readonly-monitoring", kindLabel: "aws-key", tail: "владелец: pavel", badge: "active", badgeKind: "ok", icon: Cloud },
  { id: "shared-monitoring-readonly", name: "shared-monitoring-readonly", kindLabel: "db-password", tail: "общий", badge: "active", badgeKind: "ok", icon: Database },
  { id: "infra-bmc-rack-B", name: "infra-bmc-rack-B", kindLabel: "ipmi-pass", tail: "владелец: pavel", badge: "must_rotate", badgeKind: "warn", icon: ServerIcon },
];

const GUEST: Row[] = [
  { id: "guest-network-wifi", name: "guest-network-wifi", kindLabel: "password", tail: "все", badge: "active", badgeKind: "ok", icon: Key },
  { id: "guest-printer-pin", name: "guest-printer-pin", kindLabel: "pin", tail: "год назад", badge: "active", badgeKind: "ok", icon: Key },
];

export function SecretAccountAdmin() {
  const [selected, setSelected] = useState<string>("prod-postgres-master");

  const renderGroup = (rows: Row[]) =>
    rows.map((row) => {
      const Icon = row.icon;
      const active = selected === row.id;
      return (
        <button
          key={row.id}
          onClick={() => setSelected(row.id)}
          className={`cred-row text-left ${active ? "active" : ""}`}
        >
          <div className="flex items-center gap-2">
            <Icon
              className={`w-4 h-4 ${active ? "text-warn" : "text-dim"}`}
            />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{row.name}</div>
              <div className="text-[11px] text-dim flex items-center gap-2">
                <span className="mono">{row.kindLabel}</span>
                <span>·</span>
                <span>{row.tail}</span>
              </div>
            </div>
            <span
              className={`badge${row.badgeKind ? ` badge-${row.badgeKind}` : ""}`}
            >
              {row.badge}
            </span>
          </div>
        </button>
      );
    });

  return (
    <Shell breadcrumb="secret_service / credentials">
      <section className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск учётных данных..."
            />
            <button className="btn" title="Фильтр">
              <Filter className="w-3 h-3 inline-block" />
            </button>
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim">
            <span>Группировка:</span>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>по отделам</option>
              <option>область (my / my_dep / cross_dep)</option>
              <option>тип</option>
              <option>владелец</option>
            </select>
            <span className="ml-auto">70 шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <Building2 className="w-3 h-3" /> Ядро DBOS · 24
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(CORE)}</div>
          <div className="cred-row text-dim text-xs px-3 py-2">
            + 19 ещё в Ядро DBOS · нажмите чтобы развернуть
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> ДТКК · 28
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(DTKK)}</div>
          <div className="cred-row text-dim text-xs px-3 py-2">
            + 24 ещё в ДТКК · нажмите чтобы развернуть
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> Инфра · 16
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(INFRA)}</div>
          <div className="cred-row text-dim text-xs px-3 py-2">
            + 13 ещё в Инфра · нажмите чтобы развернуть
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> Гость · 2
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(GUEST)}</div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Plus className="w-4 h-4" /> Создать учётные данные
          </button>
        </div>
      </section>

      <SecretDetailPanel
        title="prod-postgres-master"
        statusBadge={
          <span className="badge badge-warn">истекает через 3д</span>
        }
        scope="ДТКК"
        scopePrefix="отдел: "
        idStr="cred_a42f1c8b9e02d144"
        kind="db-password"
        ownerLine="owner: carol@dbos.local"
        revealValue="pg_prod_x9F2k1L0wQ8m...DEMO"
        revealNote={
          <>
            account_admin может смотреть любые учётные данные любого отдела, но
            показ эмитится в аудит с пометкой{" "}
            <span className="mono">actor_role=account_admin</span>.
          </>
        }
        meta={{
          dept: "ДТКК",
          owner: "carol",
          ownerId: "usr_c3d4e5...",
          created: "2026-03-14 09:11 UTC",
          validFrom: "2026-03-14",
          validTo: <span className="text-warn">2026-06-13 (3д)</span>,
        }}
        accessMatrix={{
          scopeBadge: "department (my_dep)",
          deptAdmins: "carol, igor (ДТКК)",
          explicitGrants: "alice (Ядро DBOS, ro)",
          bots: "prod_db_migrator (ДТКК)",
          accountAdmin: (
            <span className="text-warn">только чтение аудита (вы)</span>
          ),
        }}
        auditRows={[
          { ts: "16:04:22", action: "credential.read", actionClass: "text-ok", actor: "carol", req: "req_8a1f..." },
          { ts: "14:18:50", action: "credential.read", actionClass: "text-ok", actor: "bot:prod_db_migrator", req: "req_b22e..." },
          { ts: "10:02:11", action: "credential.read · 429", actionClass: "text-warn", actor: "igor", req: "req_71cd..." },
          { ts: "1 дн", action: "credential.grant", actionClass: "text-accent", actor: "carol → alice", req: "req_4f33..." },
          { ts: "86 дн", action: "credential.create", actionClass: "text-accent", actor: "carol", req: "req_001f..." },
        ]}
        actions={
          <>
            <button className="btn">
              <Edit3 className="w-4 h-4 inline-block" /> Изменить
            </button>
            <button className="btn">
              <RotateCw className="w-4 h-4 inline-block" /> Ротация
            </button>
            <button className="btn btn-danger">
              <Trash2 className="w-4 h-4 inline-block" /> Удалить
            </button>
          </>
        }
      />
    </Shell>
  );
}
