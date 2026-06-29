/**
 * Панель макросов консоли + редактор.
 *
 * Макрос — именованная команда; клик по кнопке отправляет `command_text + "\n"`
 * в открытый WebSocket терминала (то же, что набрать команду и нажать Enter).
 * Список тянется с сервера (`listConsoleMacros`), поэтому панель одинакова на
 * всех устройствах пользователя; после CRUD дёргаем `refetch`, чтобы кнопки
 * обновились.
 *
 * Два режима: «Использование» (по умолчанию) — только кнопки запуска,
 * разложенные по категориям «Системные» / «Собственные» и сворачиваемым
 * группам (`group_name`); «Редактирование» — CRUD с управляющими кнопками и
 * формой. Системные макросы создаёт/правит только dep_admin — для остальных
 * переключатель «системный» недоступен, а кнопки правки/удаления скрыты.
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ChevronDown,
  Pencil,
  Play,
  Plus,
  Terminal,
  Trash2,
  Users,
} from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import {
  createConsoleMacro,
  deleteConsoleMacro,
  listConsoleMacros,
  updateConsoleMacro,
  type ConsoleMacro,
} from "@/api/server/consoleMacros";

type Mode = "use" | "edit";

/** Стабильная сортировка по `display_order`, затем по имени. */
function byOrder(a: ConsoleMacro, b: ConsoleMacro): number {
  if (a.display_order !== b.display_order) {
    return a.display_order - b.display_order;
  }
  return a.name.localeCompare(b.name);
}

/** Нормализованное имя группы или `null`, если группа не задана. */
function groupKey(m: ConsoleMacro): string | null {
  const g = m.group_name?.trim();
  return g ? g : null;
}

/**
 * Раскладывает макросы категории на «без группы» (показываются плоско) и
 * именованные группы (сворачиваемые). Внутри всё отсортировано по `byOrder`.
 */
function splitGroups(items: ConsoleMacro[]): {
  ungrouped: ConsoleMacro[];
  groups: { name: string; items: ConsoleMacro[] }[];
} {
  const sorted = [...items].sort(byOrder);
  const ungrouped: ConsoleMacro[] = [];
  const map = new Map<string, ConsoleMacro[]>();
  for (const m of sorted) {
    const g = groupKey(m);
    if (!g) {
      ungrouped.push(m);
      continue;
    }
    const bucket = map.get(g);
    if (bucket) bucket.push(m);
    else map.set(g, [m]);
  }
  const groups = [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, list]) => ({ name, items: list }));
  return { ungrouped, groups };
}

