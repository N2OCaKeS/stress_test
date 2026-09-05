import { useState, type ReactNode } from "react";
import {
  Search,
  User,
  Users,
  GitBranch,
  Key,
  Database,
  Github,
  Cloud,
  Server as ServerIcon,
  Lock,
  Webhook,
  KeyRound,
  Plus,
  Edit3,
  RotateCw,
  Trash2,
  Eye,
  Copy,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Dropdown } from "@/components/ui/Dropdown";

interface CredRow {
  id: string;
  name: string;
  kindLabel: string;
  tail: string;
  badge: string;
  badgeKind?: "ok" | "warn" | "danger" | "";
  icon: typeof Key;
  iconClass?: string;
}

const MY: CredRow[] = [
  { id: "alice-personal-vault", name: "alice-personal-vault", kindLabel: "api-token", tail: "создан 12 дн назад", badge: "active", badgeKind: "ok", icon: Key, iconClass: "text-warn" },
  { id: "alice-ssh-bastion", name: "alice-ssh-bastion", kindLabel: "ssh-key", tail: "3 мес назад", badge: "active", badgeKind: "ok", icon: Key },
];

const MY_DEP: CredRow[] = [
  { id: "prod-postgres-master", name: "prod-postgres-master", kindLabel: "db-password", tail: "последний показ — bob, 2 ч назад", badge: "истекает через 3д", badgeKind: "warn", icon: Database },
  { id: "github-deploy-token", name: "github-deploy-token", kindLabel: "api-token", tail: "bot: ci_runner", badge: "active", badgeKind: "ok", icon: Github },
  { id: "aws-readonly-monitoring", name: "aws-readonly-monitoring", kindLabel: "aws-key", tail: "год назад", badge: "active", badgeKind: "ok", icon: Cloud },
  { id: "bmc-rack-A-ipmi", name: "bmc-rack-A-ipmi", kindLabel: "ipmi-pass", tail: "общий с server_service", badge: "active", badgeKind: "ok", icon: ServerIcon },
  { id: "grafana-admin", name: "grafana-admin", kindLabel: "password", tail: "2 нед назад", badge: "must_rotate", badgeKind: "warn", icon: Lock },
  { id: "slack-bot-webhook", name: "slack-bot-webhook", kindLabel: "webhook", tail: "год назад", badge: "active", badgeKind: "ok", icon: Webhook },
  { id: "vault-bootstrap-root", name: "vault-bootstrap-root", kindLabel: "root-token", tail: "bot: bootstrap", badge: "expired", badgeKind: "danger", icon: KeyRound },
];

interface CrossRow {
  id: string;
  name: string;
  dept: string;
  note: string;
  noteClass?: string;
  badge: string;
  badgeKind: "ok" | "warn" | "danger";
  icon: typeof Key;
}

const CROSS: CrossRow[] = [
  { id: "dev-jira-api", name: "dev-jira-api", dept: "Разработка", note: "только чтение", noteClass: "text-warn", badge: "active", badgeKind: "ok", icon: Key },
  { id: "shared-monitoring-readonly", name: "shared-monitoring-readonly", dept: "Инфра", note: "общий", badge: "active", badgeKind: "ok", icon: Database },
  { id: "guest-network-wifi", name: "guest-network-wifi", dept: "Гость", note: "все", badge: "active", badgeKind: "ok", icon: Key },
];

