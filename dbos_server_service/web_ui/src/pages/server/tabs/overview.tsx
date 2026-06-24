/**
 * Overview-вкладка карточки сервера.
 *
 * Read-only сводка по `Server`: имя, департамент, статус/busy/power, сеть,
 * OS-версия, ключевые timestamps. Под edit-кнопкой — inline-форма с PATCH
 * `/servers/{id}` (display_name, description-эквивалент location, IP, ssh_port,
 * os_version_id) для account_admin'а и dep_admin'а своего dept'а.
 *
 * Источник данных: prop `server: Server` (грузит ServerDetail). После PATCH
 * поднимаем свежий объект через `onServerUpdated(next)` — ServerDetail держит
 * карточку в своём state, так что header и соседние вкладки обновляются без
 * перезагрузки страницы.
 */
import { useMemo, useState } from "react";
import { Pencil } from "lucide-react";
import type {
  OsVersion,
  Server,
  ServerUpdateRequest,
} from "@/api/server/types";
import { updateServer } from "@/api/server/servers";
import { listOsVersions } from "@/api/server/osVersions";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useDeptLabel, useUserLabel } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { usePersona } from "@/contexts/PersonaContext";
import { isDepAdmin } from "@/lib/rbac";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";

interface Props {
  serverId: string;
  server?: Server;
  /** Поднимает свежий объект в ServerDetail, чтобы header и соседние
   * вкладки обновились после PATCH без перезагрузки страницы. */
  onServerUpdated?: (next: Server) => void;
}

export function OverviewTab({ server, onServerUpdated }: Props) {
  const [editing, setEditing] = useState(false);
  const view = server;
  const { persona } = usePersona();

  if (!view) {
    return <div className="p-5 text-sm text-dim">Нет данных по серверу.</div>;
  }

  // server:update — есть у dep_admin (своего dept), server.admin и server.operator
  // (по дефолтной матрице прав server_service). account_admin/logging_admin сюда
  // не доходят — Server.tsx отрезает их раньше.
  const canEdit =
    (isDepAdmin(persona) && persona.dept_id === view.department_id) ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  if (editing && canEdit) {
    return (
      <OverviewEditForm
        initial={view}
        onCancel={() => setEditing(false)}
        onSaved={(next) => {
          onServerUpdated?.(next);
          setEditing(false);
        }}
      />
    );
  }

  return (
    <OverviewView
      server={view}
      canEdit={canEdit}
      onEdit={() => setEditing(true)}
    />
  );
}

