import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Camera, ChevronDown, ChevronRight, KeyRound, Loader2, Play, Plus, Trash2 } from "lucide-react";
import { Dropdown } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64 } from "@/lib/base64";
import {
  createTestStand,
  deleteTestStand,
  getTestStand,
  getTestStandCredentials,
  getTestStandVmSnapshots,
  listTestStands,
  updateTestStand,
} from "@/api/testing/testStands";
import type {
  TestStand,
  TestStandTargetType,
  TestStandTestCredentials,
  TestStandUpdateRequest,
} from "@/api/testing/types";
import { listServers } from "@/api/server/servers";
import { listVms, type Vm } from "@/api/server/vms";
import type { Server as InventoryServer } from "@/api/server/types";
import { StandGroupLaunchModal } from "@/pages/testing/StandGroupLaunchModal";

function asServerCard(server: TestStand["server"]) {
  return server as { display_name?: string; hostname?: string; name?: string; ip_address?: string } | null;
}

function isVmStand(stand: TestStand): boolean {
  return stand.target_type === "vm";
}

/** Имя стенда для таблиц и диалогов: карточка сервера/ВМ, иначе id. */
function standLabel(stand: TestStand): string {
  const card = asServerCard(stand.server);
  return card?.display_name || card?.hostname || card?.name || stand.server_id || stand.vm_id || stand.id;
}

async function loadStands(): Promise<TestStand[]> {
  const result = await listTestStands({ limit: 500 });
  return Promise.all(result.items.map((stand) => getTestStand(stand.id)));
}

/** Регистрация и настройки стендов: Администрирование → Тестирование → Стенды пула. */
export function ServicesTestingStands() {
  const query = useQuery(loadStands, []);
  return (
    <div className="p-5">
      <StandsAdminPanel
        stands={query.data ?? []}
        loading={query.isFetching}
        error={query.error}
        onReload={query.refetch}
      />
    </div>
  );
}

