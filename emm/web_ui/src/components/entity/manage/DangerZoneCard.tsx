/**
 * «Опасная зона» — общая карточка необратимого удаления сущности (сервер / ВМ).
 * Вид одинаков; подтверждение/причину собирает вызывающий и передаёт готовый
 * обработчик клика.
 */
import type { ReactNode } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";

export function DangerZoneCard({
  description,
  buttonLabel,
  onDelete,
  busy = false,
}: {
  description: ReactNode;
  buttonLabel: string;
  onDelete: () => void | Promise<void>;
  busy?: boolean;
}) {
  return (
    <div className="card" style={{ border: "1px solid var(--danger, #b91c1c)" }}>
      <div className="text-sm font-semibold flex items-center gap-2 text-danger mb-2">
        <AlertTriangle className="w-4 h-4" /> Опасная зона
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 text-xs text-dim">{description}</div>
        <Button variant="danger"
          className="flex items-center gap-1"
          disabled={busy}
          onClick={onDelete}
        >
          <Trash2 className="w-4 h-4" /> {buttonLabel}
        </Button>
      </div>
    </div>
  );
}
