/**
 * Модалка результата ревизии атрибутов (`users/inventory` → `task.result.diffs`).
 *
 * Показывает построчно расхождения по уже привязанным аккаунтам: для каждого
 * поля (`has_sudo`/`unix_groups`/`shell`) — было (expected, приглушённо/
 * зачёркнуто) → стало (found, акцентно). Для групп добавленные подсвечиваются
 * зелёным, убранные — красным с зачёркиванием. У каждого поля чекбокс (по
 * умолчанию отмечен); «Применить к БД» собирает отмеченные поля со значениями
 * `found` и шлёт `adoptFromHost`.
 *
 * Незнакомые OS-юзеры (discovered) сюда не попадают — backend их в `diffs` не
 * присылает, это отдельный сценарий.
 */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ScanSearch, Check, ShieldCheck, User } from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { adoptFromHost } from "@/api/server/accounts";
import type { RevisionAccountDiff } from "@/api/server/types";

type FieldKey = "has_sudo" | "unix_groups" | "shell";

/** Набор отмеченных полей по одному аккаунту (account_id → field → bool). */
type Checked = Record<string, Partial<Record<FieldKey, boolean>>>;

function initialChecked(diffs: RevisionAccountDiff[]): Checked {
  const out: Checked = {};
  for (const d of diffs) {
    const fields: Partial<Record<FieldKey, boolean>> = {};
    if (d.fields.has_sudo) fields.has_sudo = true;
    if (d.fields.unix_groups) fields.unix_groups = true;
    if (d.fields.shell) fields.shell = true;
    out[d.account_id] = fields;
  }
  return out;
}

function adoptError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав для применения ревизии.";
    if (e.status === 404) return "Аккаунт не найден (возможно, уже удалён).";
    if (e.status === 422) {
      return apiErrMsg(
        e,
        "Нечего применять или невалидные значения (unix_groups).",
      );
    }
  }
  return apiErrMsg(e, "Не удалось применить ревизию");
}