function StandsAdminPanel({
  stands,
  loading,
  error,
  onReload,
}: {
  stands: TestStand[];
  loading: boolean;
  error: unknown;
  onReload: () => void | Promise<void>;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [open, setOpen] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [credentialsStandId, setCredentialsStandId] = useState<string | null>(null);
  const [snapshotsStand, setSnapshotsStand] = useState<TestStand | null>(null);
  const [groupLaunchStand, setGroupLaunchStand] = useState<TestStand | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function toggleField(stand: TestStand, field: "queue_enabled" | "is_active") {
    setBusyId(stand.id);
    const body: TestStandUpdateRequest =
      field === "queue_enabled" ? { queue_enabled: !stand.queue_enabled } : { is_active: !stand.is_active };
    try {
      await updateTestStand(stand.id, body);
      await onReload();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить стенд"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(stand: TestStand) {
    const label = standLabel(stand);
    const ok = await confirm({
      title: "Удалить стенд из пула",
      message: `Удалить стенд «${label}» из testing_service? Сервер останется в инвентаре. Стенд с историей запусков удалить нельзя.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusyId(stand.id);
    try {
      await deleteTestStand(stand.id);
      toast.success(`Стенд «${label}» удалён из пула`);
      await onReload();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить стенд"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full flex items-center justify-between px-4 py-3 hover-bg">
        <div className="flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-accent" />
          <span className="font-semibold">Управление стендами пула</span>
          <span className="text-xs text-dim">регистрация серверов инвентаря как тестовых стендов testing_service</span>
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
      </button>
      {open && (
        <div className="border-t border-token p-4 grid gap-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="text-xs text-dim">{stands.length} стендов зарегистрировано</div>
            <Button type="button" size="sm" variant="primary" disabled={loading} className="inline-flex items-center gap-1.5" onClick={() => setCreateOpen(true)}>
              <Plus className="w-3.5 h-3.5" />
              Добавить стенд
            </Button>
          </div>

          {loading ? (
            <div className="text-xs text-dim flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
            </div>
          ) : error ? (
            <div className="alert alert-danger flex items-start gap-2 text-xs">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <div className="flex-1">{apiErrMsg(error, "Список стендов не загрузился")}</div>
              <Button size="sm" onClick={() => onReload()}>Повторить</Button>
            </div>
          ) : stands.length === 0 ? (
            <div className="text-xs text-dim">Стендов пока нет — добавьте сервер из инвентаря кнопкой выше.</div>
          ) : (
            <div className="surface-2 border border-token rounded overflow-hidden">
              <table className="mini">
                <thead>
                  <tr>
                    <th>Стенд</th>
                    <th>Тип</th>
                    <th>IP</th>
                    <th>Очередь</th>
                    <th>Активен</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {stands.map((stand) => {
                    const server = asServerCard(stand.server);
                    const rowBusy = busyId === stand.id;
                    return (
                      <tr key={stand.id}>
                        <td className="mono">{standLabel(stand)}</td>
                        <td>
                          {isVmStand(stand) ? (
                            <Badge kind="info" title="Виртуальный стенд: перед тестом ВМ откатывается на снимок версии ОС">ВМ</Badge>
                          ) : (
                            <Badge kind="neutral" title="Физический сервер: перед тестом диск восстанавливается через ACS">Сервер</Badge>
                          )}
                        </td>
                        <td className="mono text-xs text-dim">{server?.ip_address ?? "—"}</td>
                        <td>
                          <Button
                            size="sm"
                            variant={stand.queue_enabled ? "primary" : "default"}
                            disabled={rowBusy}
                            onClick={() => toggleField(stand, "queue_enabled")}
                          >
                            {stand.queue_enabled ? "включена" : "выключена"}
                          </Button>
                        </td>
                        <td>
                          <Button
                            size="sm"
                            variant={stand.is_active ? "primary" : "default"}
                            disabled={rowBusy}
                            onClick={() => toggleField(stand, "is_active")}
                          >
                            {stand.is_active ? "активен" : "неактивен"}
                          </Button>
                        </td>
                        <td>
                          <div className="flex items-center gap-1 justify-end flex-wrap">
                            <Button
                              size="sm"
                              disabled={rowBusy}
                              className="inline-flex items-center gap-1"
                              onClick={() => setGroupLaunchStand(stand)}
                            >
                              <Play className="w-3.5 h-3.5" />
                              Запустить все тесты стенда
                            </Button>
                            {isVmStand(stand) && (
                              <Button
                                size="sm"
                                disabled={rowBusy}
                                className="inline-flex items-center gap-1"
                                onClick={() => setSnapshotsStand(stand)}
                              >
                                <Camera className="w-3.5 h-3.5" />
                                Снимки по версиям ОС
                              </Button>
                            )}
                            <Button
                              size="sm"
                              disabled={rowBusy}
                              className="inline-flex items-center gap-1"
                              onClick={() => setCredentialsStandId(stand.id)}
                            >
                              <KeyRound className="w-3.5 h-3.5" />
                              Учётные данные теста
                            </Button>
                            <Button size="sm" variant="danger" disabled={rowBusy} aria-label="Удалить стенд" onClick={() => handleDelete(stand)}>
                              <Trash2 className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {createOpen && (
        <CreateStandModal
          existingServerIds={stands.flatMap((stand) => (stand.server_id ? [stand.server_id] : []))}
          existingVmIds={stands.flatMap((stand) => (stand.vm_id ? [stand.vm_id] : []))}
          onClose={() => setCreateOpen(false)}
          onCreated={async () => {
            setCreateOpen(false);
            await onReload();
          }}
        />
      )}
      {credentialsStandId && (
        <TestStandCredentialsModal standId={credentialsStandId} onClose={() => setCredentialsStandId(null)} />
      )}
      {snapshotsStand && (
        <VmSnapshotsModal stand={snapshotsStand} onClose={() => setSnapshotsStand(null)} />
      )}
      {groupLaunchStand && (
        <StandGroupLaunchModal
          standId={groupLaunchStand.id}
          standDepartmentId={groupLaunchStand.department_id}
          standLabel={standLabel(groupLaunchStand)}
          onClose={() => setGroupLaunchStand(null)}
          onLaunched={(id) => {
            setGroupLaunchStand(null);
            toast.success(`Кампания ${id} запущена`);
          }}
        />
      )}
    </div>
  );
}

function CreateStandModal({
  existingServerIds,
  existingVmIds,
  onClose,
  onCreated,
}: {
  existingServerIds: string[];
  existingVmIds: string[];
  onClose: () => void;
  onCreated: () => void | Promise<void>;
}) {
  const toast = useToast();
  const [targetType, setTargetType] = useState<TestStandTargetType>("server");
  const serversQ = useQuery(async () => (await listServers({ limit: 500 })).items, []);
  const vmsQ = useQuery(async () => (await listVms({ limit: 200 })).items, []);
  const existingServers = useMemo(() => new Set(existingServerIds), [existingServerIds]);
  const existingVms = useMemo(() => new Set(existingVmIds), [existingVmIds]);
  // Сервер/ВМ, уже заведённые как стенд, из выбора убираем — backend всё равно
  // отклонит повтор (server_id/vm_id уникальны на стороне test_stand), но так
  // очевиднее, что выбирать больше не из чего.
  const availableServers = useMemo(
    () => (serversQ.data ?? []).filter((s: InventoryServer) => !existingServers.has(s.id)),
    [serversQ.data, existingServers],
  );
  const availableVms = useMemo(
    () => (vmsQ.data ?? []).filter((vm: Vm) => !existingVms.has(vm.id)),
    [vmsQ.data, existingVms],
  );

  const [targetId, setTargetId] = useState("");
  const [queueEnabled, setQueueEnabled] = useState(true);
  const [isActive, setIsActive] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const isVm = targetType === "vm";
  const listQ = isVm ? vmsQ : serversQ;
  const options = isVm
    ? availableVms.map((vm: Vm) => ({
        value: vm.id,
        label: `${vm.name}${vm.hostname ? ` (${vm.hostname})` : ""} · ${vm.ip_address ?? "без IP"}`,
      }))
    : availableServers.map((s: InventoryServer) => ({
        value: s.id,
        label: `${s.display_name || s.hostname} · ${s.ip_address}`,
      }));

  function switchType(next: TestStandTargetType) {
    setTargetType(next);
    setTargetId("");
  }

  async function submit() {
    if (!targetId || submitting) return;
    setSubmitting(true);
    try {
      await createTestStand({
        ...(isVm ? { target_type: "vm" as const, vm_id: targetId } : { server_id: targetId }),
        queue_enabled: queueEnabled,
        is_active: isActive,
      });
      toast.success("Стенд добавлен в пул");
      await onCreated();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось добавить стенд"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title="Добавить стенд"
      subtitle="Зарегистрировать сервер или ВМ из инвентаря server_service как тестовый стенд"
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Отмена</Button>
          <Button variant="primary" type="button" disabled={!targetId || submitting} onClick={submit}>
            {submitting ? "Добавление…" : "Добавить"}
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <div className="grid gap-1">
          <span className="text-xs text-dim">Тип стенда</span>
          <div className="flex gap-2 flex-wrap">
            <Button
              type="button"
              size="sm"
              variant={isVm ? "default" : "primary"}
              aria-pressed={!isVm}
              onClick={() => switchType("server")}
            >
              Физический сервер
            </Button>
            <Button
              type="button"
              size="sm"
              variant={isVm ? "primary" : "default"}
              aria-pressed={isVm}
              onClick={() => switchType("vm")}
            >
              Виртуальная машина
            </Button>
          </div>
          <span className="text-xs text-dim">
            {isVm
              ? "Перед тестом ВМ откатывается на снимок нужной версии ОС (снимок ищется по имени), дальше — та же подготовка, что у сервера. Нужна тестовая учётка отдела и IP гостя в мостовой сети."
              : "Перед тестом диск сервера восстанавливается из снимка ACS нужной версии ОС."}
          </span>
        </div>
        {listQ.loading ? (
          <div className="text-xs text-dim flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> {isVm ? "Загрузка ВМ…" : "Загрузка серверов…"}
          </div>
        ) : listQ.error ? (
          <div className="alert alert-danger text-xs">
            {apiErrMsg(listQ.error, isVm ? "Список ВМ не загрузился" : "Список серверов не загрузился")}
          </div>
        ) : (
          <label className="grid gap-1">
            <span className="text-xs text-dim">{isVm ? "ВМ" : "Сервер"}</span>
            <Dropdown
              mode="single"
              searchable
              placeholder={
                options.length
                  ? isVm ? "Выберите ВМ" : "Выберите сервер"
                  : isVm ? "Нет доступных ВМ — все уже стенды" : "Нет доступных серверов — все уже стенды"
              }
              options={options}
              value={targetId}
              onChange={setTargetId}
              disabled={!options.length}
            />
          </label>
        )}
        <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
          <Checkbox checked={queueEnabled} onChange={(e) => setQueueEnabled(e.target.checked)} className="mt-0.5" />
          <span>
            <span className="text-sm font-medium block">Автоочередь включена</span>
            <span className="text-xs text-dim">Стенд участвует в автоматической постановке тестов в очередь</span>
          </span>
        </label>
        <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
          <Checkbox checked={isActive} onChange={(e) => setIsActive(e.target.checked)} className="mt-0.5" />
          <span>
            <span className="text-sm font-medium block">Стенд активен</span>
            <span className="text-xs text-dim">Неактивные стенды исключаются из планирования прогонов</span>
          </span>
        </label>
      </div>
    </Modal>
  );
}

/**
 * Сопоставление снимков ВМ-стенда и версий ОС. Таблицы
 * сопоставления нет (решение T7): server_service читает версию из имени
 * снимка по шаблонам (Администрирование server_service → «Шаблоны снимков ВМ»)
 * и тем же правилом выбирает снимок при подготовке. Здесь — живой список: что
 * на какую версию откатит, и какие снимки названы не по схеме.
 */
function VmSnapshotsModal({ stand, onClose }: { stand: TestStand; onClose: () => void }) {
  const query = useQuery(() => getTestStandVmSnapshots(stand.id), [stand.id]);
  const data = query.data;
  const matched = (data?.snapshots ?? []).filter((item) => item.normalized_version);
  const unmatched = (data?.snapshots ?? []).filter((item) => !item.normalized_version);
  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title="Снимки ВМ по версиям ОС"
      subtitle={standLabel(stand)}
      width="lg"
      footer={<Button type="button" onClick={onClose}>Закрыть</Button>}
    >
      <div className="grid gap-3">
        {query.loading ? (
          <div className="text-xs text-dim flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Загрузка снимков…
          </div>
        ) : query.error ? (
          <div className="alert alert-danger text-xs">{apiErrMsg(query.error, "Список снимков не загрузился")}</div>
        ) : data ? (
          <>
            <div className="text-xs text-dim">
              Перед тестом ВМ откатывается на снимок, в имени которого версия ОС запуска (сравнение после
              нормализации: <span className="mono">1710rc52</span> = <span className="mono">1.7.10.52</span>).
              Шаблоны имени по порядку:{" "}
              {data.templates.map((template, index) => (
                <span key={template}>
                  {index > 0 && ", "}
                  <span className="mono">{template}</span>
                </span>
              ))}
              . Нет снимка версии — постановка теста отклоняется (<span className="mono">VM_SNAPSHOT_NOT_FOUND</span>).
            </div>
            {matched.length === 0 ? (
              <div className="text-xs text-dim">Ни один снимок не назван по шаблонам — запустить тест на этой ВМ нельзя.</div>
            ) : (
              <div className="surface-2 border border-token rounded overflow-hidden">
                <table className="mini">
                  <thead>
                    <tr>
                      <th>Версия ОС</th>
                      <th>Режим</th>
                      <th>Снимок</th>
                      <th>Шаблон</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matched.map((item) => (
                      <tr key={item.snapshot_id}>
                        <td className="mono">{item.normalized_version}</td>
                        <td>{item.mode ?? "любой"}</td>
                        <td className="mono">
                          {item.name}
                          {item.is_current && <span className="text-xs text-dim"> · текущий</span>}
                        </td>
                        <td className="mono text-xs text-dim">{item.template}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {unmatched.length > 0 && (
              <div className="text-xs text-dim">
                Не подходят под шаблоны (при подготовке не используются):{" "}
                <span className="mono">{unmatched.map((item) => item.name).join(", ")}</span>
              </div>
            )}
          </>
        ) : null}
      </div>
    </Modal>
  );
}

/**
 * Раскрытие учётки теста стенда — тот же UX-принцип, что и у карточки
 * «Учётка теста» в консоли сервера (`pages/server/tabs/manage.tsx`,
 * `TestCredentialsCard`): метаданные грузятся сразу, пароль/приватный ключ —
 * только по явному клику «Показать» отдельным запросом с `reveal=true`
 * (CRITICAL-audit + throttle на стороне backend), значения живут только в
 * state этой модалки и обнуляются закрытием.
 */
function TestStandCredentialsModal({ standId, onClose }: { standId: string; onClose: () => void }) {
  const toast = useToast();
  const [meta, setMeta] = useState<TestStandTestCredentials | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [plain, setPlain] = useState<{ password: string | null; privateKey: string | null } | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [throttleUntil, setThrottleUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);
  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getTestStandCredentials(standId)
      .then((c) => {
        if (!cancelled) setMeta(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setErr(apiErrMsg(e, "Не удалось получить учётку теста"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [standId]);

  async function handleReveal() {
    if (revealing || throttleLeft > 0) return;
    setRevealing(true);
    try {
      const c = await getTestStandCredentials(standId, true);
      setMeta(c);
      setPlain({
        password: c.password_b64 !== null ? fromBase64(c.password_b64) : null,
        privateKey: c.ssh_private_key_b64 !== null ? fromBase64(c.ssh_private_key_b64) : null,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 60;
        setThrottleUntil(Date.now() + secs * 1000);
        setNow(Date.now());
        toast.error(`Reveal-лимит: повторите через ${secs} сек`);
      } else {
        toast.error(apiErrMsg(e, "Не удалось раскрыть учётку теста"));
      }
    } finally {
      setRevealing(false);
    }
  }

  const revealed = plain !== null;

  return (
    <Modal open onOpenChange={(next) => !next && onClose()} title="Учётные данные теста" width="md">
      <div className="grid gap-3">
        {loading && <div className="text-sm text-dim">загружаем…</div>}
        {err && (
          <div className="alert alert-danger flex items-start gap-2 text-xs">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <div>{err}</div>
          </div>
        )}

        {meta && !meta.exists && (
          <div className="text-[11px] text-dim italic">
            Учётка ещё не выпускалась — пайплайн prepare-for-test ни разу не проходил для этого стенда.
          </div>
        )}

        {meta && meta.exists && (
          <>
            <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-dim">username</dt>
              <dd className="mono break-all">{meta.username ?? "—"}</dd>
              <dt className="text-dim">rotated_at</dt>
              <dd className="mono">{meta.rotated_at ?? "—"}</dd>
              <dt className="text-dim">public key</dt>
              <dd className="mono break-all">{meta.ssh_public_key ?? "—"}</dd>
            </dl>

            {revealed ? (
              <div className="grid gap-3">
                <div className="flex items-center gap-3 flex-wrap text-sm">
                  <span className="text-dim text-xs w-28 shrink-0">password</span>
                  <span className="mono flex-1 min-w-[180px] break-all">{plain.password ?? "—"}</span>
                </div>
                {plain.privateKey !== null && (
                  <pre className="mono text-xs whitespace-pre-wrap break-all surface-2 border border-token rounded p-2 max-h-64 overflow-auto">
                    {plain.privateKey}
                  </pre>
                )}
                <Button size="sm" type="button" className="self-start" onClick={() => setPlain(null)}>
                  Скрыть
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                type="button"
                className="self-start"
                onClick={handleReveal}
                disabled={revealing || throttleLeft > 0}
              >
                {revealing ? "Запрашиваем…" : throttleLeft > 0 ? `Подождите ${throttleLeft}с` : "Показать"}
              </Button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
