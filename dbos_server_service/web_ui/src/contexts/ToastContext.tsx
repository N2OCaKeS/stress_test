import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type ToastKind = "success" | "warn" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  success: (msg: string) => void;
  warn: (msg: string) => void;
  error: (msg: string) => void;
  info: (msg: string) => void;
}

const ToastContext = createContext<ToastApi | undefined>(undefined);

const TOAST_TTL_MS = 3000;

/**
 * Global toast provider. Mount once near the root and call `useToast()`
 * from anywhere to show ephemeral notifications in the bottom-right corner.
 *
 * Styling reuses the `.toast` / `.toast-success` / `.toast-warn` /
 * `.toast-error` / `.toast-info` classes from `themes.css`.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const push = useCallback((kind: ToastKind, message: string) => {
    counter.current += 1;
    const id = counter.current;
    setToasts((prev) => [...prev, { id, kind, message }]);
    if (typeof window !== "undefined") {
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, TOAST_TTL_MS);
    }
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      success: (msg) => push("success", msg),
      warn: (msg) => push("warn", msg),
      error: (msg) => push("error", msg),
      info: (msg) => push("info", msg),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col-reverse gap-2 max-h-[calc(100vh-2rem)] overflow-hidden pointer-events-none">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind} pointer-events-auto`}>
            <span className="text-sm font-medium">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

/** Как `useToast`, но возвращает `null` вне провайдера вместо исключения. */
export function useToastOptional(): ToastApi | null {
  return useContext(ToastContext) ?? null;
}
