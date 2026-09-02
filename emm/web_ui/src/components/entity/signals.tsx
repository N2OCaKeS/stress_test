/**
 * Общие индикаторы состояния для сервера и ВМ — вынесены, чтобы шапка карточки
 * и строка списка выглядели одинаково у обеих сущностей. Различие только в
 * источнике данных: у сервера сигнал питания снимается по IPMI, у ВМ — из virsh.
 */
import { formatLatencyMs } from "@/pages/server/_serverShared";

/**
 * Сигнал доступности (ping/ssh) в шапке карточки: «доступен» + latency либо
 * «недоступен»; `null` — проба не снималась, показываем нейтральный прочерк.
 */
export function ReachSignal({
  label,
  reachable,
  latencyMs,
}: {
  label: string;
  reachable?: boolean | null;
  latencyMs?: number | null;
}) {
  if (reachable == null) {
    return (
      <span className="text-xs text-dim" title={`${label}: не проверялось`}>
        {label}: —
      </span>
    );
  }
  const lat = formatLatencyMs(latencyMs);
  return (
    <span
      className={`text-xs ${reachable ? "text-ok" : "text-danger"}`}
      title={`доступность по ${label}`}
    >
      {label}: <b>{reachable ? "доступен" : "недоступен"}</b>
      {reachable && lat ? ` · ${lat}` : ""}
    </span>
  );
}

/**
 * Состояние питания в шапке карточки: включён / выключен / неизвестно. Один и
 * тот же вид для сервера (питание снимается по IPMI) и для ВМ (по virsh) —
 * различается лишь источник значения `power_state`.
 */
export function PowerStateBadge({
  state,
}: {
  state: "on" | "off" | "unknown" | null | undefined;
}) {
  const kind: "ok" | "danger" | "" =
    state === "on" ? "ok" : state === "off" ? "danger" : "";
  const label =
    state === "on"
      ? "питание: вкл"
      : state === "off"
        ? "питание: выкл"
        : "питание: —";
  return (
    <span
      className={`badge${kind ? ` badge-${kind}` : ""}`}
      title="состояние питания"
    >
      {label}
    </span>
  );
}

/**
 * Компактный бейдж доступности по ping для строки списка: доступен (зелёный,
 * latency где есть), недоступен (красный), проба не снималась (нейтральный «—»).
 */
export function ReachRowBadge({
  reachable,
  latencyMs,
}: {
  reachable?: boolean | null;
  latencyMs?: number | null;
}) {
  if (reachable == null) {
    return (
      <span className="badge" title="ping: не проверялось">
        —
      </span>
    );
  }
  if (reachable) {
    const lat = formatLatencyMs(latencyMs);
    return (
      <span className="badge badge-ok" title="ping: доступен">
        {lat ?? "доступен"}
      </span>
    );
  }
  return (
    <span className="badge badge-danger" title="ping: недоступен">
      недоступен
    </span>
  );
}
