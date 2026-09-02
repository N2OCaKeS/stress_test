/**
 * Общая строка среднего списка для сервера и ВМ. Раскладка, колонки и отступы
 * одинаковы у обеих сущностей — различаются только данные: иконка, подпись типа
 * и правый блок сигналов (у сервера ping + бронь, у ВМ — состояние virsh).
 *
 * Структура (иконка · имя+подпись · блок сигналов) держится строго: список
 * серверов тестируется по классам (усечение IP, неусыхаемый блок бейджей).
 */
import type { ReactNode } from "react";

export interface EntityRowProps {
  /** Иконка строки (у сервера несёт data-testid/aria-label по типу/статусу). */
  icon: ReactNode;
  active: boolean;
  onSelect: () => void;
  /** Отображаемое имя сущности. */
  title: string;
  /** Короткая подпись типа: «сервер» / «VMS-hub» / «ВМ». */
  kindLabel: string;
  /** Название отдела. */
  deptLabel: string;
  /** Правая часть подписи (обычно IP-адрес). */
  subtitle: string;
  /** Правый блок сигналов/бейджей (ping+бронь у сервера, virsh у ВМ). */
  badges: ReactNode;
  /** Режим множественного выбора (чекбокс) — только для серверов. */
  selectable?: boolean;
  checked?: boolean;
  onToggleChecked?: () => void;
}

export function EntityRow({
  icon,
  active,
  onSelect,
  title,
  kindLabel,
  deptLabel,
  subtitle,
  badges,
  selectable = false,
  checked = false,
  onToggleChecked,
}: EntityRowProps) {
  return (
    <div
      className={`cred-row text-left flex items-center gap-2 ${active ? "active" : ""}`}
    >
      {selectable && (
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggleChecked}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0"
          title="Выбрать для массовой операции"
        />
      )}
      <button type="button" onClick={onSelect} className="flex-1 min-w-0 text-left">
        <div className="flex items-center gap-2">
          {icon}
          <div className="flex-1 min-w-0">
            <div className="text-sm truncate">{title}</div>
            <div className="text-[11px] text-dim flex items-center gap-1.5 min-w-0">
              <span className="uppercase tracking-wide text-[10px] shrink-0">
                {kindLabel}
              </span>
              <span className="shrink-0">·</span>
              <span className="truncate">{deptLabel}</span>
              <span className="shrink-0">·</span>
              <span className="mono truncate">{subtitle}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">{badges}</div>
        </div>
      </button>
    </div>
  );
}
