/**
 * Панель макросов консоли + редактор.
 *
 * Макрос — именованная команда; клик по кнопке отправляет `command_text + "\n"`
 * в открытый WebSocket терминала (то же, что набрать команду и нажать Enter).
 * Список тянется с сервера (`listConsoleMacros`), поэтому панель одинакова на
 * всех устройствах пользователя; после CRUD дёргаем `refetch`, чтобы кнопки
 * обновились.
 *
 * Две группы: «Мои» (личные) и «Системные» (общедепартаментные). Системные
 * создаёт/правит только dep_admin — для остальных переключатель «системный»
 * недоступен, а кнопки правки/удаления системных скрыты.
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Pencil, Plus, Terminal, Trash2, Users } from "lucide-react";
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

/** Стабильная сортировка по `display_order`, затем по имени. */
function byOrder(a: ConsoleMacro, b: ConsoleMacro): number {
  if (a.display_order !== b.display_order) {
    return a.display_order - b.display_order;
  }
  return a.name.localeCompare(b.name);
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

  const [editing, setEditing] = useState<ConsoleMacro | null>(null);
  const [creating, setCreating] = useState(false);

  const macros = useMemo(() => macrosQ.data ?? [], [macrosQ.data]);
  const personal = useMemo(
    () => macros.filter((m) => !m.is_system).sort(byOrder),
    [macros],
  );
  const system = useMemo(
    () => macros.filter((m) => m.is_system).sort(byOrder),
    [macros],
  );

  async function remove(macro: ConsoleMacro) {
    try {
      await deleteConsoleMacro(macro.id);
      toast.success("Макрос удалён");
      macrosQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить макрос"));
    }
  }

  function renderGroup(
    title: string,
    icon: React.ReactNode,
    items: ConsoleMacro[],
    editable: boolean,
  ) {
    if (items.length === 0) return null;
    return (
      <div className="flex flex-col gap-1">
        <div className="text-[11px] text-dim flex items-center gap-1">
          {icon}
          {title}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {items.map((m) => (
            <div key={m.id} className="flex items-center">
              <button
                type="button"
                className="btn btn-sm"
                title={m.command_text}
                onClick={() => onRun(m.command_text)}
              >
                {m.name}
              </button>
              {editable && (
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
          ))}
        </div>
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
        <button
          type="button"
          className="btn btn-ghost btn-sm flex items-center gap-1"
          onClick={() => setCreating(true)}
        >
          <Plus className="w-3.5 h-3.5" />
          Новый
        </button>
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
          Макросов пока нет. Создайте первый кнопкой «Новый».
        </div>
      )}

      {renderGroup(
        "Мои",
        <Terminal className="w-3 h-3" />,
        personal,
        true,
      )}
      {renderGroup(
        "Системные",
        <Users className="w-3 h-3" />,
        system,
        canManageSystem,
      )}

      {(creating || editing) && (
        <MacroEditorModal
          macro={editing}
          canManageSystem={canManageSystem}
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
  onClose,
  onSaved,
}: {
  macro: ConsoleMacro | null;
  canManageSystem: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(macro?.name ?? "");
  const [commandText, setCommandText] = useState(macro?.command_text ?? "");
  const [order, setOrder] = useState<string>(
    macro ? String(macro.display_order) : "",
  );
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
    try {
      if (isEdit) {
        await updateConsoleMacro(macro.id, {
          name: name.trim(),
          command_text: commandText,
          display_order: orderNum,
          // Менять системность позволяем только тем, кто ей управляет.
          ...(canManageSystem ? { is_system: isSystem } : {}),
        });
        toast.success("Макрос обновлён");
      } else {
        await createConsoleMacro({
          name: name.trim(),
          command_text: commandText,
          display_order: orderNum,
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
