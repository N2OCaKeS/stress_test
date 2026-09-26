/**
 * Папка Zephyr пары (отдел, РЦ) на странице СТП.
 *
 * Её id — `-fti` (`FOLDER_TREE_ID`) у тестов: без него claim теста каталога
 * падает с «сгенерируйте СТП или задайте id папки вручную». Запись заводит
 * генерация СТП (найти по шаблону пути или создать); здесь её видно, можно
 * задать id руками (генерация его больше не перезаписывает) или найти заново
 * (снимает ручную метку).
 *
 * Source of truth: `testing_service` `GET/PUT /stp/zephyr-folder`,
 * `POST /stp/zephyr-folder/refresh`.
 */

import { useState } from "react";

import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { getZephyrFolder, refreshZephyrFolder, setZephyrFolder } from "@/api/testing/stp";
import type { ZephyrFolder } from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/contexts/ToastContext";

export function ZephyrFolderLine({
  osVersionId,
  departmentId,
  reloadKey,
}: {
  osVersionId: string;
  departmentId?: string;
  /** Меняется после генерации СТП — папку надо перечитать. */
  reloadKey?: unknown;
}) {
  const toast = useToast();
  const folderQ = useQuery<ZephyrFolder>(
    () => getZephyrFolder({ os_version_id: osVersionId, department_id: departmentId }),
    [osVersionId, departmentId, reloadKey],
  );
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const folder = folderQ.data;

  async function save() {
    const value = draft.trim();
    if (!value) return;
    setBusy(true);
    try {
      await setZephyrFolder({ os_version_id: osVersionId, department_id: departmentId, folder_tree_id: value });
      setEditing(false);
      folderQ.refetch();
      toast.success("id папки Zephyr сохранён");
    } catch (e) {
      toast.error(`Не удалось сохранить id папки: ${apiErrMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      const res = await refreshZephyrFolder({ os_version_id: osVersionId, department_id: departmentId });
      folderQ.refetch();
      if (res.error) {
        toast.warn(`Папка Zephyr не найдена: ${res.error.message}`);
      } else {
        toast.success(`Папка Zephyr найдена: id ${res.folder_tree_id}`);
      }
    } catch (e) {
      toast.error(`Не удалось найти папку: ${apiErrMsg(e)}`);
    } finally {
      setBusy(false);
    }
  }

  if (folderQ.error) {
    return (
      <div className="text-xs mt-1 text-danger" data-testid="zephyr-folder-line">
        Папка Zephyr: {apiErrMsg(folderQ.error)}
      </div>
    );
  }

  return (
    <div className="text-xs mt-1 flex items-center gap-1.5 flex-wrap" data-testid="zephyr-folder-line">
      <span className="text-dim">Папка Zephyr:</span>
      {folderQ.loading || !folder ? (
        <span className="text-dim italic">загрузка…</span>
      ) : (
        <>
          <span className="mono">{folder.folder_path ?? "—"}</span>
          {folder.folder_tree_id ? (
            <span className="mono">id {folder.folder_tree_id}</span>
          ) : (
            <Badge kind="warn">id не задан</Badge>
          )}
          {folder.is_manual && <Badge kind="accent">вручную</Badge>}
          {folder.error && !folder.folder_tree_id && <span className="text-dim">{folder.error.message}</span>}
        </>
      )}
      {editing ? (
        <>
          <input
            aria-label="id папки Zephyr"
            className="input mono w-28"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save();
              if (e.key === "Escape") setEditing(false);
            }}
            autoFocus
          />
          <Button size="sm" variant="primary" disabled={busy || !draft.trim()} onClick={() => void save()}>
            Сохранить
          </Button>
          <Button size="sm" disabled={busy} onClick={() => setEditing(false)}>
            Отмена
          </Button>
        </>
      ) : (
        <>
          <Button
            size="sm"
            disabled={busy || !folder}
            onClick={() => {
              setDraft(folder?.folder_tree_id ?? "");
              setEditing(true);
            }}
          >
            Изменить id
          </Button>
          <Button size="sm" disabled={busy || !folder} onClick={() => void refresh()}>
            Найти заново
          </Button>
        </>
      )}
    </div>
  );
}
