/**
 * Универсальный выпадающий список (single/multi, с опциональным поиском).
 * Список рендерится порталом в `document.body` и позиционируется по
 * координатам триггера (тот же приём, что и `HelpTooltip`), поэтому не
 * обрезается overflow родительских контейнеров — таблицы с горизонтальным
 * скроллом, карточки с `overflow-hidden` и т.п. Позиция клэмпится по краям
 * viewport и умеет разворачиваться вверх, если снизу не хватает места.
 *
 * `value`/`onChange` типизированы по `mode`: single отдаёт строку (пустая —
 * ничего не выбрано), multi — `Set<string>` (пустой набор трактуется вызывающим
 * кодом как "все значения проходят", здесь это только про отображение и выбор).
 *
 * `data-app-portal` на корне попапа — маркер для `Modal.tsx`: Radix Dialog
 * закрывается на любой pointerdown вне своего DOM-поддерева, а портал рендерится
 * в `document.body`, то есть формально «снаружи» модалки — без этого маркера
 * клик по опции внутри модалки не выбирал бы значение, а просто закрывал
 * (или ломал) модалку раньше, чем успевал сработать `onClick` опции.
 *
 * `aria-label` на кнопке-триггере — обязателен, не косметика: по всему
 * проекту `Dropdown` почти всегда оборачивают в `<label><span>подпись</span>
 * <Dropdown/></label>`. `<label>` — native host language text alternative
 * для «labelable»-элементов (кнопка входит в их число), и per accname spec
 * это ПРИОРИТЕТНЕЕ, чем «name from content» — без явного `aria-label` на
 * кнопке скринридер озвучивал бы только текст подписи поля («Версия ОС»),
 * а не реально выбранное значение, независимо от того, что показано визуально.
 * `aria-label`/`aria-labelledby` стоят в accname-алгоритме выше native-label
 * ассоциации, поэтому перебивают её и возвращают кнопке её собственное имя.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";

export interface DropdownOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface DropdownCommonProps {
  options: DropdownOption[];
  /** подпись перед текущим значением на кнопке-триггере, например "Стенд" */
  label?: string;
  /** текст на кнопке, когда ничего не выбрано */
  placeholder?: string;
  searchable?: boolean;
  /** высота скролл-области списка опций, px */
  maxHeight?: number;
  disabled?: boolean;
  className?: string;
}

interface DropdownSingleProps extends DropdownCommonProps {
  mode: "single";
  value: string;
  onChange: (value: string) => void;
}

interface DropdownMultiProps extends DropdownCommonProps {
  mode: "multi";
  value: Set<string>;
  onChange: (value: Set<string>) => void;
}

export type DropdownProps = DropdownSingleProps | DropdownMultiProps;

type Pos = { top: number; left: number; width: number };

const DEFAULT_MAX_HEIGHT = 280;
const MIN_WIDTH = 200;
const GAP = 4;

