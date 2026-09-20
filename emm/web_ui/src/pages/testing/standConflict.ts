import { useUserLabel } from "@/lib/labels";

export type Details = Record<string, unknown> | null | undefined;

export const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export const CURRENT_STATE_LABELS: Record<string, string> = {
  preparing: "подготовка стенда",
  ready: "готов к запуску",
  running: "выполняется",
  paused: "на паузе",
};

/**
 * Кто держит стенд — по данным `STAND_BUSY.details`; пользователь важнее сервиса.
 * Без имени в деталях показывает сырой id пользователя, для подписи в UI есть
 * `useHolderLabel`.
 */
export function describeHolder(details: Details): string {
  return (
    text(details?.busy_user_name) ??
    text(details?.busy_user_id) ??
    text(details?.busy_service_name) ??
    "текущего держателя"
  );
}

/** То же, что `describeHolder`, но `usr_*` держателя резолвится в ФИО/логин через auth. */
export function useHolderLabel(details: Details): string {
  const userName = text(details?.busy_user_name);
  const userId = text(details?.busy_user_id);
  const resolved = useUserLabel(userName ? null : userId);
  if (userName) return userName;
  if (userId) return resolved;
  return describeHolder(details);
}

/** Можно ли отобрать стенд: сервер отдаёт признак, без него считаем, что можно (решает сервер). */
export function isTakeoverPossible(details: Details): boolean {
  return details?.takeover_possible !== false;
}