export function SecretDepAdmin() {
  const [selected, setSelected] = useState<string>("alice-personal-vault");
  const [grouping, setGrouping] = useState("scope");

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
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim">
            <span>Группировка:</span>
            <Dropdown
              mode="single"
              options={[
                { value: "scope", label: "область (my / my_dep / cross_dep)" },
                { value: "kind", label: "тип" },
                { value: "owner", label: "владелец" },
              ]}
              value={grouping}
              onChange={setGrouping}
            />
            <span className="ml-auto">12 шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <User className="w-3 h-3" /> my · 2
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {MY.map((row) => (
              <CredRowButton
                key={row.id}
                row={row}
                active={selected === row.id}
                onClick={() => setSelected(row.id)}
              />
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Users className="w-3 h-3" /> my_dep · Ядро DBOS · 7
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {MY_DEP.map((row) => (
              <CredRowButton
                key={row.id}
                row={row}
                active={selected === row.id}
                onClick={() => setSelected(row.id)}
              />
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <GitBranch className="w-3 h-3" /> cross_dep (выдано по ACL) · 3
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {CROSS.map((row) => {
              const Icon = row.icon;
              const active = selected === row.id;
              return (
                <button
                  key={row.id}
                  onClick={() => setSelected(row.id)}
                  className={`cred-row text-left ${active ? "active" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <Icon className="w-4 h-4 text-dim" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{row.name}</div>
                      <div className="text-[11px] text-dim flex items-center gap-2">
                        <span>
                          отдел: <b>{row.dept}</b>
                        </span>
                        <span>·</span>
                        <span className={row.noteClass}>{row.note}</span>
                      </div>
                    </div>
                    <span className={`badge badge-${row.badgeKind}`}>
                      {row.badge}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Plus className="w-4 h-4" /> Создать учётные данные
          </button>
        </div>
      </section>

      <SecretDetailPanel
        title="alice-personal-vault"
        statusBadge={<span className="badge badge-ok">active</span>}
        scope="my"
        scopePrefix=""
        idStr="cred_e8f2c901a3b54c7e"
        kind="api-token"
        ownerLine="создан 12 дн назад alice@dbos.local"
        revealValue="atk_e8f2c901a3b54c7e_abc12...DEMO"
        revealNote={
          <>
            При показе эмитится событие аудита{" "}
            <span className="mono">credential.read</span> с request_id и actor_id.
          </>
        }
        meta={{
          dept: "Ядро DBOS",
          owner: "alice",
          ownerId: "usr_a1b2c3...",
          created: "2026-05-28 14:32 UTC",
          validFrom: "—",
          validTo: "—",
        }}
        accessMatrix={{
          scopeBadge: "personal (my)",
          ownerRole: "read + write + revoke",
          deptAdmins: "— (личная креда)",
          explicitGrants: "нет",
          bots: "нет",
        }}
        auditRows={[
          { ts: "15:42:11", action: "credential.read", actionClass: "text-ok", actor: "alice", req: "req_7e9f..." },
          { ts: "11:18:05", action: "credential.read", actionClass: "text-ok", actor: "alice", req: "req_4ab1..." },
          { ts: "09:02:54", action: "credential.read · 429", actionClass: "text-warn", actor: "alice", req: "req_22c8..." },
          { ts: "2 дн", action: "credential.update", actionClass: "text-accent", actor: "alice", req: "req_aa10..." },
          { ts: "12 дн", action: "credential.create", actionClass: "text-accent", actor: "alice", req: "req_001f..." },
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

function CredRowButton({
  row,
  active,
  onClick,
}: {
  row: CredRow;
  active: boolean;
  onClick: () => void;
}) {
  const Icon = row.icon;
  const iconClass = row.iconClass ?? (active ? "text-accent" : "text-dim");
  return (
    <button
      onClick={onClick}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${iconClass}`} />
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
}

export interface SecretDetailPanelProps {
  title: string;
  statusBadge: ReactNode;
  scope: string;
  scopePrefix?: string;
  idStr: string;
  kind: string;
  ownerLine: ReactNode;
  revealValue: string;
  revealNote: ReactNode;
  meta: {
    dept: string;
    owner: string;
    ownerId: string;
    created: string;
    encryptVersion?: string;
    validFrom?: string;
    validTo?: ReactNode;
    extraRow?: ReactNode;
  };
  accessMatrix: {
    scopeBadge: string;
    ownerRole?: string;
    deptAdmins?: ReactNode;
    explicitGrants?: ReactNode;
    bots?: ReactNode;
    accountAdmin?: ReactNode;
    lastGrant?: ReactNode;
    serverBinding?: ReactNode;
    sectionTitle?: string;
    primaryButton?: ReactNode;
  };
  auditRows: {
    ts: string;
    action: string;
    actionClass: string;
    actor: ReactNode;
    req: string;
  }[];
  actions: ReactNode;
  auditSubtitle?: string;
  rightHeaderExtra?: ReactNode;
}

export function SecretDetailPanel({
  title,
  statusBadge,
  scope,
  scopePrefix,
  idStr,
  kind,
  ownerLine,
  revealValue,
  revealNote,
  meta,
  accessMatrix,
  auditRows,
  actions,
  auditSubtitle,
  rightHeaderExtra,
}: SecretDetailPanelProps) {
  const [revealed, setRevealed] = useState(false);

  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center text-2xl">
          🔑
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{title}</h1>
            {statusBadge}
            <span className="text-xs text-dim">
              {scopePrefix}область: <b>{scope}</b>
              {rightHeaderExtra}
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{idStr}</span>
            <span>·</span>
            <span>
              тип: <b>{kind}</b>
            </span>
            <span>·</span>
            <span>{ownerLine}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">{actions}</div>
      </div>

      <div className="border-b border-token px-5 flex gap-1">
        <button
          className="px-3 py-2 text-sm border-b-2 -mb-px"
          style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
        >
          Обзор
        </button>
        {["Матрица доступа", "Журнал аудита", "Срок действия"].map((t) => (
          <button
            key={t}
            className="px-3 py-2 text-sm border-b-2 -mb-px border-transparent text-dim hover-bg"
          >
            {t}
          </button>
        ))}
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        {/* Secret value */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wider text-dim">
              Значение
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setRevealed((v) => !v)}
                className="btn"
              >
                <Eye className="w-4 h-4 inline-block" />{" "}
                <span>{revealed ? "Скрыть" : "Показать"}</span>
              </button>
              <button className="btn">
                <Copy className="w-4 h-4 inline-block" />
              </button>
            </div>
          </div>
          <div
            className={`mono text-lg p-3 surface-2 rounded border border-token ${
              revealed ? "" : "secret-mask"
            }`}
          >
            {revealed ? revealValue : "••••••••••••••••••••••••••"}
          </div>
          <div className="text-xs text-dim mt-2">{revealNote}</div>
        </div>

        {/* Meta */}
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            Метаданные
          </div>
          <div className="text-sm">
            <div className="stat-row">
              <span className="text-dim">Отдел</span>
              <span>{meta.dept}</span>
            </div>
            <div className="stat-row">
              <span className="text-dim">Владелец</span>
              <span>
                {meta.owner}{" "}
                <span className="text-dim">({meta.ownerId})</span>
              </span>
            </div>
            <div className="stat-row">
              <span className="text-dim">Создан</span>
              <span>{meta.created}</span>
            </div>
            <div className="stat-row">
              <span className="text-dim">Версия шифрования</span>
              <span className="mono">
                {meta.encryptVersion ?? "v3 · HKDF-SHA256 · AES-256-GCM"}
              </span>
            </div>
            {meta.extraRow}
            <div className="stat-row">
              <span className="text-dim">valid_from</span>
              <span>{meta.validFrom ?? "—"}</span>
            </div>
            <div className="stat-row">
              <span className="text-dim">valid_to</span>
              <span>{meta.validTo ?? "—"}</span>
            </div>
          </div>
        </div>

        {/* Access matrix preview */}
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            {accessMatrix.sectionTitle ?? "Кто видит"}
          </div>
          <div className="text-sm">
            <div className="stat-row">
              <span className="text-dim">Область</span>
              <span className="badge">{accessMatrix.scopeBadge}</span>
            </div>
            {accessMatrix.ownerRole && (
              <div className="stat-row">
                <span className="text-dim">Роль владельца</span>
                <span>{accessMatrix.ownerRole}</span>
              </div>
            )}
            {accessMatrix.deptAdmins !== undefined && (
              <div className="stat-row">
                <span className="text-dim">Админы отдела</span>
                <span>{accessMatrix.deptAdmins}</span>
              </div>
            )}
            {accessMatrix.explicitGrants !== undefined && (
              <div className="stat-row">
                <span className="text-dim">Явные выдачи</span>
                <span>{accessMatrix.explicitGrants}</span>
              </div>
            )}
            {accessMatrix.bots !== undefined && (
              <div className="stat-row">
                <span className="text-dim">Боты с доступом</span>
                <span>{accessMatrix.bots}</span>
              </div>
            )}
            {accessMatrix.accountAdmin !== undefined && (
              <div className="stat-row">
                <span className="text-dim">account_admin</span>
                <span>{accessMatrix.accountAdmin}</span>
              </div>
            )}
            {accessMatrix.lastGrant !== undefined && (
              <div className="stat-row">
                <span className="text-dim">Последняя выдача</span>
                <span>{accessMatrix.lastGrant}</span>
              </div>
            )}
            {accessMatrix.serverBinding !== undefined && (
              <div className="stat-row">
                <span className="text-dim">Привязка к серверу</span>
                <span>{accessMatrix.serverBinding}</span>
              </div>
            )}
          </div>
          {accessMatrix.primaryButton ?? (
            <button className="btn mt-3 w-full">
              Открыть вкладку «Матрица доступа»
            </button>
          )}
        </div>

        {/* Audit preview */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs uppercase tracking-wider text-dim">
              {auditSubtitle ?? "Последние 5 событий"}
            </div>
            <button className="text-xs text-accent">
              Открыть полный журнал аудита →
            </button>
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">Время</th>
                <th className="pb-2 pr-3">Действие</th>
                <th className="pb-2 pr-3">Инициатор</th>
                <th className="pb-2">Запрос</th>
              </tr>
            </thead>
            <tbody className="text-sm">
              {auditRows.map((r) => (
                <tr key={r.req} className="border-t border-token">
                  <td className="py-2 mono">{r.ts}</td>
                  <td className={r.actionClass}>{r.action}</td>
                  <td>{r.actor}</td>
                  <td className="mono text-dim">{r.req}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
