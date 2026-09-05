/**
 * Общая шапка карточки сервера и ВМ. Единый вид: иконка в акцентном квадрате,
 * имя, ряд бейджей/сигналов, строка мета-полей и опциональный слот управления
 * (например, бронь). Различие сущностей только в данных — набор сигналов у
 * сервера включает IPMI-питание, у ВМ на том же месте — состояние virsh.
 */
import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/Button";

export interface EntityHeaderProps {
  /** Содержимое акцентного квадрата слева (иконка сущности). */
  icon: ReactNode;
  /** Кнопка «назад» перед иконкой (у ВМ — возврат к списку/хабу). */
  onBack?: () => void;
  backLabel?: string;
  name: string;
  /** Ряд бейджей и сигналов справа от имени (статус/бронь/ping/ssh/питание). */
  badges: ReactNode;
  /** Строка мета-полей под именем (id / hostname / IP / отдел и т.п.). */
  meta: ReactNode;
  /** Дополнительный блок под мета-строкой (управление бронью и пр.). */
  children?: ReactNode;
}

export function EntityHeader({
  icon,
  onBack,
  backLabel = "Назад",
  name,
  badges,
  meta,
  children,
}: EntityHeaderProps) {
  return (
    <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
      {onBack && (
        <Button variant="ghost"
          type="button"
          className="flex items-center gap-1"
          onClick={onBack}
        >
          <ArrowLeft className="w-4 h-4" /> {backLabel}
        </Button>
      )}
      <div className="w-12 h-12 rounded bg-accent flex items-center justify-center shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-xl font-semibold truncate">{name}</h1>
          {badges}
        </div>
        <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
          {meta}
        </div>
        {children}
      </div>
    </div>
  );
}