export function RevisionDiffModal({
  open,
  serverId,
  serverName,
  diffs,
  onClose,
  onApplied,
}: {
  open: boolean;
  serverId: string;
  serverName: string;
  diffs: RevisionAccountDiff[];
  onClose: () => void;
  /** После успешного adopt одного аккаунта — refetch наверху. */
  onApplied: () => void;
}) {
  const toast = useToast();
  // Применённые аккаунты убираем из выдачи, не закрывая модалку.
  const [appliedIds, setAppliedIds] = useState<string[]>([]);
  const [checked, setChecked] = useState<Checked>(() => initialChecked(diffs));
  const [busy, setBusy] = useState<string | null>(null);

  // diffs приезжают асинхронно (после поллинга задачи) уже после монтирования
  // модалки — пересобираем отметки и сбрасываем применённое под новый набор.
  const diffsKey = useMemo(
    () => diffs.map((d) => d.account_id).join(","),
    [diffs],
  );
  useEffect(() => {
    setChecked(initialChecked(diffs));
    setAppliedIds([]);
    // diffsKey покрывает смену состава diffs; initialChecked чистая.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diffsKey]);

  const visible = useMemo(
    () => diffs.filter((d) => !appliedIds.includes(d.account_id)),
    [diffs, appliedIds],
  );

  function toggle(accountId: string, field: FieldKey) {
    setChecked((prev) => ({
      ...prev,
      [accountId]: {
        ...prev[accountId],
        [field]: !prev[accountId]?.[field],
      },
    }));
  }

  async function apply(diff: RevisionAccountDiff) {
    if (busy) return;
    const marks = checked[diff.account_id] ?? {};
    const body: {
      server_id: string;
      has_sudo?: boolean;
      unix_groups?: string[];
      shell?: string | null;
    } = { server_id: serverId };
    if (marks.has_sudo && diff.fields.has_sudo) {
      body.has_sudo = diff.fields.has_sudo.found;
    }
    if (marks.unix_groups && diff.fields.unix_groups) {
      body.unix_groups = diff.fields.unix_groups.found;
    }
    if (marks.shell && diff.fields.shell) {
      body.shell = diff.fields.shell.found;
    }
    const anyField =
      body.has_sudo !== undefined ||
      body.unix_groups !== undefined ||
      body.shell !== undefined;
    if (!anyField) {
      toast.error("Отметьте хотя бы одно поле для применения.");
      return;
    }
    setBusy(diff.account_id);
    try {
      await adoptFromHost(diff.account_id, body);
      toast.success(`Ревизия применена: ${diff.login}`);
      setAppliedIds((ids) => [...ids, diff.account_id]);
      onApplied();
    } catch (e) {
      toast.error(adoptError(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" style={{ maxWidth: 640 }}>
          <div className="modal-header">
            <ScanSearch className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Ревизия атрибутов — {serverName}
            </Dialog.Title>
          </div>
          <div className="modal-body flex flex-col gap-4">
            <Dialog.Description className="text-xs text-dim">
              Расхождения по привязанным аккаунтам: было (БД) → стало (сервер).
              Отметьте поля и примените значения с сервера в БД.
            </Dialog.Description>

            {visible.length === 0 ? (
              <div className="text-sm text-dim text-center py-6">
                {diffs.length === 0
                  ? "Расхождений нет — атрибуты в БД совпадают с сервером."
                  : "Все расхождения применены."}
              </div>
            ) : (
              visible.map((diff) => (
                <DiffCard
                  key={diff.account_id}
                  diff={diff}
                  marks={checked[diff.account_id] ?? {}}
                  busy={busy === diff.account_id}
                  disabledAll={busy !== null}
                  onToggle={(f) => toggle(diff.account_id, f)}
                  onApply={() => apply(diff)}
                />
              ))
            )}
          </div>
          <div className="modal-footer">
            <Dialog.Close asChild>
              <button type="button" className="btn">
                Закрыть
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DiffCard({
  diff,
  marks,
  busy,
  disabledAll,
  onToggle,
  onApply,
}: {
  diff: RevisionAccountDiff;
  marks: Partial<Record<FieldKey, boolean>>;
  busy: boolean;
  disabledAll: boolean;
  onToggle: (field: FieldKey) => void;
  onApply: () => void;
}) {
  const anyChecked =
    !!marks.has_sudo || !!marks.unix_groups || !!marks.shell;
  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <User className="w-4 h-4 text-accent shrink-0" />
        <span className="text-sm mono flex-1 min-w-0 truncate">
          {diff.login}
        </span>
        <span className="badge mono">{diff.account_id}</span>
      </div>

      <div className="flex flex-col gap-2">
        {diff.fields.has_sudo && (
          <FieldRow
            field="has_sudo"
            label="has_sudo"
            checked={!!marks.has_sudo}
            disabled={disabledAll}
            onToggle={() => onToggle("has_sudo")}
          >
            <BoolDiff
              expected={diff.fields.has_sudo.expected}
              found={diff.fields.has_sudo.found}
            />
          </FieldRow>
        )}
        {diff.fields.unix_groups && (
          <FieldRow
            field="unix_groups"
            label="unix_groups"
            checked={!!marks.unix_groups}
            disabled={disabledAll}
            onToggle={() => onToggle("unix_groups")}
          >
            <GroupsDiff
              expected={diff.fields.unix_groups.expected}
              found={diff.fields.unix_groups.found}
            />
          </FieldRow>
        )}
        {diff.fields.shell && (
          <FieldRow
            field="shell"
            label="shell"
            checked={!!marks.shell}
            disabled={disabledAll}
            onToggle={() => onToggle("shell")}
          >
            <ScalarDiff
              expected={diff.fields.shell.expected}
              found={diff.fields.shell.found}
            />
          </FieldRow>
        )}
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          className="btn btn-sm btn-primary flex items-center gap-1"
          disabled={busy || disabledAll || !anyChecked}
          onClick={onApply}
        >
          <Check className="w-3.5 h-3.5" />
          {busy ? "Применяем…" : "Применить к БД"}
        </button>
      </div>
    </div>
  );
}

function FieldRow({
  field,
  label,
  checked,
  disabled,
  onToggle,
  children,
}: {
  field: FieldKey;
  label: string;
  checked: boolean;
  disabled: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-start gap-2 text-sm border border-token rounded px-3 py-2">
      <input
        type="checkbox"
        className="mt-0.5"
        aria-label={`Применить ${label}`}
        data-field={field}
        checked={checked}
        disabled={disabled}
        onChange={onToggle}
      />
      <div className="flex-1 min-w-0">
        <div className="text-xs text-dim mb-1 mono">{label}</div>
        {children}
      </div>
    </label>
  );
}

/** Было → стало для скалярных строковых полей (shell). */
function ScalarDiff({
  expected,
  found,
}: {
  expected: string | null;
  found: string | null;
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap text-sm">
      <span className="mono text-dim line-through break-all">
        {expected ?? "—"}
      </span>
      <span className="text-dim">→</span>
      <span className="mono text-accent break-all">{found ?? "—"}</span>
    </div>
  );
}

/** Было → стало для has_sudo. */
function BoolDiff({ expected, found }: { expected: boolean; found: boolean }) {
  return (
    <div className="flex items-center gap-2 flex-wrap text-sm">
      <span className="mono text-dim line-through inline-flex items-center gap-1">
        {expected && <ShieldCheck className="w-3 h-3" />}
        {expected ? "да" : "нет"}
      </span>
      <span className="text-dim">→</span>
      <span className="mono text-accent inline-flex items-center gap-1">
        {found && <ShieldCheck className="w-3 h-3" />}
        {found ? "да" : "нет"}
      </span>
    </div>
  );
}

/**
 * Было → стало для unix_groups: добавленные (есть в found, нет в expected) —
 * зелёным, убранные (есть в expected, нет в found) — красным с зачёркиванием,
 * неизменные — нейтрально.
 */
function GroupsDiff({
  expected,
  found,
}: {
  expected: string[];
  found: string[];
}) {
  const expSet = new Set(expected);
  const foundSet = new Set(found);
  const removed = expected.filter((g) => !foundSet.has(g));
  // Порядок: показываем итоговый набор (found) + отдельно убранные.
  return (
    <div className="flex items-center gap-1.5 flex-wrap text-sm">
      {found.length === 0 && removed.length === 0 && (
        <span className="text-dim italic">—</span>
      )}
      {found.map((g) => {
        const added = !expSet.has(g);
        return (
          <span
            key={`f-${g}`}
            className={`badge mono${added ? " badge-ok" : ""}`}
            title={added ? "добавлена" : "без изменений"}
          >
            {added ? "+" : ""}
            {g}
          </span>
        );
      })}
      {removed.map((g) => (
        <span
          key={`r-${g}`}
          className="badge mono badge-danger line-through"
          title="убрана"
        >
          −{g}
        </span>
      ))}
    </div>
  );
}
