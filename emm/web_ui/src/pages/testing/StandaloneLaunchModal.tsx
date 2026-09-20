import { useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg, ApiError } from "@/api/client";
import { listOsVersions, resolveOsKernels } from "@/api/server/osVersions";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import { listTestStands, getTestStand } from "@/api/testing/testStands";
import { launchQueueItem } from "@/api/testing/queueItems";
import { addTestToStp } from "@/api/testing/stp";
import { usePersona } from "@/contexts/PersonaContext";
import { canForceStandLaunch } from "@/lib/rbac";
import type { ActiveQueueMode } from "@/api/testing/types";
import { QueueActivePrompt, StandBusyNotice, StandBusyPrompt } from "./StandLaunchConflict";

/**
 * Подсказка §E2 при отказе `TEST_NOT_IN_STP`/`STP_RUN_NOT_FOUND`: различает
 * случай (a) — СТП для контекста уже есть, просто наш тест не в ней (можно
 * добавить, `runId` указывает куда) — от случая (b) — СТП для этого
 * стенда/РЦ/ядра/режима не генерировалась вовсе (добавлять некуда, нужна
 * сперва генерация). См. `launch_stp.py::require_membership`.
 */
type StpPrompt = { kind: "add"; runId: string } | { kind: "not_generated" };

