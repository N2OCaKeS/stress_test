/**
 * Общие индикаторы состояния для сервера и ВМ — вынесены, чтобы шапка карточки
 * и строка списка выглядели одинаково у обеих сущностей. Различие только в
 * источнике данных: у сервера сигнал питания снимается по IPMI, у ВМ — из virsh.
 */
import { formatLatencyMs } from "@/pages/server/_serverShared";
import { Badge } from "@/components/ui/Badge";

/**
 * Порог деградации latency для ping/ssh (мс). Тестовые стенды сидят в одном
 * LAN-сегменте, где здоровая проба укладывается в единицы-десятки мс — всё,
 * что выше 150 мс, уже говорит о проблеме на линке или перегрузке хоста, хотя
 * узел формально ещё отвечает. Используем как границу ok/warn.
 */
const HIGH_LATENCY_MS = 150;

type ReachTier = "ok" | "warn" | "danger";

function reachTier(
  reachable: boolean,
  latencyMs: number | null | undefined,
): ReachTier {
  if (!reachable) return "danger";
  return latencyMs != null && latencyMs > HIGH_LATENCY_MS ? "warn" : "ok";
}

/**
 * Сигнал доступности (ping/ssh) в шапке карточки: «доступен» + latency либо
 * «недоступен»; `null` — проба не снималась, показываем нейтральный прочерк.
 * При высокой latency (см. HIGH_LATENCY_MS) узел всё ещё доступен, но
 * подсвечивается оранжевым как предупреждение.
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
  const tier = reachTier(reachable, latencyMs);
  const colorClass =
    tier === "ok" ? "text-ok" : tier === "warn" ? "text-warn" : "text-danger";
  const statusWord = reachable
    ? tier === "warn"
      ? "высокая задержка"
      : "доступен"
    : "недоступен";
  return (
    <span
      className={`text-xs ${colorClass}`}
      title={`доступность по ${label}`}
    >
      {label}: <b>{statusWord}</b>
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
  const kind = state === "on" ? "ok" : state === "off" ? "danger" : "neutral";
  const label =
    state === "on"
      ? "питание: вкл"
      : state === "off"
        ? "питание: выкл"
        : "питание: —";
  return <Badge kind={kind} title="состояние питания">{label}</Badge>;
}

/**
 * Компактный бейдж доступности по ping для строки списка: доступен (зелёный,
 * latency где есть), доступен с высокой задержкой (оранжевый, см.
 * HIGH_LATENCY_MS), недоступен (красный), проба не снималась (нейтральный «—»).
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
      <Badge title="ping: не проверялось">
        —
      </Badge>
    );
  }
  if (reachable) {
    const lat = formatLatencyMs(latencyMs);
    const tier = reachTier(reachable, latencyMs);
    if (tier === "warn") {
      return (
        <Badge kind="warn" title="ping: доступен, высокая задержка">
          {lat ?? "доступен"}
        </Badge>
      );
    }
    return (
      <Badge kind="ok" title="ping: доступен">
        {lat ?? "доступен"}
      </Badge>
    );
  }
  return (
    <Badge kind="danger" title="ping: недоступен">
      недоступен
    </Badge>
  );
}
