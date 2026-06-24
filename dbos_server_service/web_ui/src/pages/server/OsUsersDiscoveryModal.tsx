/**
 * Поиск OS-юзеров на сервере (discovery) — модалка над страницей
 * `Server.Пользователи`.
 *
 * Сверху выбор сервера отдела + «Сканировать»: дёргает `usersInventory`,
 * поллит задачу (паттерн `useTaskOutcome`), из `result` собирает снимок.
 * Сам вид результата (таблица unknown_users с режимами add/ignore/skip,
 * секция unlinked_existing с привязкой) рендерит `InventoryResultView` —
 * он же владеет действиями импорта/ignore/привязки.
 *
 * Отдельная мини-секция «Проверка логина» переиспользует тот же снимок: по
 * введённому логину показывает вердикт (незнакомый / расхождение / совпадает /
 * не найден). Отдельного backend-роута нет — фильтруем результат скана.
 */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ScanSearch,
  Server as ServerIcon,
  RotateCw,
  AlertCircle,
  Search,
} from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { usersInventory } from "@/api/server/misc";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { InventoryResultView } from "@/pages/server/InventoryResultView";
import type {
  Server,
  UnknownUser,
  UnlinkedExistingUser,
  RevisionAccountDiff,
} from "@/api/server/types";

