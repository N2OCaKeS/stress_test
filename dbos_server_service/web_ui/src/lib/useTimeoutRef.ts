import { useCallback, useEffect, useRef } from "react";

/**
 * Таймер, заводимый из обработчика (onClick/onBlur), который безопасно
 * чистится при размонтировании компонента. Без этого отложенный `setState`
 * (сброс copy-hint, закрытие дропдауна, авто-скрытие тоста) может выстрелить
 * уже после unmount'а и кинуть warning про обновление на размонтированном
 * компоненте.
 *
 * Возвращает `set(fn, ms)` — заводит таймер, предварительно сбросив прошлый;
 * сам id живёт в ref и очищается на unmount.
 */
export function useTimeoutRef(): (fn: () => void, ms: number) => void {
  const ref = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (ref.current !== null) clearTimeout(ref.current);
    },
    [],
  );

  return useCallback((fn: () => void, ms: number) => {
    if (ref.current !== null) clearTimeout(ref.current);
    ref.current = setTimeout(fn, ms);
  }, []);
}
