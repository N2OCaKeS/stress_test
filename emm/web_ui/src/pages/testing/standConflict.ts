export type Details = Record<string, unknown> | null | undefined;

export const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export const CURRENT_STATE_LABELS: Record<string, string> = {
  preparing: "подготовка стенда",
  ready: "готов к запуску",
  running: "выполняется",
  paused: "на паузе",
};

/** Кто держит стенд — по данным `STAND_BUSY.details`; пользователь важнее сервиса. */
export function describeHolder(details: Details): string {
  return (
    text(details?.busy_user_name) ??
    text(details?.busy_user_id) ??
    text(details?.busy_service_name) ??
    "текущего держателя"
  );
}

/** Можно ли отобрать стенд: сервер отдаёт признак, без него считаем, что можно (решает сервер). */
export function isTakeoverPossible(details: Details): boolean {
  return details?.takeover_possible !== false;
}