export function ConsoleMacrosPanel({
  onRun,
}: {
  /** Выполнить команду в терминале (UI сам добавит "\n"). */
  onRun: (commandText: string) => void;
}) {
  const { persona } = usePersona();
  const toast = useToast();
  const macrosQ = useQuery(() => listConsoleMacros(), []);

  // Системные макросы создаёт/редактирует только dep_admin. Гейт клиентский —
  // финальное решение за backend (403 при попытке без права).
  const canManageSystem = persona.platform_role === "dep_admin";

  const [mode, setMode] = useState<Mode>("use");
  const [editing, setEditing] = useState<ConsoleMacro | null>(null);
  const [creating, setCreating] = useState(false);
  // Свёрнутые категории и группы; ключ группы — `${категория}/${имя}`.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  const macros = useMemo(() => macrosQ.data ?? [], [macrosQ.data]);
  const personal = useMemo(
    () => macros.filter((m) => !m.is_system),
    [macros],
  );
  const system = useMemo(
    () => macros.filter((m) => m.is_system),
    [macros],
  );
  const knownGroups = useMemo(() => {
    const set = new Set<string>();
    for (const m of macros) {
      const g = groupKey(m);
      if (g) set.add(g);
    }
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [macros]);

  const editMode = mode === "edit";

  function toggle(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function remove(macro: ConsoleMacro) {
    try {
      await deleteConsoleMacro(macro.id);
      toast.success("Макрос удалён");
      macrosQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить макрос"));
    }
  }

  function macroButton(m: ConsoleMacro, editable: boolean) {
    return (
      <div key={m.id} className="flex items-center">
        <button
          type="button"
          className="btn btn-sm"
          title={m.command_text}
          onClick={() => onRun(m.command_text)}
        >
          {m.name}
        </button>
        {editMode && editable && (
          <span className="flex items-center ml-0.5">
            <button
              type="button"
              className="btn btn-ghost btn-sm px-1"
              title="Изменить макрос"
              aria-label={`Изменить ${m.name}`}
              onClick={() => setEditing(m)}
            >
              <Pencil className="w-3 h-3" />
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm px-1"
              title="Удалить макрос"
              aria-label={`Удалить ${m.name}`}
              onClick={() => remove(m)}
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </span>
        )}
      </div>
    );
  }

  function renderCategory(
    catKey: string,
    title: string,
    icon: React.ReactNode,
    items: ConsoleMacro[],
    editable: boolean,
  ) {
    if (items.length === 0) return null;
    const { ungrouped, groups } = splitGroups(items);
    const catCollapsed = collapsed.has(catKey);
    return (
      <div className="flex flex-col gap-1">
        <button
          type="button"
          className="text-[11px] text-dim flex items-center gap-1 w-full text-left"
          onClick={() => toggle(catKey)}
        >
          <ChevronDown
            className={`w-3 h-3 transition-transform ${catCollapsed ? "-rotate-90" : ""}`}
          />
          {icon}
          {title}
        </button>
        {!catCollapsed && (
          <div className="flex flex-col gap-1.5 pl-3">
            {ungrouped.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {ungrouped.map((m) => macroButton(m, editable))}
              </div>
            )}
            {groups.map((g) => {
              const gKey = `${catKey}/${g.name}`;
              const gCollapsed = collapsed.has(gKey);
              return (
                <div key={gKey} className="flex flex-col gap-1">
                  <button
                    type="button"
                    className="text-[11px] text-dim flex items-center gap-1 w-full text-left"
                    onClick={() => toggle(gKey)}
                  >
                    <ChevronDown
                      className={`w-3 h-3 transition-transform ${gCollapsed ? "-rotate-90" : ""}`}
                    />
                    {g.name}
                    <span className="opacity-60">({g.items.length})</span>
                  </button>
                  {!gCollapsed && (
                    <div className="flex flex-wrap gap-1.5 pl-3">
                      {g.items.map((m) => macroButton(m, editable))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 surface-2 border border-token rounded p-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium flex items-center gap-1.5">
          <Terminal className="w-3.5 h-3.5 text-accent" />
          Макросы
        </div>
        <div className="flex items-center gap-1">
          <div className="flex items-center rounded border border-token overflow-hidden">
            <button
              type="button"
              className={`btn btn-sm px-2 ${editMode ? "btn-ghost" : "btn-primary"}`}
              aria-pressed={!editMode}
              onClick={() => setMode("use")}
              title="Только запуск макросов"
            >
              <Play className="w-3 h-3" />
              Использование
            </button>
            <button
              type="button"
              className={`btn btn-sm px-2 ${editMode ? "btn-primary" : "btn-ghost"}`}
              aria-pressed={editMode}
              onClick={() => setMode("edit")}
              title="Создание и правка макросов"
            >
              <Pencil className="w-3 h-3" />
              Редактирование
            </button>
          </div>
          {editMode && (
            <button
              type="button"
              className="btn btn-ghost btn-sm flex items-center gap-1"
              onClick={() => setCreating(true)}
            >
              <Plus className="w-3.5 h-3.5" />
              Новый
            </button>
          )}
        </div>
      </div>

      {macrosQ.loading && (
        <div className="text-xs text-dim">Загрузка макросов…</div>
      )}
      {macrosQ.error && (
        <div className="alert-danger text-xs">
          {apiErrMsg(macrosQ.error, "Макросы не загрузились")}
          <button className="btn btn-sm ml-2" onClick={() => macrosQ.refetch()}>
            Повторить
          </button>
        </div>
      )}

      {!macrosQ.loading && !macrosQ.error && macros.length === 0 && (
        <div className="text-xs text-dim">
          {editMode
            ? "Макросов пока нет. Создайте первый кнопкой «Новый»."
            : "Макросов пока нет. Перейдите в «Редактирование», чтобы создать."}
        </div>
      )}

      {renderCategory(
        "cat:system",
        "Системные",
        <Users className="w-3 h-3" />,
        system,
        canManageSystem,
      )}
      {renderCategory(
        "cat:personal",
        "Собственные",
        <Terminal className="w-3 h-3" />,
        personal,
        true,
      )}

      {(creating || editing) && (
        <MacroEditorModal
          macro={editing}
          canManageSystem={canManageSystem}
          knownGroups={knownGroups}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            macrosQ.refetch();
          }}
        />
      )}
    </div>
  );
}

/**
 * Модалка создания/редактирования макроса. `macro === null` → создание.
 * Переключатель «системный» доступен только при `canManageSystem`.
 */
function MacroEditorModal({
  macro,
  canManageSystem,
  knownGroups,
  onClose,
  onSaved,
}: {
  macro: ConsoleMacro | null;
  canManageSystem: boolean;
  knownGroups: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(macro?.name ?? "");
  const [commandText, setCommandText] = useState(macro?.command_text ?? "");
  const [order, setOrder] = useState<string>(
    macro ? String(macro.display_order) : "",
  );
  const [group, setGroup] = useState(macro?.group_name ?? "");
  const [isSystem, setIsSystem] = useState(macro?.is_system ?? false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isEdit = macro !== null;
  const canSubmit = name.trim().length > 0 && commandText.length > 0;

  async function submit() {
    if (pending || !canSubmit) return;
    setErr(null);
    setPending(true);
    const orderNum = order.trim() === "" ? undefined : Number(order);
    const trimmedGroup = group.trim();
    try {
      if (isEdit) {
        await updateConsoleMacro(macro.id, {
          name: name.trim(),
          command_text: commandText,
          display_order: orderNum,
          // Пустое поле очищает группу.
          group_name: trimmedGroup === "" ? null : trimmedGroup,
          // Менять системность позволяем только тем, кто ей управляет.
          ...(canManageSystem ? { is_system: isSystem } : {}),
        });
        toast.success("Макрос обновлён");
      } else {
        await createConsoleMacro({
          name: name.trim(),
          command_text: commandText,
          display_order: orderNum,
          ...(trimmedGroup ? { group_name: trimmedGroup } : {}),
          ...(canManageSystem && isSystem ? { is_system: true } : {}),
        });
        toast.success("Макрос создан");
      }
      onSaved();
    } catch (e) {
      setErr(apiErrMsg(e, "Не удалось сохранить макрос"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Terminal className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              {isEdit ? "Изменить макрос" : "Новый макрос"}
            </Dialog.Title>
          </div>

          <div className="modal-body flex flex-col gap-3">
            <Dialog.Description className="text-sm text-dim">
              Клик по кнопке макроса выполняет команду в открытой консольной
              сессии.
            </Dialog.Description>

            {err && <div className="alert-danger text-sm">{err}</div>}

            <label className="flex flex-col gap-1">
              <span className="field-label">Имя</span>
              <input
                className="field-input"
                value={name}
                disabled={pending}
                onChange={(e) => setName(e.target.value)}
                placeholder="например, df -h"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="field-label">Команда</span>
              <textarea
                className="field-input mono"
                value={commandText}
                disabled={pending}
                rows={3}
                onChange={(e) => setCommandText(e.target.value)}
                placeholder="команда, которая уйдёт в терминал"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="field-label">Группа (опционально)</span>
              <input
                className="field-input"
                value={group}
                disabled={pending}
                maxLength={128}
                list="console-macro-groups"
                onChange={(e) => setGroup(e.target.value)}
                placeholder="например, Диагностика"
              />
              {knownGroups.length > 0 && (
                <datalist id="console-macro-groups">
                  {knownGroups.map((g) => (
                    <option key={g} value={g} />
                  ))}
                </datalist>
              )}
            </label>

            <label className="flex flex-col gap-1">
              <span className="field-label">Порядок (опционально)</span>
              <input
                className="field-input"
                type="number"
                value={order}
                disabled={pending}
                onChange={(e) => setOrder(e.target.value)}
                placeholder="чем меньше, тем левее"
              />
            </label>

            {canManageSystem && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={isSystem}
                  disabled={pending}
                  onChange={(e) => setIsSystem(e.target.checked)}
                />
                <span>
                  Системный (виден всему отделу; правится только администратором
                  департамента)
                </span>
              </label>
            )}
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={pending}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={submit}
              disabled={pending || !canSubmit}
            >
              {isEdit ? "Сохранить" : "Создать"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
