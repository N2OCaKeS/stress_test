/**
 * Модалка выдачи прямого гранта доступа к учётке (per-account ACL).
 *
 * Гранты добавляют доступ к одной учётке поверх ролей отдела. Выбор
 * пользователя двухрежимный: dep_admin / account_admin берут пикер из списка
 * юзеров отдела (`listUsersByDepartment`), сервис-админ без dep-admin прав
 * вводит username вручную с точечным резолвом через `resolveUser` — бэкенд
 * list-юзеров такому актору отдаёт 403. Сырой `usr_*`/`bot_*` id принимается в
 * обоих режимах. Набор действий — чекбоксы (минимум один); при «изменить грант»
 * текущие действия префиллятся, повторная выдача заменяет набор целиком.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ShieldPlus, X } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { listUsersByDepartment, resolveUser } from "@/api/auth/users";
import {
  ACCOUNT_ACL_ACTIONS,
  type AccountAclAction,
  type AccountAclGrant,
} from "@/api/server/types";

export function AccountAclModal({
  canPick,
  pickerDeptId,
  editing,
  onClose,
  onSubmit,
}: {
  /** dep_admin / account_admin — доступен пикер юзеров отдела. */
  canPick: boolean;
  /** Отдел учётки — источник списка юзеров для пикера. */
  pickerDeptId: string | null;
  /** Существующий грант для редактирования (префилл) либо null для новой выдачи. */
  editing: AccountAclGrant | null;
  onClose: () => void;
  onSubmit: (body: {
    user_id: string;
    actions: AccountAclAction[];
  }) => void | Promise<void>;
}) {
  const isEdit = editing !== null;
  const pickEnabled = canPick && !!pickerDeptId && !isEdit;
  const usersQ = useQuery(
    () => listUsersByDepartment(pickerDeptId as string, { limit: 200 }),
    [pickerDeptId],
    { enabled: pickEnabled },
  );
  const userItems = usersQ.data?.items ?? [];
  const usePicker =
    pickEnabled && !usersQ.loading && !usersQ.error && userItems.length > 0;

  const [input, setInput] = useState(editing?.user_id ?? "");
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [actions, setActions] = useState<Set<string>>(
    () => new Set(editing?.actions ?? ["view"]),
  );
  const [submitting, setSubmitting] = useState(false);
  const [resolveErr, setResolveErr] = useState<string | null>(null);

  const filteredUsers = filter.trim()
    ? userItems.filter((u) =>
        u.username.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : userItems;

  function toggleAction(a: AccountAclAction) {
    setActions((prev) => {
      const next = new Set(prev);
      if (next.has(a)) next.delete(a);
      else next.add(a);
      return next;
    });
  }

  // В режиме редактирования пользователь зафиксирован — выбирать заново нельзя,
  // только менять набор действий.
  const userValid = isEdit
    ? true
    : usePicker
      ? !!selectedId
      : input.trim().length > 0;
  const valid = userValid && actions.size > 0;

  // username/id → usr_*|bot_*: пикер уже даёт id; сырой id пропускаем как есть;
  // иначе точечно резолвим username (виден свой отдел).
  async function resolveUserId(): Promise<string | null> {
    if (isEdit) return editing.user_id;
    if (usePicker) return selectedId || null;
    const raw = input.trim();
    if (!raw) return null;
    if (raw.startsWith("usr_") || raw.startsWith("bot_")) return raw;
    try {
      const r = await resolveUser(raw);
      return r.user_id;
    } catch {
      return null;
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    setSubmitting(true);
    setResolveErr(null);
    const userId = await resolveUserId();
    if (!userId) {
      setResolveErr(
        "Пользователь не найден в вашем отделе. Можно ввести id (usr_… / bot_…) напрямую.",
      );
      setSubmitting(false);
      return;
    }
    // Отдаём действия в каноническом порядке каталога.
    const ordered = ACCOUNT_ACL_ACTIONS.filter((a) =>
      actions.has(a.action),
    ).map((a) => a.action);
    try {
      await Promise.resolve(onSubmit({ user_id: userId, actions: ordered }));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !submitting && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onInteractOutside={(e) => submitting && e.preventDefault()}
          onEscapeKeyDown={(e) => submitting && e.preventDefault()}
        >
          <div className="modal-header">
            <ShieldPlus className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              {isEdit ? "Изменить грант" : "Выдать доступ к учётке"}
            </Dialog.Title>
          </div>

          <form onSubmit={handleSubmit}>
            <div className="modal-body flex flex-col gap-3">
              <Dialog.Description className="text-sm text-dim">
                Грант добавляет доступ к этой учётке конкретному пользователю
                поверх ролей отдела.
                {isEdit
                  ? " Сохранение заменяет набор действий целиком."
                  : usePicker
                    ? " Выберите пользователя из списка отдела."
                    : " Введите username — он сопоставится с id (виден ваш отдел). Сырой id (usr_… / bot_…) тоже принимается."}
              </Dialog.Description>

              {isEdit ? (
                <label className="flex flex-col gap-1 text-sm">
                  <span className="field-label">пользователь</span>
                  <input
                    className="field-input mono"
                    value={editing.user_id}
                    readOnly
                    disabled
                  />
                </label>
              ) : usePicker ? (
                <label className="flex flex-col gap-1 text-sm">
                  <span className="field-label">пользователь *</span>
                  <input
                    className="field-input"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="поиск по username…"
                    aria-label="поиск пользователя"
                  />
                  <select
                    className="field-input mt-1"
                    value={selectedId}
                    onChange={(e) => {
                      setSelectedId(e.target.value);
                      setResolveErr(null);
                    }}
                    aria-label="пользователь"
                    required
                    size={Math.min(8, Math.max(3, filteredUsers.length))}
                  >
                    {filteredUsers.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username}
                      </option>
                    ))}
                  </select>
                  {resolveErr && (
                    <span className="text-[11px] text-danger">{resolveErr}</span>
                  )}
                </label>
              ) : (
                <label className="flex flex-col gap-1 text-sm">
                  <span className="field-label">username *</span>
                  <input
                    className="field-input"
                    value={input}
                    onChange={(e) => {
                      setInput(e.target.value);
                      setResolveErr(null);
                    }}
                    maxLength={64}
                    required
                    placeholder="username, usr_… или bot_…"
                  />
                  {resolveErr && (
                    <span className="text-[11px] text-danger">{resolveErr}</span>
                  )}
                </label>
              )}

              <div className="flex flex-col gap-1 text-sm">
                <span className="field-label">действия * (минимум одно)</span>
                <div className="flex flex-col gap-0.5 border border-token rounded p-2">
                  {ACCOUNT_ACL_ACTIONS.map(({ action, label }) => (
                    <label
                      key={action}
                      className="inline-flex items-center gap-2 text-sm py-0.5"
                    >
                      <input
                        type="checkbox"
                        checked={actions.has(action)}
                        onChange={() => toggleAction(action)}
                        aria-label={label}
                      />
                      <span>{label}</span>
                      <span className="text-[11px] text-dim mono">{action}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn flex items-center gap-1"
                onClick={onClose}
                disabled={submitting}
              >
                <X className="w-4 h-4" /> Отмена
              </button>
              <button
                type="submit"
                className="btn btn-primary flex items-center gap-1"
                disabled={submitting || !valid}
              >
                <ShieldPlus className="w-4 h-4" />
                {submitting ? "Сохраняем…" : isEdit ? "Сохранить" : "Выдать"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
