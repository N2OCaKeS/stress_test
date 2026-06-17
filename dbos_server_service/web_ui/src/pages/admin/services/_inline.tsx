import { useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Lock, Plug, Plus, type LucideIcon } from "lucide-react";
import { HelpTooltip } from "@/components/ui/HelpTooltip";

/**
 * Inline "service not wired to UI" card — used by admin tabs whose backing
 * service has no API surface in this UI yet (server_service, secret_service,
 * server_worker subsections, log rules/retention etc.). Keeps the visual rhythm
 * of the inline editor but doesn't show fabricated данные.
 */
export function NotWiredInline({
  service,
  endpoints,
}: {
  service: string;
  endpoints?: string[];
}) {
  return (
    <div className="card flex items-start gap-3 m-4 max-w-2xl">
      <Plug className="w-6 h-6 text-warn shrink-0 mt-1" />
      <div className="flex-1">
        <div className="text-sm font-semibold mb-1">
          {service} ещё не подключён к UI
        </div>
        <div className="text-xs text-dim">
          В live-режиме здесь будут реальные данные. Пока — заглушка.
        </div>
        {endpoints && endpoints.length > 0 && (
          <div className="mt-2 text-[11px] text-dim">
            <div className="mb-1">Ожидаемые endpoint&apos;ы:</div>
            <ul className="space-y-0.5">
              {endpoints.map((ep) => (
                <li key={ep} className="mono">
                  {ep}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Banner для зон, где UI раньше предлагал CRUD service-ролей для loging_service.
 * Фактически loging_service гейтится только платформенными ролями
 * (`loging_admin` / `loging_reader`); service-роли в этой связке guard'ами
 * игнорируются. Используется в ServicesCatalog (deep-detail), DeptRolesNav
 * (под именем сервиса) и ServiceRolesCard (полная страница).
 */
export function LogingPlatformRoleBanner({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`flex items-start gap-2 border border-token rounded text-warn ${compact ? "text-[11px] p-2" : "text-xs p-3"}`}
      style={{
        background: "color-mix(in srgb, var(--warn) 12%, transparent)",
        borderColor: "var(--warn)",
      }}
    >
      <AlertTriangle
        className={`${compact ? "w-3 h-3" : "w-4 h-4"} shrink-0 mt-0.5`}
      />
      <div className="flex-1">
        Доступ к <span className="mono">loging_service</span> управляется только
        платформенными ролями <span className="mono">loging_admin</span> /{" "}
        <span className="mono">loging_reader</span>. Создавать service-роли
        здесь нельзя — guards внутри loging_service их игнорируют. Назначайте
        платформенную роль через карточку пользователя
        (<span className="mono">/users/&lt;id&gt;</span> → Edit profile →
        platform_role) или при создании юзера.
      </div>
    </div>
  );
}

/**
 * Shared inline-editor scaffolding for /admin service items.
 *
 * Layout (no Radix Dialog, no sub-routes):
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ header (label · hint · top-right actions)               │
 *   ├──────────────────┬──────────────────────────────────────┤
 *   │ list (280-320px) │ detail / form / empty state          │
 *   │  scrollable      │  scrollable                          │
 *   └──────────────────┴──────────────────────────────────────┘
 *
 * URL state: `?action=new` for create form, `?id=<id>` for detail/edit.
 * No selection → empty state with "Создать" hint (if RBAC allows).
 */

export type InlineMode = "list" | "detail" | "edit" | "new";

export interface ListRowProps<T> {
  item: T;
  active: boolean;
  onSelect: () => void;
}

export interface InlineEditorProps<T> {
  title: string;
  icon: LucideIcon;
  /** Short subtitle/hint shown under the title. */
  hint?: string;
  items: T[];
  /**
   * List is still loading. When true, the left pane shows a spinner instead of
   * the empty-state — otherwise an in-flight fetch reads as «Список пуст.».
   */
  loading?: boolean;
  /**
   * List failed to load. When set, the left pane shows the error (and optional
   * retry) instead of the empty-state, so a failed fetch isn't mistaken for «no
   * items».
   */
  error?: string | null;
  /** Retry handler shown next to `error`. */
  onRetry?: () => void;
  /** Stable ID accessor. */
  getId: (item: T) => string;
  /** Renders one row in the left list. */
  renderRow: (props: ListRowProps<T>) => ReactNode;
  /** Renders the detail/edit pane for the selected item. */
  renderDetail: (item: T, opts: { editing: boolean; onClose: () => void }) => ReactNode;
  /** Renders the create form. Receives onClose to back out. */
  renderCreate?: (onClose: () => void) => ReactNode;
  /** True → user can create / edit / delete. False → list + read-only detail. */
  canEdit: boolean;
  /** Optional read-only banner text shown above the list. */
  readonlyNote?: string;
  /** Empty-state text when no item selected. */
  emptyHint?: string;
  /** Optional list-header row (count / filter, etc.). */
  listHeader?: ReactNode;
  /** Optional list-footer row (pagination, "load more", etc.). */
  listFooter?: ReactNode;
  /** Width of the left list pane (Tailwind class fragment). */
  listWidth?: string;
}

/**
 * Reads `?id=...&action=...` from the URL and returns helpers to mutate them.
 * Keeps URL-state co-located so the parent page stays small.
 */
export function useInlineState() {
  const [params, setParams] = useSearchParams();
  const id = params.get("id");
  const action = params.get("action"); // "new" | "edit" | null
  const mode: InlineMode = action === "new"
    ? "new"
    : action === "edit" && id
      ? "edit"
      : id
        ? "detail"
        : "list";

  function select(nextId: string | null) {
    const next = new URLSearchParams(params);
    if (nextId) next.set("id", nextId);
    else next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function startCreate() {
    const next = new URLSearchParams(params);
    next.set("action", "new");
    next.delete("id");
    setParams(next, { replace: true });
  }
  function startEdit(targetId: string) {
    const next = new URLSearchParams(params);
    next.set("id", targetId);
    next.set("action", "edit");
    setParams(next, { replace: true });
  }
  function close() {
    const next = new URLSearchParams(params);
    next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }

  return { id, action, mode, select, startCreate, startEdit, close };
}

export function InlineEditor<T>({
  title,
  icon: Icon,
  hint,
  items,
  loading,
  error,
  onRetry,
  getId,
  renderRow,
  renderDetail,
  renderCreate,
  canEdit,
  readonlyNote,
  emptyHint,
  listHeader,
  listFooter,
  listWidth = "w-[300px]",
}: InlineEditorProps<T>) {
  const { id, mode: rawMode, select, startCreate, close } = useInlineState();

  // RBAC gate — `?action=new` / `?action=edit` in URL must be ignored for read-only personas.
  // Without this, anyone can hand-craft `/admin/<item>?action=new` and bypass the create UI.
  const mode: InlineMode =
    !canEdit && (rawMode === "new" || rawMode === "edit")
      ? rawMode === "new"
        ? "new"
        : "edit"
      : rawMode;
  const gated = !canEdit && (rawMode === "new" || rawMode === "edit");

  const selected = useMemo(
    () => (id ? items.find((it) => getId(it) === id) : undefined),
    [id, items, getId],
  );

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Icon className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">{title}</h1>
          {hint && <div className="text-xs text-dim">{hint}</div>}
        </div>
        <div className="text-xs text-dim mr-2">
          {loading && items.length === 0 ? "…" : `${items.length} записей`}
        </div>
        {canEdit && renderCreate && (
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={startCreate}
          >
            <Plus className="w-4 h-4" /> Создать
          </button>
        )}
      </div>

      {readonlyNote && (
        <div className="readonly-bar shrink-0">
          <span className="ro-label">read-only</span>
          <span>{readonlyNote}</span>
        </div>
      )}

      {/* Body — list + detail */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {/* List pane */}
        <aside className={`${listWidth} shrink-0 border-r border-token surface flex flex-col min-h-0`}>
          {listHeader && (
            <div className="px-3 py-2 border-b border-token shrink-0">{listHeader}</div>
          )}
          <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-0.5">
            {loading && items.length === 0 ? (
              <div className="text-xs text-dim px-3 py-6 text-center">
                Загрузка…
              </div>
            ) : error ? (
              <div className="alert-danger text-xs m-1 flex items-center justify-between gap-2">
                <span>{error}</span>
                {onRetry && (
                  <button className="btn btn-ghost btn-sm" onClick={onRetry}>
                    Повторить
                  </button>
                )}
              </div>
            ) : items.length === 0 ? (
              <div className="text-xs text-dim px-3 py-6 text-center">
                Список пуст.
              </div>
            ) : null}
            {items.map((it) => {
              const itemId = getId(it);
              return (
                <div key={itemId}>
                  {renderRow({
                    item: it,
                    active: itemId === id,
                    onSelect: () => select(itemId),
                  })}
                </div>
              );
            })}
          </div>
          {listFooter && (
            <div className="px-3 py-2 border-t border-token shrink-0">
              {listFooter}
            </div>
          )}
        </aside>

        {/* Detail pane */}
        <section className="flex-1 min-w-0 min-h-0 overflow-y-auto">
          {gated && (
            <DetailWrap onBack={close} title="Только чтение">
              <ReadonlyAccessCard
                action={rawMode === "new" ? "new" : "edit"}
                hint={readonlyNote}
              />
            </DetailWrap>
          )}
          {!gated && mode === "new" && canEdit && renderCreate && (
            <DetailWrap onBack={close} title="Создание">
              {renderCreate(close)}
            </DetailWrap>
          )}
          {!gated && mode !== "new" && selected && (
            <DetailWrap key={getId(selected)} onBack={close} title={getId(selected)}>
              {renderDetail(selected, { editing: mode === "edit" && canEdit, onClose: close })}
            </DetailWrap>
          )}
          {!gated && mode !== "new" && !selected && (
            <div className="h-full flex items-center justify-center p-8">
              <div className="empty-card max-w-md">
                <Icon className="w-10 h-10 mx-auto text-dim mb-3" />
                <div className="text-sm text-dim">
                  {emptyHint ?? "Выберите запись слева для просмотра."}
                </div>
                {canEdit && renderCreate && (
                  <button
                    className="btn btn-primary mt-4 inline-flex items-center gap-1"
                    onClick={startCreate}
                  >
                    <Plus className="w-4 h-4" /> Создать
                  </button>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ReadonlyAccessCard({
  action,
  hint,
}: {
  action: "new" | "edit";
  hint?: string;
}) {
  return (
    <div className="empty-card max-w-md mx-auto mt-8">
      <Lock className="w-10 h-10 mx-auto text-dim mb-3" />
      <div className="text-sm font-medium mb-1">
        Доступ только для чтения
      </div>
      <div className="text-xs text-dim">
        {action === "new"
          ? "Создание записей этой персоной запрещено."
          : "Редактирование этой записи персоной запрещено."}
      </div>
      {hint && (
        <div className="text-xs text-dim mt-2 italic">{hint}</div>
      )}
    </div>
  );
}

function DetailWrap({
  onBack,
  title,
  children,
}: {
  onBack: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="p-5 flex flex-col gap-4 min-h-full">
      <div className="flex items-center gap-2 shrink-0">
        <button className="btn btn-ghost flex items-center gap-1" onClick={onBack}>
          <ArrowLeft className="w-4 h-4" /> Назад
        </button>
        <div className="text-sm text-dim truncate mono">{title}</div>
      </div>
      <div>{children}</div>
    </div>
  );
}

/* Small shared building blocks for inline detail/form panes. */

export function FormRow({
  label,
  hint,
  help,
  children,
}: {
  label: string;
  hint?: string;
  /** Короткая справка, разворачивается из иконки «?» рядом с подписью. */
  help?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-dim text-xs flex items-center gap-1">
        {label}
        {help && <HelpTooltip text={help} label={`Справка: ${label}`} />}
      </span>
      {children}
      {hint && <span className="text-[11px] text-dim">{hint}</span>}
    </label>
  );
}

export function StatRow({
  k,
  v,
}: {
  k: string;
  v: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span className="min-w-0 break-words">{v}</span>
    </div>
  );
}