export function Dropdown(props: DropdownProps) {
  const {
    options,
    label,
    placeholder = "Все",
    searchable = true,
    maxHeight = DEFAULT_MAX_HEIGHT,
    disabled,
    className,
  } = props;

  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const [query, setQuery] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const r = trigger.getBoundingClientRect();
    const width = Math.max(r.width, MIN_WIDTH);
    const maxLeft = Math.max(GAP, window.innerWidth - width - GAP);
    const left = Math.min(r.left, maxLeft);

    // Первый проход — попап ещё не в DOM, реальной высоты не знаем, берём
    // грубую оценку только чтобы решить, вверх или вниз, и не мигнуть не
    // туда. Как только попап смонтирован, второй эффект ниже подменяет
    // `top` точным измеренным `offsetHeight` — иначе при коротком списке
    // (оценка почти всегда больше факта) поповер открытый вверх повисает
    // с отступом от поля, будто оторван от триггера.
    const measured = popRef.current?.offsetHeight;
    const estimatedHeight = measured ?? Math.min(maxHeight, 320) + (searchable ? 40 : 0) + 44;
    const spaceBelow = window.innerHeight - r.bottom;
    const openAbove = spaceBelow < estimatedHeight && r.top > spaceBelow;
    const top = openAbove ? Math.max(GAP, r.top - estimatedHeight - GAP) : r.bottom + GAP;

    setPos({ top, left, width });
  }, [maxHeight, searchable]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  // Коррекция вторым проходом реальной высотой попапа (см. комментарий в
  // `place`) — срабатывает сразу после того, как попап впервые попал в DOM
  // по оценочной позиции, и успевает отработать до отрисовки кадра.
  useLayoutEffect(() => {
    if (open && pos) place();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, !!pos]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    return options.filter((o) => !q || o.label.toLowerCase().includes(q))
      .sort((a, b) => a.label.localeCompare(b.label, "ru", { numeric: true, sensitivity: "base" }));
  }, [options, query]);

  const triggerText = useMemo(() => {
    if (props.mode === "single") {
      const opt = options.find((o) => o.value === props.value);
      return opt ? opt.label : placeholder;
    }
    const size = props.value.size;
    if (size === 0) return placeholder;
    if (size === 1) {
      const only = options.find((o) => props.value.has(o.value));
      if (only) return only.label;
    }
    return `Выбрано: ${size}`;
  }, [props.mode, props.value, options, placeholder]);

  const hasValue = props.mode === "single" ? props.value !== "" : props.value.size > 0;

  function selectSingle(value: string) {
    if (props.mode !== "single") return;
    props.onChange(value === props.value ? "" : value);
    setOpen(false);
  }

  function toggleMulti(value: string) {
    if (props.mode !== "multi") return;
    const next = new Set(props.value);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    props.onChange(next);
  }

  return (
    <div className={`inline-block ${className ?? ""}`}>
      <button
        type="button"
        ref={triggerRef}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={label ? `${label}: ${triggerText}` : triggerText}
        className={`btn btn-sm inline-flex items-center gap-1.5 ${hasValue ? "btn-primary" : ""}`}
        onClick={() => setOpen((v) => !v)}
      >
        {label && <span className={hasValue ? "" : "text-dim"}>{label}:</span>}
        <span className="truncate max-w-[180px]">{triggerText}</span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0" />
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            role="listbox"
            data-app-portal
            style={{
              position: "fixed",
              top: pos.top,
              left: pos.left,
              width: pos.width,
              // Radix Dialog (modal) ставит `pointer-events: none` на весь
              // <body> пока открыт, восстанавливая `auto` только на своём
              // собственном DOM-поддереве `Dialog.Content` — наш попап,
              // будучи порталом в body (DOM-сиблинг, а не потомок), без этого
              // наследовал бы `none` и стал бы некликабельным: клик проходил
              // бы «сквозь» опцию на то, что визуально позади (оверлей/сама
              // модалка), это читалось бы как «внешний» клик для
              // собственного outside-click хендлера попапа и просто закрывало
              // список без выбора.
              pointerEvents: "auto",
            }}
            className="z-[1000] surface border border-token rounded shadow-lg p-2 flex flex-col gap-1.5"
          >
            {searchable && (
              <div className="flex items-center gap-1.5 surface-2 border border-token rounded px-2 py-1 shrink-0">
                <Search className="w-3.5 h-3.5 text-dim shrink-0" />
                <input
                  autoFocus
                  className="bg-transparent outline-none flex-1 text-xs min-w-0"
                  placeholder="Поиск…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            )}
            {props.mode === "multi" && options.length > 0 && (
              <div className="flex items-center justify-between gap-2 pb-1.5 border-b border-token shrink-0">
                <button type="button" className="btn btn-ghost btn-sm text-[11px]" onClick={() => props.onChange(new Set())}>
                  Сбросить
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm text-[11px]"
                  onClick={() => props.onChange(new Set(filteredOptions.filter((o) => !o.disabled).map((o) => o.value)))}
                >
                  Выбрать все
                </button>
              </div>
            )}
            <div className="grid gap-0.5 overflow-y-auto" style={{ maxHeight }}>
              {filteredOptions.length === 0 && <div className="text-xs text-dim px-1.5 py-1">Нет вариантов</div>}
              {props.mode === "multi"
                ? filteredOptions.map((opt) => (
                    <label key={opt.value} className="flex items-center gap-2 px-1.5 py-1 rounded hover-bg cursor-pointer text-xs">
                      <input type="checkbox" disabled={opt.disabled} checked={props.value.has(opt.value)} onChange={() => toggleMulti(opt.value)} />
                      <span className="truncate">{opt.label}</span>
                    </label>
                  ))
                : filteredOptions.map((opt) => {
                    const active = opt.value === props.value;
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        role="option"
                        disabled={opt.disabled}
                        aria-selected={active}
                        className={`flex items-center gap-2 px-1.5 py-1 rounded hover-bg text-xs text-left ${active ? "surface-2" : ""}`}
                        onClick={() => selectSingle(opt.value)}
                      >
                        <Check className={`w-3.5 h-3.5 shrink-0 ${active ? "text-accent" : "opacity-0"}`} />
                        <span className="truncate">{opt.label}</span>
                      </button>
                    );
                  })}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
