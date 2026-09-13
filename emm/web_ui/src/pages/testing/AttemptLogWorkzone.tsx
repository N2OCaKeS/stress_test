import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { listQueueItems, retryQueueItem } from "@/api/testing/queueItems";
import { AttemptLogViewer } from "./AttemptLogViewer";

export function AttemptLogWorkzone({ id, state, logStatus, title, subtitle, canRetry, onClose, onRetried, track = false }: {
  id: string; state: string; logStatus?: string; title: string; subtitle?: string;
  track?: boolean; canRetry?: boolean; onClose: () => void; onRetried?: () => void;
}) {
  const currentQ = useQuery(() => listQueueItems({ kind: "all", attempt_id: id }), [id], { enabled: track });
  useEffect(() => {
    if (!track) return;
    const timer = setInterval(currentQ.refetch, 5000);
    return () => clearInterval(timer);
  }, [track, currentQ.refetch]);
  const current = currentQ.data?.items[0];
  state = current?.state ?? state;
  logStatus = current?.log_status ?? logStatus;
  canRetry = current ? current.is_current !== false && ["succeeded", "failed"].includes(state) : canRetry;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const keys = useRef<Record<string, string>>({});
  async function retry() {
    if (busy) return;
    setBusy(true); setMessage("");
    try {
      const item = await retryQueueItem(id, keys.current[id] ??= crypto.randomUUID());
      setMessage(`Создана новая попытка ${item.id}`); onRetried?.();
    } catch (error) { setMessage(apiErrMsg(error, "Не удалось повторить тест")); }
    finally { setBusy(false); }
  }
  const unavailable = logStatus === "rotated" || logStatus === "missing";
  return <section className="flex flex-1 min-h-0 flex-col gap-3" aria-label="Лог выбранной попытки">
    <header className="flex items-start justify-between gap-3 shrink-0">
      <div className="min-w-0"><h2 className="font-semibold break-words">{title}</h2><p className="text-xs text-dim mt-1 break-words">{subtitle}</p></div>
      <Button size="sm" aria-label="Закрыть лог" onClick={onClose}><X className="w-4 h-4" /></Button>
    </header>
    <div className="flex-1 min-h-0 overflow-auto grid gap-3 content-start">
      {unavailable ? <div className="surface border border-token rounded p-6 grid gap-3">
        <p>{logStatus === "rotated" ? "Лог удалён по сроку хранения (ротирован). Результат теста сохранён." : "Текст лога этой попытки отсутствует. Результат теста сохранён."}</p>
        {canRetry && <Button disabled={busy} onClick={retry}>Перезапустить тест</Button>}
        {!canRetry && <p className="text-xs text-dim">Для повторного запуска выберите последнюю попытку теста.</p>}
        {message && <p role="status" className="text-sm">{message}</p>}
      </div> : <AttemptLogViewer key={id} queueItemId={id} state={state} />}
    </div>
  </section>;
}
