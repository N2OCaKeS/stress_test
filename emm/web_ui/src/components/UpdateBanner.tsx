import { useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { fetchBuildSha, VERSION_POLL_INTERVAL_MS } from "@/lib/appVersion";

/**
 * Мягкое уведомление о новой версии фронта.
 *
 * Легаси (allta_app) на передеплое форсит логаут и редирект на /login через
 * одноразовый флаг-файл. Здесь ничего похожего — просто предлагаем обновить
 * вкладку, когда SHA сборки на сервере разошёлся с тем, с которым вкладка
 * была открыта. Сессию и текущую работу пользователя баннер не трогает.
 *
 * Монтируется один раз у корня приложения, рядом с ReencryptBanner.
 */
export function UpdateBanner() {
  const [available, setAvailable] = useState(false);
  const initialShaRef = useRef<string | null>(null);
  const dismissedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const tick = () => {
      fetchBuildSha().then((sha) => {
        if (cancelled || !sha || dismissedRef.current) return;
        if (initialShaRef.current === null) {
          // Первый успешный ответ — запоминаем как SHA открытой вкладки,
          // сравнивать его с самим собой не нужно.
          initialShaRef.current = sha;
        } else if (sha !== initialShaRef.current) {
          setAvailable(true);
        }
      });
    };

    tick();
    const intervalId = window.setInterval(tick, VERSION_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  if (!available) return null;

  return (
    <div className="fixed bottom-4 left-0 right-0 z-50 flex justify-center px-3 pointer-events-none">
      <div
        className="alert-info pointer-events-auto w-full max-w-md shadow-lg"
        role="status"
        aria-live="polite"
      >
        <RefreshCw className="w-4 h-4 shrink-0" />
        <div className="flex-1 text-xs">
          Доступна новая версия интерфейса — обновите вкладку.
        </div>
        <Button variant="primary" size="sm" onClick={() => window.location.reload()}>
          Обновить
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="flex items-center"
          onClick={() => {
            dismissedRef.current = true;
            setAvailable(false);
          }}
          title="Скрыть"
        >
          <X className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}
