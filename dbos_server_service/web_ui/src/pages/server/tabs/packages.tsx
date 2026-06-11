/**
 * Packages-вкладка карточки сервера.
 *
 * Probe запускает live SSH-задачу через worker. Ответ endpoint'а — только
 * `{task_id, status}` (HTTP 202), сам список пакетов уезжает в `task.result`
 * и опрашивается отдельным запросом. Wrapper'а для `GET /tasks/{id}` в
 * `@/api/server/*` пока нет, поэтому здесь показываем ack от dispatch'а и
 * напоминаем, где смотреть результат. Когда `getTask` подъедет — табличка
 * `name / version / arch` рендерится тут же, скелет уже стоит.
 *
 * RBAC: probe доступен `server.operator`+ и dep_admin'у своего dept'а;
 * `account_admin` — везде. Backend перепроверит ещё раз — клиентский
 * gate только прячет заведомо лишнюю кнопку.
 */
import { useState } from "react";
import { Package, RefreshCw, AlertCircle } from "lucide-react";
import { installedPackagesProbe } from "@/api/server/misc";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { isDepAdmin, isPlatformWideAdmin } from "@/lib/rbac";
import type { Server } from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
}

interface PackageRow {
  name: string;
  version: string;
  arch?: string | null;
}

function canProbe(
  persona: ReturnType<typeof usePersona>["persona"],
  server: Server | undefined,
): boolean {
  if (isPlatformWideAdmin(persona)) return true;
  if (persona.service_roles.server === "admin") return true;
  if (persona.service_roles.server === "operator") return true;
  if (
    isDepAdmin(persona) &&
    server &&
    persona.dept_id === server.department_id
  ) {
    return true;
  }
  return false;
}

export function PackagesTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [pattern, setPattern] = useState("");
  const [pending, setPending] = useState(false);
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);
  const [lastStatus, setLastStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Список здесь пока не заполняется — нужен `GET /tasks/{id}` wrapper.
  // Скелет под `{packages: [{name, version, arch?}]}` оставлен, чтобы дорисовать
  // одной строкой, когда роут появится.
  const [packages] = useState<PackageRow[]>([]);

  const allowed = canProbe(persona, server);

  async function handleProbe() {
    if (pending || !allowed) return;
    setPending(true);
    setErr(null);
    try {
      const res = await installedPackagesProbe(
        serverId,
        pattern.trim() ? { pattern: pattern.trim() } : undefined,
      );
      setLastTaskId(res.task_id);
      setLastStatus(res.status);
      toast.success(`Probe запущен (task ${res.task_id})`);
    } catch (e) {
      const msg = apiErrMsg(e, "Probe не запустился");
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            <div className="text-sm font-medium mb-1 flex items-center gap-2">
              <Package className="w-4 h-4 text-accent" />
              Установленные пакеты
            </div>
            <div className="text-xs text-dim">
              Live SSH-probe через worker: `dpkg-query` / `rpm -qa` по
              shell-glob'у. Сама проба обычно идёт <b>десятки секунд</b> —
              endpoint отдаёт <span className="mono">task_id</span> сразу,
              реальный список появится в результатах задачи.
            </div>
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-2"
              onClick={handleProbe}
              disabled={pending}
              title="Запустить probe установленных пакетов"
            >
              <RefreshCw
                className={`w-4 h-4 ${pending ? "animate-spin" : ""}`}
              />
              {pending ? "Запускаем…" : "Probe installed packages"}
            </button>
          )}
        </div>

        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <label className="text-xs text-dim">pattern (shell glob)</label>
          <input
            className="input mono text-xs"
            style={{ minWidth: 220 }}
            placeholder="* / linux-image* / *-dev"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            disabled={pending || !allowed}
          />
          <span className="text-[11px] text-dim">
            пусто → `*` (все пакеты)
          </span>
        </div>

        {!allowed && (
          <div className="mt-3 text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или
            dep_admin своего департамента).
          </div>
        )}
      </div>

      {err && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">{err}</div>
        </div>
      )}

      {lastTaskId && (
        <div className="surface-2 border border-token rounded p-3 text-xs flex flex-col gap-1">
          <div>
            Последний probe:{" "}
            <span className="mono">{lastTaskId}</span>
            {lastStatus && (
              <>
                {" · "}статус{" "}
                <span className="badge badge-warn">{lastStatus}</span>
              </>
            )}
          </div>
          <div className="text-dim">
            Результат (`{`{packages: [{name, version, arch?}, ...]}`}`)
            читается из <span className="mono">task.result</span> — отдельным
            запросом к task-роуту. UI-просмотр результата подъедет после
            появления `GET /tasks/{`{id}`}` wrapper'а.
          </div>
        </div>
      )}

      <div className="surface-2 border border-token rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] uppercase text-dim border-b border-token">
              <th className="text-left px-3 py-2 font-medium">name</th>
              <th className="text-left px-3 py-2 font-medium">version</th>
              <th className="text-left px-3 py-2 font-medium">arch</th>
            </tr>
          </thead>
          <tbody>
            {packages.length === 0 ? (
              <tr>
                <td
                  colSpan={3}
                  className="px-3 py-4 text-xs text-dim text-center"
                >
                  Список пуст. Запусти probe и подожди, пока worker закроет
                  задачу.
                </td>
              </tr>
            ) : (
              packages.map((p) => (
                <tr
                  key={`${p.name}-${p.version}-${p.arch ?? ""}`}
                  className="border-b border-token last:border-b-0"
                >
                  <td className="px-3 py-1.5 mono text-xs">{p.name}</td>
                  <td className="px-3 py-1.5 mono text-xs">{p.version}</td>
                  <td className="px-3 py-1.5 mono text-xs text-dim">
                    {p.arch ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
