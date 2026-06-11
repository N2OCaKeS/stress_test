import { AlertTriangle } from "lucide-react";

interface TruncationNoticeProps {
  /** Сколько элементов реально показано в списке. */
  shown: number;
  /**
   * Сколько всего на бэкенде. `null`/`undefined` — total неизвестен (бэк не
   * вернул `X-Total-Count`/`total`); тогда баннер опирается только на
   * `hasMore`.
   */
  total?: number | null;
  /**
   * Признак, что есть ещё страницы (cursor `next_cursor` / offset «страница
   * заполнена целиком»). Используется, когда точный total недоступен.
   */
  hasMore?: boolean;
  /** Подгрузить следующую страницу. Если не задан — показываем только текст. */
  onLoadMore?: () => void;
  /** Идёт догрузка — гасит кнопку. */
  loadingMore?: boolean;
  className?: string;
}

/**
 * Честный индикатор усечённого списка.
 *
 * Списки тянут страницу с капом (бэкенд auth_service режет `limit` до 200) и
 * раньше молча показывали первые N, не сигналя, что данных больше. Этот баннер
 * рендерится только когда выдача действительно неполная — показывает «N из M»
 * и, если передан `onLoadMore`, кнопку догрузки; иначе просит уточнить фильтр.
 */
export function TruncationNotice({
  shown,
  total,
  hasMore,
  onLoadMore,
  loadingMore,
  className,
}: TruncationNoticeProps) {
  const knownTotal = typeof total === "number" && Number.isFinite(total);
  const truncated = knownTotal ? shown < total : Boolean(hasMore);
  if (!truncated) return null;

  const label = knownTotal
    ? `Показано ${shown} из ${total}`
    : `Показаны первые ${shown}`;

  return (
    <div
      className={`alert-warn text-xs ${className ?? ""}`}
      role="status"
    >
      <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
      <span className="flex-1">
        {label}
        {onLoadMore ? "." : " — уточните фильтр, чтобы увидеть остальные."}
      </span>
      {onLoadMore && (
        <button
          type="button"
          className="btn btn-ghost shrink-0"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? "Загрузка…" : "Загрузить ещё"}
        </button>
      )}
    </div>
  );
}