function OverviewView({
  server,
  canEdit,
  onEdit,
}: {
  server: Server;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const createdByLabel = useUserLabel(server.created_by);
  const busyUserLabel = useUserLabel(server.busy_user_id);
  const name = server.display_name ?? server.hostname;

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-start gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-base">Идентификация</h3>
            <div className="text-xs text-dim">
              Базовые поля карточки сервера.
            </div>
          </div>
          {canEdit && (
            <button
              className="btn btn-ghost flex items-center gap-1"
              onClick={onEdit}
            >
              <Pencil className="w-4 h-4" /> Редактировать
            </button>
          )}
        </div>
        <StatRow k="display_name" v={<span className="mono">{name}</span>} />
        <StatRow k="hostname" v={<span className="mono">{server.hostname}</span>} />
        <StatRow k="id" v={<span className="mono">{server.id}</span>} />
        <StatRow k="department" v={deptLabel} />
        <StatRow
          k="serial_number"
          v={server.serial_number ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="asset_tag"
          v={server.asset_tag ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="location"
          v={server.location ?? <span className="text-dim">—</span>}
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Состояние</h3>
        <StatRow k="status" v={server.status} />
        <StatRow k="power_state" v={server.power_state} />
        <StatRow k="busy_state" v={server.busy_state} />
        <StatRow
          k="busy_user_id"
          v={
            server.busy_user_id ? (
              <span title={server.busy_user_id}>{busyUserLabel}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="busy_since"
          v={
            server.busy_since ? (
              <span className="mono">{formatMsk(server.busy_since)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="busy_note"
          v={server.busy_note ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="is_managed"
          v={server.is_managed ? "да" : "нет"}
        />
        <StatRow
          k="management_user"
          v={
            server.management_user ? (
              <span className="mono">{server.management_user}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Сеть и OS</h3>
        <StatRow
          k="ip_address"
          v={<span className="mono">{server.ip_address}</span>}
        />
        <StatRow
          k="mgmt_ip_address"
          v={
            server.mgmt_ip_address ? (
              <span className="mono">{server.mgmt_ip_address}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow k="ssh_port" v={<span className="mono">{server.ssh_port}</span>} />
        <StatRow
          k="network_interface_name"
          v={
            server.network_interface_name ? (
              <span className="mono">{server.network_interface_name}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="os_version"
          v={
            server.os_version_id ? (
              <OsVersionName osVersionId={server.os_version_id} />
            ) : (
              <span className="text-dim">не задана</span>
            )
          }
        />
        <StatRow
          k="os_last_synced_at"
          v={
            server.os_last_synced_at ? (
              <span className="mono">{formatMsk(server.os_last_synced_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Аудит</h3>
        <StatRow
          k="created_at"
          v={<span className="mono">{formatMsk(server.created_at)}</span>}
        />
        <StatRow
          k="updated_at"
          v={<span className="mono">{formatMsk(server.updated_at)}</span>}
        />
        <StatRow
          k="prepared_at"
          v={
            server.prepared_at ? (
              <span className="mono">{formatMsk(server.prepared_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="decommissioned_at"
          v={
            server.decommissioned_at ? (
              <span className="mono">{formatMsk(server.decommissioned_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="created_by"
          v={
            server.created_by ? (
              <span title={server.created_by}>{createdByLabel}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>
    </div>
  );
}

/** Резолвит `osv_*` в имя из каталога OS-версий; raw id — в title. */
function OsVersionName({ osVersionId }: { osVersionId: string }) {
  const q = useQuery(() => listOsVersions({ limit: 200 }), []);
  const name = useMemo(
    () => q.data?.items.find((v) => v.id === osVersionId)?.name,
    [q.data, osVersionId],
  );
  return (
    <span className="mono" title={osVersionId}>
      {name ?? osVersionId}
    </span>
  );
}

function OverviewEditForm({
  initial,
  onCancel,
  onSaved,
}: {
  initial: Server;
  onCancel: () => void;
  onSaved: (next: Server) => void;
}) {
  const [displayName, setDisplayName] = useState(initial.display_name ?? "");
  const [location, setLocation] = useState(initial.location ?? "");
  const [ip, setIp] = useState(initial.ip_address);
  const [sshPort, setSshPort] = useState(String(initial.ssh_port));
  const [osVersionId, setOsVersionId] = useState(initial.os_version_id ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const osVersions = useQuery(() => listOsVersions({ limit: 200 }), []);
  const osList: OsVersion[] = useMemo(
    () => osVersions.data?.items ?? [],
    [osVersions.data],
  );

  const submit = async () => {
    setErr(null);
    const portNum = Number(sshPort);
    if (!Number.isInteger(portNum) || portNum <= 0 || portNum > 65535) {
      setErr("ssh_port должен быть целым числом 1..65535");
      return;
    }
    const body: ServerUpdateRequest = {
      display_name: displayName.trim() ? displayName.trim() : null,
      location: location.trim() ? location.trim() : null,
      ip_address: ip.trim(),
      ssh_port: portNum,
      os_version_id: osVersionId ? osVersionId : null,
    };
    setPending(true);
    try {
      const next = await updateServer(initial.id, body);
      onSaved(next);
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="p-5">
      <div className="card max-w-2xl">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Pencil className="w-4 h-4 text-accent" /> Редактировать ·{" "}
          <span className="mono">{initial.hostname}</span>
        </h3>
        {err && <div className="alert-danger mb-2">{err}</div>}
        <div className="flex flex-col gap-3">
          <FormRow label="display_name" hint="Пусто = очистить поле">
            <input
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={initial.hostname}
            />
          </FormRow>
          <FormRow label="location" hint="Стойка / DC / комната">
            <input
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />
          </FormRow>
          <FormRow label="ip_address">
            <input
              className="input mono"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
            />
          </FormRow>
          <FormRow label="ssh_port">
            <input
              className="input mono"
              value={sshPort}
              onChange={(e) => setSshPort(e.target.value)}
              inputMode="numeric"
            />
          </FormRow>
          <FormRow
            label="os_version_id"
            hint={
              osVersions.loading
                ? "Загружаем каталог…"
                : "Пусто = сбросить версию"
            }
          >
            <select
              className="input"
              value={osVersionId}
              onChange={(e) => setOsVersionId(e.target.value)}
            >
              <option value="">— не задано —</option>
              {osList.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </FormRow>
        </div>
        <div className="mt-4 flex gap-2 justify-end">
          <button className="btn" onClick={onCancel} disabled={pending}>
            Отмена
          </button>
          <button
            className="btn btn-primary"
            disabled={pending}
            onClick={submit}
          >
            Сохранить
          </button>
        </div>
        <div className="mt-3 text-[11px] text-dim">
          Смена департамента и hardware-поля редактируются отдельно (вкладка
          «Железо» и admin-flow для dept).
        </div>
      </div>
    </div>
  );
}