export function StandaloneLaunchModal({ onClose, onLaunched }: { onClose: () => void; onLaunched: (id: string) => void }) {
  const { persona } = usePersona();
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  const testsQ = useQuery(() => listTestDefinitions({ limit: 500 }), []);
  const standsQ = useQuery(async () => {
    const page = await listTestStands({ is_active: true, limit: 500 });
    return Promise.all(page.items.map((stand) => getTestStand(stand.id)));
  }, []);
  const [testId, setTestId] = useState("");
  const [standId, setStandId] = useState("");
  const [rc, setRc] = useState("");
  const [detected, setDetected] = useState<Record<string, string[]>>({});
  const [kernel, setKernel] = useState("");
  const [debug, setDebug] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stpPrompt, setStpPrompt] = useState<StpPrompt | null>(null);
  const [addBusy, setAddBusy] = useState(false);
  const [standBusyDetails, setStandBusyDetails] = useState<Record<string, unknown> | null>(null);
  const [queueActiveDetails, setQueueActiveDetails] = useState<Record<string, unknown> | null>(null);
  const request = useRef({ fingerprint: "", id: "" });
  const test = testsQ.data?.items.find((item) => item.id === testId);
  const resolvedStand = debug ? standId : test?.pinned_stand_id ?? "";
  const standDepartmentId = standsQ.data?.find((item) => item.id === resolvedStand)?.department_id;
  const canForce = canForceStandLaunch(persona, standDepartmentId);
  async function selectVersion(value: string) {
    setRc(value); setKernel(""); setError("");
    const known = versionsQ.data?.items.find((item) => item.id === value)?.kernels ?? [];
    if (known.length) { setKernel(known[0]); return; }
    setBusy(true);
    try {
      const version = await resolveOsKernels(value);
      setDetected((previous) => ({ ...previous, [value]: version.kernels }));
      setKernel(version.kernels[0] ?? "");
    } catch (error) { setError(apiErrMsg(error, "Не удалось обнаружить ядра ОС")); }
    finally { setBusy(false); }
  }
  async function attemptLaunch(options: { force?: boolean; onActiveQueue?: ActiveQueueMode } = {}) {
    const body = {
      test_id: testId, stand_id: resolvedStand, os_version_id: rc.trim(), kernel: kernel.trim(), debug_mode: debug,
      force: options.force ?? false,
      ...(options.onActiveQueue ? { on_active_queue: options.onActiveQueue } : {}),
    };
    const fingerprint = JSON.stringify(body);
    if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
    setBusy(true); setError(""); setStpPrompt(null); setStandBusyDetails(null); setQueueActiveDetails(null);
    try {
      onLaunched((await launchQueueItem({ ...body, request_id: request.current.id })).id);
    } catch (err) {
      const runId = err instanceof ApiError && err.errorCode === "TEST_NOT_IN_STP" ? err.details?.stp_test_run_id : undefined;
      if (typeof runId === "string") {
        setStpPrompt({ kind: "add", runId });
      } else if (err instanceof ApiError && err.errorCode === "STP_RUN_NOT_FOUND") {
        setStpPrompt({ kind: "not_generated" });
      } else if (err instanceof ApiError && err.errorCode === "STAND_BUSY") {
        setStandBusyDetails(err.details ?? {});
      } else if (err instanceof ApiError && err.errorCode === "STAND_QUEUE_ACTIVE") {
        setQueueActiveDetails(err.details ?? {});
      } else {
        setError(apiErrMsg(err, "Не удалось запустить тест"));
      }
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    await attemptLaunch();
  }
  async function addToStpAndRetry(runId: string) {
    setAddBusy(true);
    try {
      const op = await addTestToStp(runId, { test_id: testId });
      if (op.status === "succeeded") { await attemptLaunch(); }
      else { setStpPrompt(null); setError(`Добавление в СТП: ${op.status}${op.last_error ? " — " + op.last_error : ""}`); }
    } catch (err) {
      setError(apiErrMsg(err, "Не удалось добавить тест в СТП"));
    } finally {
      setAddBusy(false);
    }
  }
  const unavailable = !debug && test && test.readiness !== "ready";
  return <Modal open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title="Одиночный запуск" width="md">
    <form onSubmit={submit} className="grid gap-4">
      {(testsQ.error || standsQ.error || versionsQ.error) && <div role="alert" className="text-danger text-xs">Не удалось загрузить каталог или стенды. <Button size="sm" type="button" onClick={() => { testsQ.refetch(); standsQ.refetch(); versionsQ.refetch(); }}>Повторить загрузку</Button></div>}
      <label className="grid gap-1 text-sm">Тест
        <Dropdown mode="single" placeholder="Выберите тест" value={testId} onChange={setTestId} options={(testsQ.data?.items ?? []).map((item) => ({ value: item.id, label: `${item.full_name} · ${item.code}` }))} />
      </label>
      <label className="flex items-center gap-2 text-sm"><Checkbox checked={debug} onChange={(event) => setDebug(event.target.checked)} />Debug</label>
      <label className="grid gap-1 text-sm">Стенд
        <Dropdown mode="single" placeholder="Выберите стенд" value={resolvedStand} onChange={setStandId} disabled={!debug} options={(standsQ.data ?? []).map((item) => {
          const server = item.server as { display_name?: string; hostname?: string } | undefined;
          return { value: item.id, label: server?.display_name ?? server?.hostname ?? "Имя стенда недоступно" };
        })} />
      </label>
      <label className="grid gap-1 text-sm">РЦ<Dropdown mode="single" placeholder="Выберите РЦ" value={rc} onChange={selectVersion} disabled={busy} options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))} /></label>
      <label className="grid gap-1 text-sm">Ядро<Dropdown mode="single" placeholder="Выберите ядро" value={kernel} onChange={setKernel} options={(detected[rc] ?? versionsQ.data?.items.find((item) => item.id === rc)?.kernels ?? []).map((value) => ({ value, label: value }))} /></label>
      {test && <div className="text-xs text-dim">Режим: <span className="font-medium">{test.mode === "smolensk" ? "Смоленск" : "Орёл"}</span> — фиксирован на самом тесте.</div>}
      <div className="text-xs text-dim">{debug ? "Отладка вне прогона: результат не записывается в СТП и Zephyr." : "Запуск вне прогона: результат записывается в СТП выбранного РЦ, ядра и режима теста. Тест должен входить в эту СТП."}</div>
      {unavailable && <div className="text-xs text-warn">Статус теста допускает только debug.</div>}
      {!debug && test && !resolvedStand && <div className="text-xs text-warn">Для обычного запуска привяжите тест к стенду в каталоге.</div>}
      {stpPrompt?.kind === "add" && (
        <div className="text-xs grid gap-2 surface-2 border border-token rounded p-2">
          <div className="text-warn">Тест отсутствует в выбранной СТП.</div>
          <Button type="button" size="sm" variant="primary" disabled={addBusy} onClick={() => addToStpAndRetry(stpPrompt.runId)}>
            {addBusy ? "Добавляем…" : "Добавить в СТП и запустить"}
          </Button>
        </div>
      )}
      {stpPrompt?.kind === "not_generated" && (
        <div className="text-xs text-warn">
          Для этого стенда/РЦ/ядра/режима ещё не сгенерирована СТП — сначала выполните генерацию в разделе «Тестирование → СТП».
        </div>
      )}
      {error && <div role="alert" className="text-xs text-danger">{error}</div>}
      {standBusyDetails && (
        <div role="alert" className="text-xs text-danger"><StandBusyNotice details={standBusyDetails} suffix="запуск отклонён." /></div>
      )}
      {standBusyDetails && (
        <StandBusyPrompt details={standBusyDetails} canTakeover={canForce} busy={busy} onTakeover={() => attemptLaunch({ force: true })} />
      )}
      {queueActiveDetails && (
        <QueueActivePrompt details={queueActiveDetails} canChoose={canForce} busy={busy} onChoose={(mode) => attemptLaunch({ onActiveQueue: mode })} />
      )}
      <Button type="submit" variant="primary" disabled={busy || !testId || !resolvedStand || !rc.trim() || !kernel.trim() || !!unavailable}>{busy ? "Постановка в очередь…" : "Запустить тест"}</Button>
    </form>
  </Modal>;
}
