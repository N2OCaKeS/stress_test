import { Button } from "@/components/ui/Button";
import type { ActiveQueueMode } from "@/api/testing/types";
import { CURRENT_STATE_LABELS, isTakeoverPossible, text, useHolderLabel, type Details } from "./standConflict";

interface QueueActiveInfo {
  queuedCount: number;
  current: { label: string; state: string | null } | null;
}

function parseQueueActive(details: Details): QueueActiveInfo {
  const raw = details?.current as Record<string, unknown> | null | undefined;
  const label = raw ? text(raw.test_code) ?? text(raw.test_name) ?? text(raw.test_id) : null;
  const state = raw ? text(raw.state) : null;
  return {
    queuedCount: typeof details?.queued_count === "number" ? details.queued_count : 0,
    current: label ? { label, state } : null,
  };
}

/**
 * Вопрос админу, когда очередь стенда уже идёт: добавить тесты в конец или
 * очистить очередь и начать сразу. Не-админу сюда попасть нечем — сервер
 * молча ставит его тесты в конец, поэтому без права выбора показываем только
 * состояние очереди.
 */
export function QueueActivePrompt({
  details,
  canChoose,
  busy,
  onChoose,
}: {
  details: Details;
  canChoose: boolean;
  busy: boolean;
  onChoose: (mode: ActiveQueueMode) => void;
}) {
  const info = parseQueueActive(details);
  const stateLabel = info.current?.state ? CURRENT_STATE_LABELS[info.current.state] ?? info.current.state : null;
  return (
    <div role="group" aria-label="Очередь стенда занята" className="text-xs grid gap-2 surface-2 border border-token rounded p-2">
      <div className="text-warn">
        На стенде уже идёт очередь тестов
        {info.current ? <>: сейчас <span className="mono">{info.current.label}</span>{stateLabel ? ` (${stateLabel})` : ""}</> : ""}
        {info.queuedCount > 0 ? `, в очереди ещё ${info.queuedCount}` : ""}.
      </div>
      {canChoose && (
        <>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={busy} onClick={() => onChoose("append")}>
              Добавить в конец очереди
            </Button>
            <Button type="button" size="sm" variant="primary" disabled={busy} onClick={() => onChoose("replace")}>
              Очистить очередь и запустить сразу
            </Button>
          </div>
          <div className="text-dim">Второй вариант прерывает текущий тест и удаляет ожидающие — они не вернутся.</div>
        </>
      )}
    </div>
  );
}

/**
 * Занятый стенд: админу своего отдела — кнопка «Забрать стенд», остальным —
 * только уведомление (его выводит вызывающая модалка). Если сервер сообщил,
 * что стенд забрать нельзя (обновление ОС, восстановление образа), кнопки нет.
 */
export function StandBusyPrompt({
  details,
  canTakeover,
  busy,
  onTakeover,
}: {
  details: Details;
  canTakeover: boolean;
  busy: boolean;
  onTakeover: () => void;
}) {
  const holder = useHolderLabel(details);
  if (!canTakeover) return null;
  if (!isTakeoverPossible(details)) {
    return <div className="text-xs text-dim">Стенд забрать нельзя: идёт обновление ОС или восстановление образа — дождитесь завершения.</div>;
  }
  return (
    <div className="grid gap-1">
      <Button type="button" size="sm" variant="primary" disabled={busy} onClick={onTakeover}>
        {busy ? "Запускаем…" : `Забрать стенд у ${holder} и запустить`}
      </Button>
    </div>
  );
}

/** Строка «Стенд занят (<кто>) — …» для отказа запуска на занятом стенде. */
export function StandBusyNotice({ details, suffix }: { details: Details; suffix: string }) {
  const holder = useHolderLabel(details);
  return <>Стенд занят ({holder}) — {suffix}</>;
}
