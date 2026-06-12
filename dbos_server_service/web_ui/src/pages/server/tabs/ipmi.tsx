/**
 * IPMI-вкладка карточки сервера.
 *
 * Покрывает три состояния:
 *   1. У сервера нет IPMI-контроллера (GET → 404 NO_IPMI_CONTROLLER) — форма
 *      регистрации (host/port разнесены, kind, username, password).
 *   2. Контроллер есть — карточка metadata + блок power + блок credentials +
 *      edit/delete.
 *   3. Loading / network error c retry.
 *
 * Power-операции возвращают 202 + task_id; здесь это отображается как toast,
 * результат уезжает в `/tasks/{id}` и не блокирует UI. PowerStatus читается
 * с auto-refresh раз в 10 сек.
 *
 * Rotate IPMI дёргает `POST /ipmi-controllers/{id}/rotate` (202 + task_id):
 * worker генерит новый пароль, применяет на BMC, verify'ит read-only вызовом
 * и только потом server_service шифрует/сохраняет ciphertext. Результат уезжает
 * в `/tasks/{id}`; метаданные credentials подтягиваются после callback'а.
 *
 * RBAC: power-операции — server.operator/admin или dep_admin (своего dept);
 * edit/delete/rotate — server.admin или dep_admin (своего dept). account_admin /
 * logging_admin закрыты от server_service целиком — страница /server для них не
 * рендерится. Источник флагов — `usePersona()` + `persona.service_roles`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Edit3,
  KeyRound,
  Power,
  RefreshCw,
  Trash2,
  Zap,
} from "lucide-react";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  deleteIpmi,
  dispatchPowerStatus,
  getIpmi,
  getIpmiCredentials,
  getPowerStatus,
  powerOff,
  powerOn,
  powerReboot,
  registerIpmi,
  rotateIpmi,
  updateIpmi,
} from "@/api/server/ipmi";
import type {
  IpmiController,
  IpmiCreateRequest,
  IpmiCredentials,
  IpmiKind,
  IpmiUpdateRequest,
  PowerStatus,
  Server,
} from "@/api/server/types";
import { usePersona } from "@/contexts/PersonaContext";
import type { Persona } from "@/types/persona";
import { useToast } from "@/contexts/ToastContext";
import { formatMskShort } from "@/lib/datetime";

interface Props {
  serverId: string;
  server?: Server;
}

interface IpmiCaps {
  /** Power on/off/reboot/status. */
  power: boolean;
  /** Edit metadata / delete / rotate password / register controller. */
  admin: boolean;
  /** Reason if both flags are false (для tooltip кнопок). */
  reason: string;
}

function ipmiCaps(persona: Persona, serverDeptId: string | null): IpmiCaps {
  const platform = persona.platform_role;
  const svc = persona.service_roles.server;

  // account_admin / logging_admin отрезаны от server_service на уровне backend —
  // сюда не доходят (Server.tsx закрывает им весь раздел). logging_reader может
  // быть обычным сотрудником с server-ролью, поэтому его права считаем по svc.
  if (platform === "logging_reader" && !svc) {
    return { power: false, admin: false, reason: "logging-роль — только просмотр" };
  }
  // dep_admin своего dept'а — фактически admin для платформенных серверов dept'а.
  if (platform === "dep_admin" && serverDeptId && persona.dept_id === serverDeptId) {
    return { power: true, admin: true, reason: "" };
  }
  if (svc === "admin") {
    return { power: true, admin: true, reason: "" };
  }
  if (svc === "operator") {
    return { power: true, admin: false, reason: "operator не правит/удаляет IPMI" };
  }
  return {
    power: false,
    admin: false,
    reason: "нужна роль server.operator+ для power и server.admin+ для edit/rotate",
  };
}

const fmtTs = formatMskShort;

const KIND_LABEL: Record<IpmiKind, string> = {
  idrac: "iDRAC",
  ilo: "iLO",
  ipmi: "ipmitool",
  redfish: "Redfish",
};