export function OsUsersDiscoveryModal({
  servers,
  serverName,
  onClose,
  onImported,
  onIgnored,
  onLinked,
  onScanStarted,
}: {
  servers: Server[];
  serverName: (id: string) => string;
  onClose: () => void;
  /** После успешного импорта хотя бы одного юзера — refetch списка аккаунтов. */
  onImported: () => void;
  /** После добавления хотя бы одного логина в ignore-list. */
  onIgnored: () => void;
  /** После привязки существующего аккаунта к серверу — refetch списка аккаунтов. */
  onLinked?: () => void;
  /**
   * Скан задиспатчен — отдаём наверх task_id и server_id. Точка запуска держит
   * этот трек у себя, чтобы результат не пропал при закрытии модалки до конца
   * долгой задачи.
   */
  onScanStarted?: (serverId: string, taskId: string) => void;
}) {
  const toast = useToast();
  const scan = useTaskOutcome();
  const [serverId, setServerId] = useState<string>(servers[0]?.id ?? "");
  // Сервер, по которому крутится/завершён текущий скан.
  const [scannedServer, setScannedServer] = useState<string | null>(null);
  const [checkLogin, setCheckLogin] = useState("");
  // true, пока InventoryResultView применяет batch — блокируем закрытие.
  const [applying, setApplying] = useState(false);

  const scanResult = scan.tracked?.result;

  const unknownUsers = useMemo<UnknownUser[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { unknown_users?: unknown }).unknown_users;
    return Array.isArray(raw) ? (raw as UnknownUser[]) : [];
  }, [scanResult]);

  const diffs = useMemo<RevisionAccountDiff[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { diffs?: unknown }).diffs;
    return Array.isArray(raw) ? (raw as RevisionAccountDiff[]) : [];
  }, [scanResult]);

  const unlinkedExisting = useMemo<UnlinkedExistingUser[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { unlinked_existing?: unknown }).unlinked_existing;
    return Array.isArray(raw) ? (raw as UnlinkedExistingUser[]) : [];
  }, [scanResult]);

  const polling = scan.tracked?.polling ?? false;
  const scanDone =
    scan.tracked != null &&
    !scan.tracked.polling &&
    scan.tracked.status === "succeeded";
  const scanFailed =
    scan.tracked != null &&
    !scan.tracked.polling &&
    (scan.tracked.status === "failed" || scan.tracked.error != null);

  // Новый скан — сбрасываем флаг busy (могли перезапустить).
  useEffect(() => {
    setApplying(false);
  }, [scan.tracked?.taskId]);

  async function handleScan() {
    if (!serverId || polling) return;
    scan.reset();
    setScannedServer(serverId);
    try {
      const res = await usersInventory(serverId);
      scan.track("discovery", res.task_id, res.status);
      onScanStarted?.(serverId, res.task_id);
    } catch (e) {
      setScannedServer(null);
      toast.error(apiErrMsg(e, "Не удалось запустить скан"));
    }
  }

  // Вердикт по введённому логину относительно последнего скана.
  const checkVerdict = useMemo(() => {
    const term = checkLogin.trim();
    if (!term || !scanDone) return null;
    const unknown = unknownUsers.find((u) => u.login === term);
    if (unknown) {
      return {
        kind: "warn" as const,
        text: `Незнакомый OS-юзер (uid ${unknown.uid}${unknown.has_sudo ? ", sudo" : ""}) — не привязан к аккаунту.`,
      };
    }
    const diff = diffs.find((d) => d.login === term);
    if (diff) {
      return {
        kind: "warn" as const,
        text: "Привязан, но есть расхождение атрибутов с БД (см. Ревизию).",
      };
    }
    return {
      kind: "ok" as const,
      text: "Не найден среди незнакомых и расхождений: привязан и совпадает либо отсутствует на боксе.",
    };
  }, [checkLogin, scanDone, unknownUsers, diffs]);

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !applying && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: 720 }}
          onInteractOutside={(e) => applying && e.preventDefault()}
          onEscapeKeyDown={(e) => applying && e.preventDefault()}
        >
          <div className="modal-header">
            <ScanSearch className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Поиск пользователей на ОС
            </Dialog.Title>
          </div>

          <div className="modal-body flex flex-col gap-4">
            <Dialog.Description className="text-xs text-dim">
              Сканирует OS-юзеров сервера (users/inventory) и показывает тех, кто
              не привязан к аккаунту и не в ignore-list. Каждого можно завести в
              БД (discovered) или отправить в ignore-list.
            </Dialog.Description>

            {/* Выбор сервера + скан */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-dim flex items-center gap-1">
                <ServerIcon className="w-3.5 h-3.5" /> Сервер:
              </span>
              <select
                className="surface-2 border border-token rounded px-2 py-1 text-sm flex-1 min-w-[180px]"
                value={serverId}
                disabled={polling}
                onChange={(e) => setServerId(e.target.value)}
              >
                {servers.length === 0 && (
                  <option value="">— нет серверов —</option>
                )}
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.display_name || s.hostname}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-sm btn-primary flex items-center gap-1"
                disabled={!serverId || polling}
                onClick={handleScan}
              >
                <ScanSearch
                  className={`w-3.5 h-3.5 ${polling ? "animate-spin" : ""}`}
                />
                {polling ? "Сканируем…" : "Сканировать"}
              </button>
            </div>

            {polling && (
              <div className="alert text-sm flex items-center gap-2" role="status">
                <RotateCw className="w-4 h-4 animate-spin shrink-0" />
                <span>
                  Скан {scannedServer ? `(${serverName(scannedServer)}) ` : ""}
                  запущен — ждём результат…
                </span>
              </div>
            )}

            {scanFailed && (
              <div className="alert-danger text-sm flex items-start gap-2">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <span>
                  Скан не удался: {scan.tracked?.error ?? "задача завершилась ошибкой"}
                </span>
              </div>
            )}

            {/* Результат скана: незнакомые + unlinked_existing + действия */}
            {scanDone && (
              <InventoryResultView
                key={scan.tracked?.taskId}
                serverId={scannedServer}
                unknownUsers={unknownUsers}
                unlinkedExisting={unlinkedExisting}
                onImported={onImported}
                onIgnored={onIgnored}
                onLinked={onLinked}
                onBusyChange={setApplying}
              />
            )}

            {/* Проверка конкретного логина */}
            <div className="card flex flex-col gap-2">
              <div className="text-xs uppercase text-dim flex items-center gap-2">
                <Search className="w-3 h-3" /> Проверка логина
              </div>
              <div className="text-xs text-dim">
                По данным последнего скана. Сначала просканируйте сервер.
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  className="field-input mono flex-1 min-w-[160px]"
                  value={checkLogin}
                  onChange={(e) => setCheckLogin(e.target.value)}
                  placeholder="login для проверки"
                  aria-label="login для проверки"
                />
              </div>
              {checkLogin.trim() && !scanDone && (
                <div className="text-xs text-dim">
                  Нет данных скана — нажмите «Сканировать».
                </div>
              )}
              {checkVerdict && (
                <div
                  className={`text-sm ${checkVerdict.kind === "ok" ? "text-dim" : "alert-warn"}`}
                >
                  {checkVerdict.text}
                </div>
              )}
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={applying}>
              Закрыть
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
