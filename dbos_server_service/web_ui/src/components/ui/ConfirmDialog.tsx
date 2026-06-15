import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, Info } from "lucide-react";

/**
 * Промис-based замена `window.confirm` / `window.alert` / `window.prompt`.
 *
 * `confirm()` показывает да/нет и резолвится в boolean. С `reason: true`
 * к диалогу добавляется поле ввода — резолв возвращает `{ ok, reason }`,
 * где `reason` — введённый текст (или пустая строка). `alert()` показывает
 * единственную кнопку «ОК» и резолвится после закрытия.
 *
 * Стиль — общие `.modal-*` классы из themes.css, как у ForcePasswordChangeModal.
 */

interface ConfirmOptions {
  title?: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** danger-кнопка для деструктивных действий (бан, удаление). */
  danger?: boolean;
}

interface PromptOptions extends ConfirmOptions {
  /** Показать поле ввода (например, причину удаления/бана). */
  reason: true;
  reasonLabel?: string;
  reasonPlaceholder?: string;
  /** Маскировать ввод (для пароля). */
  reasonSecret?: boolean;
  /** Обязательность ввода: если true — кнопка подтверждения недоступна на пустом поле. */
  reasonRequired?: boolean;
  defaultReason?: string;
}

interface AlertOptions {
  title?: string;
  message: ReactNode;
  okLabel?: string;
}

export interface ConfirmApi {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<{ ok: boolean; reason: string }>;
  alert: (opts: AlertOptions) => Promise<void>;
}

const ConfirmContext = createContext<ConfirmApi | undefined>(undefined);

type DialogState =
  | {
      kind: "confirm";
      opts: ConfirmOptions;
      resolve: (v: boolean) => void;
    }
  | {
      kind: "prompt";
      opts: PromptOptions;
      resolve: (v: { ok: boolean; reason: string }) => void;
    }
  | {
      kind: "alert";
      opts: AlertOptions;
      resolve: () => void;
    };

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DialogState | null>(null);
  const [reason, setReason] = useState("");
  // Держим текущее состояние в ref, чтобы close по overlay/esc мог корректно
  // зарезолвить промис «как отмену», не завязываясь на замыкание.
  const stateRef = useRef<DialogState | null>(null);
  stateRef.current = state;

  const close = useCallback(() => {
    setState(null);
    setReason("");
  }, []);

  const cancel = useCallback(() => {
    const s = stateRef.current;
    if (!s) return;
    if (s.kind === "confirm") s.resolve(false);
    else if (s.kind === "prompt") s.resolve({ ok: false, reason: "" });
    else s.resolve();
    close();
  }, [close]);

  const accept = useCallback(() => {
    const s = stateRef.current;
    if (!s) return;
    if (s.kind === "confirm") s.resolve(true);
    else if (s.kind === "prompt") s.resolve({ ok: true, reason });
    else s.resolve();
    close();
  }, [close, reason]);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setReason("");
        setState({ kind: "confirm", opts, resolve });
      }),
    [],
  );

  const prompt = useCallback(
    (opts: PromptOptions) =>
      new Promise<{ ok: boolean; reason: string }>((resolve) => {
        setReason(opts.defaultReason ?? "");
        setState({ kind: "prompt", opts, resolve });
      }),
    [],
  );

  const alert = useCallback(
    (opts: AlertOptions) =>
      new Promise<void>((resolve) => {
        setReason("");
        setState({ kind: "alert", opts, resolve });
      }),
    [],
  );

  const api: ConfirmApi = { confirm, prompt, alert };

  const open = state !== null;
  const isAlert = state?.kind === "alert";
  const isPrompt = state?.kind === "prompt";
  const opts = state?.opts;
  const danger = (opts as ConfirmOptions | undefined)?.danger ?? false;
  const reasonRequired =
    isPrompt && (state.opts as PromptOptions).reasonRequired === true;
  const acceptDisabled = reasonRequired && reason.trim().length === 0;

  const title = opts?.title ?? (isAlert ? "Уведомление" : "Подтверждение");

  return (
    <ConfirmContext.Provider value={api}>
      {children}
      <Dialog.Root
        open={open}
        onOpenChange={(next) => {
          if (!next) cancel();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="modal-overlay" />
          <Dialog.Content
            className="modal-content"
            onOpenAutoFocus={(e) => {
              // Для prompt отдаём фокус полю ввода, иначе — дефолтная кнопка.
              if (!isPrompt) e.preventDefault();
            }}
          >
            <div className="modal-header">
              {isAlert ? (
                <Info className="w-5 h-5 text-accent" />
              ) : (
                <AlertTriangle
                  className={danger ? "w-5 h-5 text-danger" : "w-5 h-5 text-warn"}
                />
              )}
              <Dialog.Title className="text-base font-semibold">
                {title}
              </Dialog.Title>
            </div>
            <div className="modal-body">
              <Dialog.Description asChild>
                <div className="text-sm text-dim whitespace-pre-line">
                  {opts?.message}
                </div>
              </Dialog.Description>
              {isPrompt && (
                <div className="mt-3">
                  {(state.opts as PromptOptions).reasonLabel && (
                    <label className="field-label">
                      {(state.opts as PromptOptions).reasonLabel}
                    </label>
                  )}
                  <input
                    type={
                      (state.opts as PromptOptions).reasonSecret
                        ? "password"
                        : "text"
                    }
                    className="field-input"
                    autoFocus
                    value={reason}
                    placeholder={
                      (state.opts as PromptOptions).reasonPlaceholder
                    }
                    onChange={(e) => setReason(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !acceptDisabled) {
                        e.preventDefault();
                        accept();
                      }
                    }}
                  />
                </div>
              )}
            </div>
            <div className="modal-footer">
              {!isAlert && (
                <button type="button" className="btn" onClick={cancel}>
                  {(opts as ConfirmOptions | undefined)?.cancelLabel ??
                    "Отмена"}
                </button>
              )}
              <button
                type="button"
                className={
                  danger ? "btn btn-danger" : "btn btn-primary"
                }
                onClick={accept}
                disabled={acceptDisabled}
              >
                {isAlert
                  ? ((opts as AlertOptions | null)?.okLabel ?? "ОК")
                  : ((opts as ConfirmOptions | null)?.confirmLabel ?? "Подтвердить")}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmApi {
  const ctx = useContext(ConfirmContext);
  if (!ctx)
    throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return ctx;
}
