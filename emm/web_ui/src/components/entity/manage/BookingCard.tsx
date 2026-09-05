/**
 * Карточка брони — общая для сервера и ВМ. Захват блокирует деструктивные
 * операции других пользователей до снятия. Вид и порядок одинаковы у обеих
 * сущностей; различаются только данные (у сервера есть держатель брони и время
 * захвата, у ВМ — booking-статус).
 */
import { useState, type ReactNode } from "react";
import { Lock, Unlock } from "lucide-react";
import { Button } from "@/components/ui/Button";

export function BookingCard({
  entityWord,
  reserved,
  stateLabel,
  note,
  reserverLabel,
  since,
  canManage,
  foreign = false,
  busy,
  onReserve,
  onRelease,
}: {
  /** Слово для текстов: «сервер» / «ВМ». */
  entityWord: string;
  reserved: boolean;
  /** Текущее состояние занятости (busy_state / booking-статус). */
  stateLabel: string;
  note?: string | null;
  /** Кто держит бронь (у сервера); опционально. */
  reserverLabel?: ReactNode;
  /** Момент захвата, уже отформатированный; опционально. */
  since?: ReactNode;
  canManage: boolean;
  /** Бронь держит другой пользователь — освобождение будет принудительным. */
  foreign?: boolean;
  /** В процессе операции — блокируем кнопки. */
  busy: boolean;
  onReserve: (reason: string) => void | Promise<void>;
  onRelease: () => void | Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [reason, setReason] = useState("");

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Lock className="w-4 h-4 text-accent" /> Бронь
      </h3>

      {reserved ? (
        <div className="alert flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 text-warn" />
          <div className="flex-1 text-xs">
            <div>
              {entityWord === "ВМ" ? "ВМ занята" : `${entityWord} занят`}:{" "}
              <span className="mono">{stateLabel}</span>
              {reserverLabel && <> · {reserverLabel}</>}
            </div>
            {note && (
              <div className="text-dim mt-1">
                Причина: <span className="mono">{note}</span>
              </div>
            )}
            {since && (
              <div className="text-dim text-[11px] mt-1">с {since}</div>
            )}
          </div>
          {canManage && (
            <Button
              className="flex items-center gap-1"
              disabled={busy}
              onClick={onRelease}
              title={
                foreign ? "Снять чужую бронь принудительно" : "Снять бронь"
              }
            >
              <Unlock className="w-4 h-4" />
              {foreign ? "Освободить принудительно" : "Снять бронь"}
            </Button>
          )}
        </div>
      ) : showForm ? (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Примечание (зачем бронь)</span>
            <input
              className="input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="например, ручной debug-цикл"
            />
          </label>
          <div className="flex gap-2 justify-end">
            <Button
              onClick={() => {
                setShowForm(false);
                setReason("");
              }}
              disabled={busy}
            >
              Отмена
            </Button>
            <Button variant="primary"
              disabled={busy || !reason.trim()}
              onClick={async () => {
                await onReserve(reason.trim());
                setShowForm(false);
                setReason("");
              }}
            >
              Забронировать
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-xs text-dim flex-1">
            {entityWord === "ВМ" ? "ВМ свободна" : `${entityWord} свободен`}.
            Бронь блокирует операции других пользователей, пока вы или другой
            админ не снимете lease.
          </div>
          {canManage && (
            <Button variant="primary"
              className="flex items-center gap-1"
              disabled={busy}
              onClick={() => setShowForm(true)}
            >
              <Lock className="w-4 h-4" /> Забронировать
            </Button>
          )}
        </div>
      )}

      {!canManage && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на бронь.
        </div>
      )}
    </div>
  );
}
