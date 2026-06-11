/**
 * Drift-вкладка карточки сервера.
 *
 * Источник — `GET /api/server/v1/servers/{id}/drift`. Один drift-row — это
 * сигнал «то, что мы видим на боксе, расходится с тем, что хранится в БД».
 * `drift_type`:
 *   - `unknown_login`     — на сервере есть OS-юзер, который не привязан к
 *                           `server_account`;
 *   - `attributes`        — у привязанного аккаунта отличаются OS-атрибуты
 *                           (`fields` показывает какие);
 *   - `missing_on_box`    — в БД аккаунт есть, на сервере его не нашли.
 *
 * WARN-level — норма: drift не перетирает БД, owner сам решает чинить или
 * принять текущее состояние через `inventorySync` (выровняет БД по боксу).
 */
import { AlertCircle, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useQuery } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { ApiError } from "@/api/client";
import { getServerDrift, inventorySync } from "@/api/server/servers";
import { listAccounts } from "@/api/server/accounts";
import type {
  CursorPaginatedResponse,
  DriftEventItem,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
} from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
}

const DRIFT_KIND_LABEL: Record<string, string> = {
  unknown_login: "unknown login",
  attributes: "attributes",
  missing_on_box: "missing on box",
};

function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) return `${e.errorCode}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return fallback;
}

function formatDt(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * Аккаунты сервера, видимые текущей persona — тот же грубый client-side
 * фильтр, что в `tabs/console.tsx` / `tabs/manage.tsx`. Backend перепроверит
 * при fetch'е пароля.
 */
function filterAccessibleAccounts(
  accounts: ServerAccount[],
  persona: ReturnType<typeof usePersona>["persona"],
): ServerAccount[] {
  if (persona.platform_role === "account_admin") return accounts;
  if (persona.service_roles.server === "admin") return accounts;
  if (
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "operator" ||
    persona.service_roles.server === "reader"
  ) {
    if (!persona.dept_id) return [];
    return accounts.filter((a) => a.department_id === persona.dept_id);
  }
  return [];
}

export function DriftTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const driftQ = useQuery(() => getServerDrift(serverId), [serverId]);
  const [syncing, setSyncing] = useState(false);

  const canSync =
    persona.platform_role === "account_admin" ||
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  // На неуправляемом сервере inventory-sync ходит по SSH под аккаунтом —
  // подгружаем привязанные аккаунты для picker'а. Managed → по ключу, picker
  // не нужен (backend сам None'ит account_id).
  const needsAccount = !!server && !server.is_managed && canSync;
  const accountsQ = useQuery(
    () => listAccounts({ server_id: serverId, limit: 200 }),
    [serverId],
    { enabled: needsAccount },
  );
  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    return data?.items ?? [];
  }, [accountsQ.data]);
  const accessibleAccounts = useMemo(
    () => filterAccessibleAccounts(accounts, persona),
    [accounts, persona],
  );
  const [accountId, setAccountId] = useState<string>("");

  async function handleSync() {
    if (syncing) return;
    setSyncing(true);
    try {
      const res = await inventorySync(serverId, {
        account_id: needsAccount && accountId ? accountId : undefined,
      });
      toast.success(`Inventory sync запущен (task ${res.task_id})`);
      // Backend пишет результат через worker callback; refetch покажет drift
      // как только воркер закроет таску и пересоберёт сводку.
      driftQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Sync не запустился"));
    } finally {
      setSyncing(false);
    }
  }

  const drifts = driftQ.data?.drifts ?? [];

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <div className="text-sm font-medium mb-1">Drift</div>
          <div className="text-xs text-dim">
            Расхождения между БД и тем, что найдено инвентаризацией бокса.
            {driftQ.data?.since && (
              <>
                {" "}
                Окно с <span className="mono">{formatDt(driftQ.data.since)}</span>.
              </>
            )}
            {driftQ.data?.truncated && (
              <>
                {" "}
                <span className="text-warn">список усечён</span>.
              </>
            )}
          </div>
        </div>
        {canSync && (
          <div className="flex items-center gap-2 flex-wrap">
            {needsAccount && (
              <select
                className="surface-2 border border-token rounded px-2 py-1 text-sm"
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                disabled={syncing || accountsQ.loading}
                title="SSH-аккаунт для inventory-sync"
              >
                <option value="">— дефолтный аккаунт —</option>
                {accessibleAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.login}
                    {a.has_sudo ? " (sudo)" : ""}
                  </option>
                ))}
              </select>
            )}
            <button
              className="btn btn-primary flex items-center gap-2"
              onClick={handleSync}
              disabled={syncing}
              title="Запустить inventory-sync через worker"
            >
              <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
              {syncing ? "Запускаем…" : "Sync drift"}
            </button>
          </div>
        )}
      </div>

      {driftQ.loading && (
        <div className="text-xs text-dim">Загружаем drift…</div>
      )}

      {driftQ.error && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(driftQ.error, "Drift не загрузился")}</div>
            <button
              className="btn btn-ghost mt-2"
              onClick={() => driftQ.refetch()}
            >
              Повторить
            </button>
          </div>
        </div>
      )}

      {!driftQ.loading && !driftQ.error && drifts.length === 0 && (
        <div className="text-xs text-dim">
          Расхождений не найдено — БД совпадает с тем, что вернула последняя
          инвентаризация.
        </div>
      )}

      {drifts.length > 0 && (
        <div className="surface-2 border border-token rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase text-dim border-b border-token">
                <th className="text-left px-3 py-2 font-medium">login</th>
                <th className="text-left px-3 py-2 font-medium">тип</th>
                <th className="text-left px-3 py-2 font-medium">поля</th>
                <th className="text-left px-3 py-2 font-medium">когда</th>
                <th className="text-left px-3 py-2 font-medium">статус</th>
              </tr>
            </thead>
            <tbody>
              {drifts.map((d, idx) => (
                <DriftRow key={`${d.login}-${d.drift_type}-${idx}`} row={d} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DriftRow({ row }: { row: DriftEventItem }) {
  const label = DRIFT_KIND_LABEL[row.drift_type] ?? row.drift_type;
  const fields = row.fields ?? [];
  return (
    <tr className="border-b border-token last:border-b-0 align-top">
      <td className="px-3 py-2 mono text-xs">{row.login}</td>
      <td className="px-3 py-2 text-xs">{label}</td>
      <td className="px-3 py-2 text-xs">
        {fields.length === 0 ? (
          <span className="text-dim">—</span>
        ) : (
          <span className="mono text-[11px]">{fields.join(", ")}</span>
        )}
      </td>
      <td className="px-3 py-2 text-xs text-dim mono">
        {formatDt(row.detected_at)}
      </td>
      <td className="px-3 py-2">
        <span className="badge badge-warn">mismatch</span>
      </td>
    </tr>
  );
}