const POWER_REFRESH_MS = 10_000;

// ---------------------------------------------------------------------------
// Главный компонент
// ---------------------------------------------------------------------------

export function IpmiTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const caps = useMemo(
    () => ipmiCaps(persona, server?.department_id ?? null),
    [persona, server?.department_id],
  );

  const [controller, setController] = useState<IpmiController | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | Error | null>(null);
  const [missing, setMissing] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  // GET /ipmi: 404 NO_IPMI_CONTROLLER — нормальная развилка, рендерим форму
  // регистрации; 403/5xx — ошибка с retry.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setMissing(false);
    getIpmi(serverId)
      .then((c) => {
        if (!cancelled) {
          setController(c);
          setMissing(false);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setController(null);
          setMissing(true);
        } else if (e instanceof ApiError || e instanceof Error) {
          setLoadError(e);
        } else {
          setLoadError(new Error(String(e)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [serverId, reloadTick]);

  const refetch = useCallback(() => setReloadTick((n) => n + 1), []);

  if (loading) {
    return (
      <div className="p-5 text-sm text-dim">Загружаем IPMI-контроллер…</div>
    );
  }
  if (loadError) {
    return (
      <div className="p-5">
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-sm">
            <div>{apiErrMsg(loadError, "Не удалось получить IPMI")}</div>
            <button className="btn btn-ghost mt-2" onClick={refetch}>
              Повторить
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (missing) {
    return (
      <RegisterPane
        serverId={serverId}
        canRegister={caps.admin}
        denyReason={caps.reason}
        onRegistered={refetch}
      />
    );
  }

  if (!controller) {
    // Защита от race: missing=false и controller=null одновременно быть не должно.
    return <div className="p-5 text-sm text-dim">IPMI: нет данных.</div>;
  }

  return (
    <ControllerPane
      controller={controller}
      caps={caps}
      onChanged={refetch}
      onDeleted={refetch}
    />
  );
}

// ---------------------------------------------------------------------------
// Регистрация
// ---------------------------------------------------------------------------

function defaultEndpointFor(kind: IpmiKind, host: string, port: string): string {
  // Для redfish/idrac/ilo принято https://. Для ipmitool транспорт — RMCP+
  // по UDP 623, но endpoint_url всё равно проходит через серверный
  // validate_safe_endpoint_url, который пропускает только http/https-схемы
  // (см. utils/url_security.py). Поэтому для ipmitool кладём http:// — этого
  // достаточно: worker extract_bmc_host отрезает схему и работает с host:port,
  // а схема ipmi:// отбивалась бы 422 ещё на регистрации.
  if (kind === "ipmi") {
    return port ? `http://${host}:${port}` : `http://${host}`;
  }
  return port ? `https://${host}:${port}` : `https://${host}`;
}

function RegisterPane({
  serverId,
  canRegister,
  denyReason,
  onRegistered,
}: {
  serverId: string;
  canRegister: boolean;
  denyReason: string;
  onRegistered: () => void;
}) {
  const toast = useToast();
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [kind, setKind] = useState<IpmiKind>("redfish");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setErr(null);
    const endpoint = defaultEndpointFor(kind, host.trim(), port.trim());
    // IpmiCreateRequest несёт только kind/endpoint_url/username/password.
    // TLS-verify в схеме контроллера нет: worker пробует схему и проверку
    // сертификата на каждом вызове (https-verify → https-no-verify → http →
    // ipmitool) и не кэширует выбор, поэтому отдельного поля для него нет.
    const body: IpmiCreateRequest = {
      kind,
      endpoint_url: endpoint,
      username: username.trim(),
      password,
    };
    setSubmitting(true);
    registerIpmi(serverId, body)
      .then(() => {
        toast.success("IPMI-контроллер зарегистрирован");
        onRegistered();
      })
      .catch((e: unknown) => {
        const msg = apiErrMsg(e, "Не удалось зарегистрировать IPMI");
        setErr(msg);
        toast.error(msg);
      })
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="p-5 max-w-xl">
      <div className="card">
        <div className="flex items-center gap-2 mb-3">
          <KeyRound className="w-5 h-5" />
          <h3 className="text-base font-semibold">Регистрация IPMI</h3>
        </div>
        <p className="text-sm text-dim mb-4">
          У сервера ещё нет IPMI-контроллера. Заполните параметры BMC, чтобы
          получить power-операции и ротацию паролей.
        </p>

        {!canRegister && (
          <div className="mb-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="text-sm">
              Недостаточно прав для регистрации IPMI.{" "}
              {denyReason && <span className="text-dim">{denyReason}.</span>}
            </div>
          </div>
        )}

        {err && (
          <div className="mb-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="text-sm">{err}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">host *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="bmc-node-01.example"
              required
              disabled={!canRegister}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">port</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={port}
              onChange={(e) => setPort(e.target.value)}
              type="number"
              min={1}
              max={65535}
              placeholder={kind === "ipmi" ? "623" : "443"}
              disabled={!canRegister}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">kind *</span>
            <select
              className="surface-2 border border-token rounded px-2 py-1"
              value={kind}
              onChange={(e) => setKind(e.target.value as IpmiKind)}
              disabled={!canRegister}
            >
              <option value="redfish">Redfish (generic)</option>
              <option value="idrac">iDRAC</option>
              <option value="ilo">iLO</option>
              <option value="ipmi">ipmitool (RMCP+)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">username *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="off"
              required
              disabled={!canRegister}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">password * (write-only)</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="new-password"
              required
              disabled={!canRegister}
            />
          </label>
          <p className="text-xs text-dim">
            TLS-проверка не настраивается здесь: worker сам подбирает транспорт
            (https с проверкой сертификата → https без проверки → http →
            ipmitool) на каждом обращении к BMC.
          </p>

          <div className="flex items-center gap-2 mt-2">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={
                !canRegister ||
                submitting ||
                !host.trim() ||
                !username.trim() ||
                !password
              }
            >
              {submitting ? "Регистрируем…" : "Зарегистрировать"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Существующий контроллер: metadata + power + credentials + edit + delete
// ---------------------------------------------------------------------------

function ControllerPane({
  controller,
  caps,
  onChanged,
  onDeleted,
}: {
  controller: IpmiController;
  caps: IpmiCaps;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const serverId = controller.server_id;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  function handleDelete() {
    const ok = window.confirm(
      "Удалить IPMI-контроллер? Все power-операции на этом сервере перестанут работать до повторной регистрации.",
    );
    if (!ok) return;
    setDeleting(true);
    deleteIpmi(serverId)
      .then(() => {
        toast.success("IPMI-контроллер удалён");
        onDeleted();
      })
      .catch((e: unknown) => toast.error(apiErrMsg(e, "Не удалось удалить")))
      .finally(() => setDeleting(false));
  }

  return (
    <div className="p-5 flex flex-col gap-4 max-w-3xl">
      {/* metadata */}
      <div className="card">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <KeyRound className="w-5 h-5" />
            <h3 className="text-base font-semibold">IPMI-контроллер</h3>
            <span className="badge">{KIND_LABEL[controller.kind]}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn flex items-center gap-1"
              onClick={() => setEditing(true)}
              disabled={!caps.admin || editing}
              title={caps.admin ? "" : caps.reason}
            >
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={handleDelete}
              disabled={!caps.admin || deleting}
              title={caps.admin ? "" : caps.reason}
            >
              <Trash2 className="w-4 h-4" />
              {deleting ? "Удаляем…" : "Delete"}
            </button>
          </div>
        </div>

        <Meta label="endpoint" value={controller.endpoint_url} mono />
        <Meta label="username" value={controller.username} mono />
        <Meta
          label="last probed"
          value={`${fmtTs(controller.last_probed_at)} · status: ${
            controller.last_status ?? "—"
          }`}
        />
        <Meta label="created" value={fmtTs(controller.created_at)} />
        <Meta label="updated" value={fmtTs(controller.updated_at)} />
      </div>

      {editing && (
        <EditCard
          controller={controller}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}

      <PowerCard serverId={serverId} canPower={caps.power} caps={caps} />

      <CredentialsCard
        serverId={serverId}
        controllerId={controller.id}
        canRotate={caps.admin}
        denyReason={caps.reason}
      />
    </div>
  );
}

function Meta({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-3 py-1 text-sm">
      <span className="text-dim text-xs w-28 shrink-0">{label}</span>
      <span className={mono ? "mono" : ""}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit card
// ---------------------------------------------------------------------------

function EditCard({
  controller,
  onCancel,
  onSaved,
}: {
  controller: IpmiController;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [endpoint, setEndpoint] = useState(controller.endpoint_url);
  const [username, setUsername] = useState(controller.username);
  const [kind, setKind] = useState<IpmiKind>(controller.kind);
  const [submitting, setSubmitting] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    const body: IpmiUpdateRequest = {};
    if (endpoint !== controller.endpoint_url) body.endpoint_url = endpoint;
    if (username !== controller.username) body.username = username;
    if (kind !== controller.kind) body.kind = kind;
    if (Object.keys(body).length === 0) {
      onCancel();
      return;
    }
    setSubmitting(true);
    updateIpmi(controller.server_id, body)
      .then(() => {
        toast.success("IPMI обновлён");
        onSaved();
      })
      .catch((e: unknown) =>
        toast.error(apiErrMsg(e, "Не удалось обновить IPMI")),
      )
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-3">
        <Edit3 className="w-5 h-5" />
        <h3 className="text-base font-semibold">Изменение IPMI</h3>
      </div>
      <p className="text-xs text-dim mb-3">
        Пароль через PATCH не меняется — ротация идёт отдельным потоком через
        кнопку <b>Rotate IPMI</b> (worker dispatch с BMC apply/verify).
      </p>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">endpoint_url</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 mono"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">username</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">kind</span>
          <select
            className="surface-2 border border-token rounded px-2 py-1"
            value={kind}
            onChange={(e) => setKind(e.target.value as IpmiKind)}
          >
            <option value="redfish">Redfish (generic)</option>
            <option value="idrac">iDRAC</option>
            <option value="ilo">iLO</option>
            <option value="ipmi">ipmitool (RMCP+)</option>
          </select>
        </label>
        <div className="flex items-center gap-2 mt-2">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting}
          >
            {submitting ? "Сохраняем…" : "Сохранить"}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            Отмена
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Power card
// ---------------------------------------------------------------------------

function PowerCard({
  serverId,
  canPower,
  caps,
}: {
  serverId: string;
  canPower: boolean;
  caps: IpmiCaps;
}) {
  const toast = useToast();
  const [status, setStatus] = useState<PowerStatus | null>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [pending, setPending] = useState<
    "on" | "off" | "reboot" | "status" | null
  >(null);

  // Живёт весь lifecycle компонента: ставится в false при unmount, чтобы ни
  // polling, ни отложенные через setTimeout перепроверки не дёргали setState
  // после размонтирования.
  const aliveRef = useRef(true);
  const deferRef = useRef<number | null>(null);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (deferRef.current !== null) {
        window.clearTimeout(deferRef.current);
        deferRef.current = null;
      }
    };
  }, []);

  const fetchStatus = useCallback(() => {
    let cancelled = false;
    getPowerStatus(serverId)
      .then((s) => {
        if (!cancelled && aliveRef.current) {
          setStatus(s);
          setStatusErr(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled && aliveRef.current)
          setStatusErr(apiErrMsg(e, "Не удалось получить power"));
      })
      .finally(() => {
        if (!cancelled && aliveRef.current) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [serverId]);

  // Initial fetch + 10-sec polling. Чтобы не флапать при размонтировании,
  // храним cleanup из fetchStatus в ref-like closure.
  useEffect(() => {
    const stop = fetchStatus();
    const id = window.setInterval(() => {
      fetchStatus();
    }, POWER_REFRESH_MS);
    return () => {
      stop();
      window.clearInterval(id);
    };
  }, [fetchStatus]);

  // Отложенная перепроверка статуса после power-операции: backend обновит
  // power_state async через worker-callback, поэтому подёргиваем статус через
  // секунду. Таймер один на компонент — новый запрос отменяет предыдущий.
  const scheduleRefresh = useCallback(() => {
    if (deferRef.current !== null) window.clearTimeout(deferRef.current);
    deferRef.current = window.setTimeout(() => {
      deferRef.current = null;
      if (aliveRef.current) fetchStatus();
    }, 1000);
  }, [fetchStatus]);

  function runPower(
    kind: "on" | "off" | "reboot",
    fn: (id: string) => Promise<{ task_id: string; status: string }>,
    confirmMsg?: string,
  ) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setPending(kind);
    fn(serverId)
      .then((r) => {
        toast.success(`Задача ${kind} принята: ${r.task_id}`);
        // backend в этот момент ещё не успел обновить power_state — он
        // прилетит async через callback worker'а. Подёргаем статус через
        // секунду, дальше всё равно поедет polling.
        scheduleRefresh();
      })
      .catch((e: unknown) => toast.error(apiErrMsg(e, `Power ${kind} не отправлен`)))
      .finally(() => {
        if (aliveRef.current) setPending(null);
      });
  }

  function runDispatchStatus() {
    setPending("status");
    dispatchPowerStatus(serverId)
      .then((r) => toast.info(`power.status поставлен в очередь: ${r.task_id}`))
      .catch((e: unknown) =>
        toast.error(apiErrMsg(e, "Не удалось запросить probe")),
      )
      .finally(() => {
        if (aliveRef.current) setPending(null);
      });
  }

  const powerLabel = (() => {
    if (statusLoading) return "загружаем…";
    if (statusErr) return statusErr;
    if (!status) return "—";
    return status.power_state;
  })();

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Power className="w-5 h-5" />
          <h3 className="text-base font-semibold">Power</h3>
        </div>
        <button
          className="btn btn-ghost flex items-center gap-1 text-xs"
          onClick={fetchStatus}
          title="Перечитать кэшированный power_state"
        >
          <RefreshCw className="w-3 h-3" /> refresh
        </button>
      </div>

      <div className="flex items-baseline gap-3 mb-3 text-sm">
        <span className="text-dim text-xs w-28 shrink-0">state</span>
        <span className="mono">{powerLabel}</span>
        {status?.last_probed_at && (
          <span className="text-xs text-dim">
            probed: {fmtTs(status.last_probed_at)}
          </span>
        )}
      </div>

      {!canPower && (
        <div className="text-xs text-dim mb-3">
          Power-операции недоступны: {caps.reason || "нет роли"}.
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={!canPower || pending !== null}
          onClick={() => runPower("on", powerOn)}
          title={canPower ? "" : caps.reason}
        >
          <Power className="w-4 h-4" />
          {pending === "on" ? "Отправляем…" : "Power On"}
        </button>
        <button
          className="btn btn-danger flex items-center gap-1"
          disabled={!canPower || pending !== null}
          onClick={() =>
            runPower(
              "off",
              powerOff,
              "Hard power off — соединения SSH/тесты будут оборваны. Продолжить?",
            )
          }
          title={canPower ? "" : caps.reason}
        >
          <Power className="w-4 h-4" />
          {pending === "off" ? "Отправляем…" : "Power Off"}
        </button>
        <button
          className="btn flex items-center gap-1"
          disabled={!canPower || pending !== null}
          onClick={() =>
            runPower(
              "reboot",
              powerReboot,
              "Reboot — BMC выполнит power-cycle. Продолжить?",
            )
          }
          title={canPower ? "" : caps.reason}
        >
          <RefreshCw className="w-4 h-4" />
          {pending === "reboot" ? "Отправляем…" : "Reboot"}
        </button>
        <button
          className="btn flex items-center gap-1"
          disabled={!canPower || pending !== null}
          onClick={runDispatchStatus}
          title={
            canPower
              ? "Запросить live probe BMC (worker, результат через /tasks/{id})"
              : caps.reason
          }
        >
          <Zap className="w-4 h-4" />
          {pending === "status" ? "Отправляем…" : "Dispatch power-status"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Credentials card + rotate
// ---------------------------------------------------------------------------

function CredentialsCard({
  serverId,
  controllerId,
  canRotate,
  denyReason,
}: {
  serverId: string;
  controllerId: string;
  canRotate: boolean;
  denyReason: string;
}) {
  const toast = useToast();
  const [creds, setCreds] = useState<IpmiCredentials | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const [tick, setTick] = useState(0);

  const aliveRef = useRef(true);
  const deferRef = useRef<number | null>(null);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (deferRef.current !== null) {
        window.clearTimeout(deferRef.current);
        deferRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getIpmiCredentials(serverId)
      .then((c) => {
        if (!cancelled) setCreds(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setErr(apiErrMsg(e, "Не удалось получить credentials"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [serverId, tick]);

  function handleRotate() {
    const ok = window.confirm(
      "Запустить ротацию IPMI-пароля? Новый пароль будет сгенерирован и применён к BMC; старый перестанет работать сразу после успешного callback'а worker'а.",
    );
    if (!ok) return;
    setRotating(true);
    rotateIpmi(controllerId)
      .then((r) =>
        toast.success(`Ротация поставлена в очередь: ${r.task_id}`),
      )
      .catch((e: unknown) => toast.error(apiErrMsg(e, "Ротация не запущена")))
      .finally(() => {
        if (!aliveRef.current) return;
        setRotating(false);
        // Метаданные credentials.password_rotated_at обновятся после
        // успешного callback'а worker'а; пересмотрим через секунду.
        if (deferRef.current !== null) window.clearTimeout(deferRef.current);
        deferRef.current = window.setTimeout(() => {
          deferRef.current = null;
          if (aliveRef.current) setTick((n) => n + 1);
        }, 1000);
      });
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-3">
        <KeyRound className="w-5 h-5" />
        <h3 className="text-base font-semibold">Credentials</h3>
      </div>

      {loading && <div className="text-sm text-dim">загружаем…</div>}
      {err && (
        <div className="alert alert-danger flex items-start gap-2 mb-3">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="text-sm">{err}</div>
        </div>
      )}
      {creds && (
        <>
          <Meta label="username" value={creds.username} mono />
          <Meta label="endpoint" value={creds.endpoint_url} mono />
          <Meta label="kind" value={KIND_LABEL[creds.kind]} />
          <Meta
            label="last rotation"
            value={fmtTs(creds.password_rotated_at)}
          />
        </>
      )}

      <p className="mt-3 text-xs text-dim">
        Ротация идёт через worker: генерируется новый пароль, применяется на BMC
        (Redfish PATCH или ipmitool), затем выполняется read-only verify новым
        паролем и только после этого шифруется и сохраняется. Результат
        прилетает асинхронно через <code>/tasks/{`{id}`}</code>; метаданные ниже
        обновятся после успешного callback'а.
      </p>

      {!canRotate && (
        <div className="text-xs text-dim mt-3">
          Rotate недоступен: {denyReason || "нет роли"}.
        </div>
      )}

      <div className="mt-3">
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={handleRotate}
          disabled={!canRotate || rotating}
          title={canRotate ? "" : denyReason}
        >
          <KeyRound className="w-4 h-4" />
          {rotating ? "Запускаем…" : "Rotate IPMI"}
        </button>
      </div>
    </div>
  );
}
