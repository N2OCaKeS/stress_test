/**
 * Модалка массовых операций со снимками ACS для выбранных серверов
 * (`POST /servers/acs-snapshots/create-batch` / `.../restore-batch`).
 *
 * По образцу `_bulkPrepareModal.tsx`: список выбранных серверов задаётся
 * пропом `servers`, локальный чекбокс «кроме VMS-hub» лишь фильтрует, что
 * реально уйдёт в запрос — внешний выбор в `Server.tsx` не трогаем. Действие
 * (создать/восстановить) переключается табом внутри модалки — оба доступны
 * держателю одного права `Action.ACS_SNAPSHOT` (list/create/restore разом,
 * см. `server_service/src/core/constants.py`), поэтому фронт не гейтит режим
 * «Восстановить» отдельно. Выбор серверов может охватывать несколько
 * отделов — реальную авторизацию (и «есть ли грант вообще») проверяет
 * backend один раз на весь батч перед циклом (403 на весь запрос, если права
 * нет), per-server гейты (decommissioned/vms-hub/busy/prepared/bootstrap-
 * пароль) — в `failed[]`.
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  Camera,
  CheckCircle2,
  RotateCcw,
  XCircle,
  X,
} from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { listOsVersions } from "@/api/server/osVersions";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  createAcsSnapshotsBatch,
  restoreAcsSnapshotsBatch,
} from "@/api/server/acsSnapshots";
import type {
  AcsSnapshotBatchReason,
  AcsSnapshotBatchResponse,
  OsVersion,
  Server,
} from "@/api/server/types";

const ACS_BATCH_REASON_RU: Record<string, string> = {
  not_found_or_cross_dept: "сервер не найден или принадлежит другому отделу",
  decommissioned: "сервер выведен из эксплуатации",
  server_is_vms_hub: "сервер подготовлен как VMS-hub — снимки ACS недоступны",
  reserved: "сервер забронирован другим пользователем",
  updating: "на сервере уже идёт обновление ОС",
  acs_busy: "на сервере уже идёт операция ACS",
  prepare_required: "сначала нужен prepare — сервер не подготовлен",
  acs_disabled: "ACS отключён платформенно",
  acs_department_not_enabled: "ACS не включён для отдела сервера",
  os_version_not_found: "версия каталога не найдена",
  bootstrap_password_missing: "не задан bootstrap-пароль версии для авто-prepare",
  permission_denied: "недостаточно прав на это действие",
  idempotent_conflict: "задача с таким ключом идемпотентности уже существует",
  worker_unreachable: "worker недоступен — задача не поставлена",
  not_attempted: "не обработан — батч прерван более ранней ошибкой",
};

function acsBatchReasonRu(reason: AcsSnapshotBatchReason): string {
  return ACS_BATCH_REASON_RU[reason] ?? reason;
}

type AcsAction = "create" | "restore";

export function BulkAcsSnapshotModal({
  servers,
  onClose,
  onDone,
}: {
  servers: Server[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [action, setAction] = useState<AcsAction>("create");
  const [excludeVmsHub, setExcludeVmsHub] = useState(true);
  const [osVersionId, setOsVersionId] = useState("");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<AcsSnapshotBatchResponse | null>(null);

  const versionsQ = useQuery<OsVersion[]>(
    async () => (await listOsVersions({ limit: 200 })).items,
    [],
  );
  const versions = versionsQ.data ?? [];

  const targetServers = useMemo(
    () => (excludeVmsHub ? servers.filter((s) => !s.is_vms_hub) : servers),
    [servers, excludeVmsHub],
  );
  const excludedCount = servers.length - targetServers.length;

  const serverName = (s: Server) => s.display_name ?? s.hostname;
  const byId = useMemo(() => {
    const m = new Map<string, Server>();
    for (const s of servers) m.set(s.id, s);
    return m;
  }, [servers]);

  const valid = !!osVersionId && targetServers.length > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setErr(null);
    setPending(true);
    try {
      const serverIds = targetServers.map((s) => s.id);
      const res =
        action === "create"
          ? await createAcsSnapshotsBatch(serverIds, osVersionId)
          : await restoreAcsSnapshotsBatch(serverIds, osVersionId);
      setResult(res);
    } catch (e) {
      setErr(
        apiErrMsg(
          e,
          action === "create"
            ? "Массовое создание снимков не удалось"
            : "Массовое восстановление не удалось",
        ),
      );
    } finally {
      setPending(false);
    }
  }

  const dispatched = result?.dispatched ?? [];
  const failed = result?.failed ?? [];

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: 640 }}
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Camera className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Снимки ACS выбранных ({servers.length})
            </Dialog.Title>
          </div>

          {result ? (
            <>
              <div className="modal-body flex flex-col gap-3">
                <div className="flex items-center gap-3 text-sm flex-wrap">
                  <span className="flex items-center gap-1 text-ok">
                    <CheckCircle2 className="w-4 h-4" /> поставлено:{" "}
                    {dispatched.length}
                  </span>
                  {failed.length > 0 && (
                    <span className="flex items-center gap-1 text-warn">
                      <XCircle className="w-4 h-4" /> пропущено: {failed.length}
                    </span>
                  )}
                </div>

                {dispatched.length > 0 && (
                  <div className="flex flex-col gap-1">
                    {dispatched.map((d) => (
                      <div
                        key={d.server_id}
                        className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5 text-ok shrink-0" />
                        <span className="flex-1 min-w-0 truncate">
                          {d.server_name ??
                            (byId.get(d.server_id) &&
                              serverName(byId.get(d.server_id)!)) ??
                            d.server_id}
                        </span>
                        {d.task_id && (
                          <span className="mono text-[11px] text-dim">
                            {d.task_id}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {failed.length > 0 && (
                  <div className="flex flex-col gap-1">
                    {failed.map((f) => (
                      <div
                        key={f.server_id}
                        className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
                      >
                        <XCircle className="w-3.5 h-3.5 text-warn shrink-0" />
                        <span className="flex-1 min-w-0 truncate">
                          {f.server_name ??
                            (byId.get(f.server_id) &&
                              serverName(byId.get(f.server_id)!)) ??
                            f.server_id}
                        </span>
                        <span className="text-xs text-dim text-right">
                          {f.reason ? acsBatchReasonRu(f.reason) : "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-primary flex items-center gap-1"
                  onClick={() => {
                    onDone();
                    onClose();
                  }}
                >
                  <X className="w-4 h-4" /> Готово
                </button>
              </div>
            </>
          ) : (
            <form onSubmit={submit}>
              <div className="modal-body flex flex-col gap-3">
                <div className="inline-flex rounded border border-token overflow-hidden self-start">
                  <button
                    type="button"
                    onClick={() => setAction("create")}
                    aria-pressed={action === "create"}
                    className={`px-3 py-1.5 text-sm flex items-center gap-1.5 ${
                      action === "create" ? "btn-primary" : "hover-bg text-dim"
                    }`}
                  >
                    <Camera className="w-4 h-4" /> Создать снимок
                  </button>
                  <button
                    type="button"
                    onClick={() => setAction("restore")}
                    aria-pressed={action === "restore"}
                    className={`px-3 py-1.5 text-sm flex items-center gap-1.5 border-l border-token ${
                      action === "restore" ? "btn-danger" : "hover-bg text-dim"
                    }`}
                  >
                    <RotateCcw className="w-4 h-4" /> Восстановить
                  </button>
                </div>

                {action === "restore" && (
                  <div className="alert alert-danger flex items-start gap-2 text-xs">
                    <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                    <div>
                      Восстановление ПОЛНОСТЬЮ ПЕРЕПИШЕТ ДИСК каждого выбранного
                      сервера снимком версии ниже. Необратимо.
                    </div>
                  </div>
                )}

                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-dim text-xs">Версия ОС *</span>
                  {versionsQ.loading ? (
                    <div className="text-xs text-dim">Загрузка каталога…</div>
                  ) : (
                    <Dropdown
                      mode="single"
                      placeholder="— выберите версию —"
                      options={[
                        { value: "", label: "— выберите версию —" },
                        ...versions.map((v) => ({ value: v.id, label: v.name })),
                      ]}
                      value={osVersionId}
                      onChange={setOsVersionId}
                    />
                  )}
                </label>

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={excludeVmsHub}
                    onChange={(e) => setExcludeVmsHub(e.target.checked)}
                  />
                  Выбрать все, кроме VMS-hub
                </label>

                <div className="text-xs text-dim">
                  В запрос уйдёт {targetServers.length} из {servers.length}{" "}
                  выбранных серверов
                  {excludedCount > 0 && ` (${excludedCount} VMS-hub исключено)`}.
                </div>

                {targetServers.length > 0 && (
                  <div className="flex flex-col gap-1 max-h-40 overflow-y-auto border border-token rounded p-2">
                    {targetServers.map((s) => (
                      <div key={s.id} className="text-xs mono truncate">
                        {serverName(s)}
                      </div>
                    ))}
                  </div>
                )}

                {err && <div className="alert-danger text-sm">{err}</div>}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn flex items-center gap-1"
                  onClick={onClose}
                  disabled={pending}
                >
                  <X className="w-4 h-4" /> Отмена
                </button>
                <button
                  type="submit"
                  className={`btn flex items-center gap-1 ${action === "restore" ? "btn-danger" : "btn-primary"}`}
                  disabled={pending || !valid}
                >
                  {action === "create" ? (
                    <Camera className="w-4 h-4" />
                  ) : (
                    <RotateCcw className="w-4 h-4" />
                  )}
                  {pending
                    ? "Запускаем…"
                    : action === "create"
                      ? "Создать снимки"
                      : "Восстановить"}
                </button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
