import { useEffect, useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import { registerReencryptHandler, type ReencryptNotice } from "@/api/client";

/**
 * Глобальный баннер обслуживания на время force-перешифровки ключа.
 *
 * Когда сервис уходит в экстренную полную перешифровку, он отвечает 503
 * REENCRYPT_IN_PROGRESS на любой запрос (кроме статуса/health) с заголовком
 * Retry-After. Api-клиент перехватывает это и дёргает зарегистрированный здесь
 * handler — вместо обычного тоста показываем плашку с обратным отсчётом. По
 * истечении отсчёта баннер скрывается сам: сервис к этому моменту должен
 * вернуться, а следующее действие пользователя пройдёт заново.
 *
 * Монтируется один раз у корня приложения.
 */

const SERVICE_LABEL: Record<ReencryptNotice["service"], string> = {
  server: "server_service",
  secret: "secret_service",
  unknown: "сервис",
};

interface BannerState {
  service: ReencryptNotice["service"];
  message?: string;
  remaining?: number;
  /** Абсолютный дедлайн отсчёта, ms. */
  deadline: number;
}

function formatLeft(seconds: number): string {
  if (seconds >= 60) {
    const mins = Math.ceil(seconds / 60);
    return `~${mins} мин`;
  }
  return `${seconds} сек`;
}

export function ReencryptBanner() {
  const [state, setState] = useState<BannerState | null>(null);
  // Пустой tick только чтобы перерисовывать отсчёт раз в секунду.
  const [, setTick] = useState(0);

  useEffect(() => {
    registerReencryptHandler((notice) => {
      const secs = Math.max(1, Math.ceil(notice.retryAfter || notice.etaSeconds || 30));
      const deadline = Date.now() + secs * 1000;
      setState((prev) => ({
        service: notice.service,
        message: notice.message,
        remaining: notice.remaining,
        // Повторные 503 во время активного баннера продлевают отсчёт до
        // большего дедлайна, а не сбрасывают его на меньший.
        deadline: prev && prev.deadline > deadline ? prev.deadline : deadline,
      }));
    });
    return () => registerReencryptHandler(null);
  }, []);

  useEffect(() => {
    if (!state) return;
    const id = window.setInterval(() => {
      if (Date.now() >= state.deadline) {
        setState(null);
      } else {
        setTick((x) => x + 1);
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [state]);

  if (!state) return null;

  const secondsLeft = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));

  return (
    <div className="fixed top-0 left-0 right-0 z-50 flex justify-center px-3 pt-3 pointer-events-none">
      <div
        className="alert-warn pointer-events-auto w-full max-w-3xl shadow-lg"
        role="status"
        aria-live="polite"
      >
        <ShieldAlert className="w-5 h-5 shrink-0" />
        <div className="flex-1 text-xs">
          <div className="font-semibold">
            Идёт экстренная перешифровка ключа ({SERVICE_LABEL[state.service]})
          </div>
          <div>
            Сервис временно недоступен. Повторите через {formatLeft(secondsLeft)}.
            {typeof state.remaining === "number" &&
              ` Осталось строк: ${state.remaining}.`}
          </div>
        </div>
        <button
          className="btn btn-ghost flex items-center"
          onClick={() => setState(null)}
          title="Скрыть"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
