import { useCallback, useEffect, useState } from "react";

function readNumber(key: string, fallback: number): number {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw == null) return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}

function readBool(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw === "1" || raw === "true";
  } catch {
    return fallback;
  }
}

/** clamp+persist numeric panel width in localStorage. */
export function usePanelWidth(
  key: string,
  defaultPx: number,
  min: number,
  max: number,
) {
  const [width, setWidthRaw] = useState<number>(() =>
    Math.min(max, Math.max(min, readNumber(key, defaultPx))),
  );

  const setWidth = useCallback(
    (next: number) => {
      const clamped = Math.min(max, Math.max(min, next));
      setWidthRaw(clamped);
      try {
        window.localStorage.setItem(key, String(clamped));
      } catch {
        // ignore quota / disabled storage
      }
    },
    [key, min, max],
  );

  return [width, setWidth] as const;
}

/**
 * clamp+persist numeric panel height. В отличие от ширины высота необязательна:
 * `null` означает «занимать всю доступную высоту» (базовое состояние), а число —
 * зафиксированный пользователем оверрайд. Сброс (setHeight(null)) убирает ключ
 * из localStorage и возвращает панель к заполнению места.
 */
export function usePanelHeight(key: string, min: number, max: number) {
  const [height, setHeightRaw] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw == null) return null;
      const n = Number(raw);
      if (!Number.isFinite(n)) return null;
      return Math.min(max, Math.max(min, n));
    } catch {
      return null;
    }
  });

  const setHeight = useCallback(
    (next: number | null) => {
      if (next == null) {
        setHeightRaw(null);
        try {
          window.localStorage.removeItem(key);
        } catch {
          // ignore
        }
        return;
      }
      const clamped = Math.min(max, Math.max(min, next));
      setHeightRaw(clamped);
      try {
        window.localStorage.setItem(key, String(clamped));
      } catch {
        // ignore quota / disabled storage
      }
    },
    [key, min, max],
  );

  return [height, setHeight] as const;
}

/** persisted boolean flag (used for left-panel collapsed mode). */
export function usePanelFlag(key: string, fallback: boolean) {
  const [value, setValueRaw] = useState<boolean>(() => readBool(key, fallback));

  const setValue = useCallback(
    (next: boolean) => {
      setValueRaw(next);
      try {
        window.localStorage.setItem(key, next ? "1" : "0");
      } catch {
        // ignore
      }
    },
    [key],
  );

  useEffect(() => {
    // sync if another tab toggled
    const onStorage = (e: StorageEvent) => {
      if (e.key === key && e.newValue != null) {
        setValueRaw(e.newValue === "1" || e.newValue === "true");
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [key]);

  return [value, setValue] as const;
}
