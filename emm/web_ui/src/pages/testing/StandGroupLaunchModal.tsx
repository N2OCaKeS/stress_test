import { useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listOsVersions, resolveOsKernels } from "@/api/server/osVersions";
import { createTestRun, previewTestRun } from "@/api/testing/testRuns";
import { addTestToStp } from "@/api/testing/stp";
import type { ActiveQueueMode, TestRunCreateResponse, TestRunPreviewEntry } from "@/api/testing/types";
import { usePersona } from "@/contexts/PersonaContext";
import { canForceStandLaunch } from "@/lib/rbac";
import { QueueActivePrompt, StandBusyNotice, StandBusyPrompt } from "./StandLaunchConflict";

const SKIP_LABELS: Record<string, string> = {
  skip_debug_required: "Требуется debug",
  skip_stand_inactive: "Стенд неактивен",
  skip_not_in_stp: "Не входит в состав СТП",
  skip_stp_not_generated: "СТП для этого контекста не сгенерирована",
};

/** Кнопка «Добавить в СТП» для одной пропущенной записи предпросмотра (§E2, случай (a)).
 * Каждая запись может относиться к своему прогону СТП — добавляем именно в её `stp_test_run_id`. */
function StpAddInlineButton({ entry, onAdded }: { entry: TestRunPreviewEntry; onAdded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!entry.stp_test_run_id) return null;
  async function submit() {
    setBusy(true); setErr("");
    try {
      const op = await addTestToStp(entry.stp_test_run_id as string, { test_id: entry.test_id });
      if (op.status === "succeeded") onAdded();
      else setErr(`${op.status}${op.last_error ? " — " + op.last_error : ""}`);
    } catch (e) { setErr(apiErrMsg(e, "Не удалось добавить в СТП")); }
    finally { setBusy(false); }
  }
  return (
    <span className="inline-flex items-center gap-1">
      <Button size="sm" type="button" disabled={busy} onClick={submit}>{busy ? "Добавляем…" : "Добавить в СТП"}</Button>
      {err && <span className="text-danger">{err}</span>}
    </span>
  );
}

/**
 * Запуск всех тестов, закреплённых через `pinned_stand_id` за одним стендом
 * (§E3/E4 плана 2026-09-11) — аналог `_standN group` из allta_app. Стенд
 * зафиксирован пропом, оператор выбирает только РЦ/ядро — режим у каждого
 * теста свой (фиксирован в каталоге), смотрит предпросмотр состава и
 * подтверждает запуск кампании на одном стенде.
 */
export function StandGroupLaunchModal({
  standId,
  standLabel,
  standDepartmentId,
  onClose,
  onLaunched,
}: {
  standId: string;
  standLabel?: string;
  /** Отдел стенда: если он не совпадает с отделом персоны, кнопки забора/замены очереди не показываются. */
  standDepartmentId?: string | null;
  onClose: () => void;
  onLaunched: (id: string) => void;
}) {
  const { persona } = usePersona();
  const canForce = canForceStandLaunch(persona, standDepartmentId);
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  const [rc, setRc] = useState("");
  const [detected, setDetected] = useState<Record<string, string[]>>({});
  const [kernel, setKernel] = useState("");
  const [final, setFinal] = useState(false);
  const [debug, setDebug] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<TestRunCreateResponse | null>(null);
  const request = useRef({ fingerprint: "", id: "" });

  async function selectVersion(value: string) {
    setRc(value); setKernel(""); setError("");
    const known = versionsQ.data?.items.find((item) => item.id === value)?.kernels ?? [];
    if (known.length) { setKernel(known[0]); return; }
    setBusy(true);
    try {
      const version = await resolveOsKernels(value);
      setDetected((previous) => ({ ...previous, [value]: version.kernels }));
      setKernel(version.kernels[0] ?? "");
    } catch (err) { setError(apiErrMsg(err, "Не удалось обнаружить ядра ОС")); }
    finally { setBusy(false); }
  }

  const ready = !!rc.trim() && !!kernel.trim();
  const previewQ = useQuery(
    () => previewTestRun({ os_version_id: rc.trim(), kernel: kernel.trim(), test_run_stands: [standId], final, debug }),
    [standId, rc, kernel, final, debug],
    { enabled: ready },
  );
  const entries = previewQ.data?.entries ?? [];
  const launchable = entries.filter((entry) => entry.action === "launch");
  const emptyStand = previewQ.data?.stands_without_tests.includes(standId) ?? false;

  async function launch(options: { force?: boolean; onActiveQueue?: ActiveQueueMode } = {}) {
    const body = {
      os_version_id: rc.trim(), kernel: kernel.trim(), test_run_stands: [standId], final, debug,
      force: options.force ?? false,
      ...(options.onActiveQueue ? { on_active_queue: options.onActiveQueue } : {}),
    };
    const fingerprint = JSON.stringify(body);
    if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
    setBusy(true); setError("");
    try { setResult(await createTestRun({ ...body, request_id: request.current.id })); }
    catch (err) { setError(apiErrMsg(err, "Не удалось запустить группу")); }
    finally { setBusy(false); }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !ready || launchable.length === 0) return;
    await launch();
  }

  if (result) {
    const busyErrors = result.enqueue_errors.filter((err) => err.error_code === "STAND_BUSY");
    const queueErrors = result.enqueue_errors.filter((err) => err.error_code === "STAND_QUEUE_ACTIVE");
    const otherErrors = result.enqueue_errors.filter((err) => err.error_code !== "STAND_BUSY" && err.error_code !== "STAND_QUEUE_ACTIVE");
    const hasWarnings = result.stands_without_tests.length > 0 || otherErrors.length > 0;
    return (
      <Modal open onOpenChange={(open) => { if (!open) onLaunched(result.id); }} title="Группа запущена" width="md">
        <div className="grid gap-3">
          <div className="text-sm">Кампания <span className="mono">{result.id}</span> создана.</div>
          {busyErrors.length > 0 && (
            <div className="text-xs text-warn grid gap-2">
              <div><StandBusyNotice details={busyErrors[0].details} suffix="тесты не запущены." /></div>
              <StandBusyPrompt details={busyErrors[0].details} canTakeover={canForce} busy={busy} onTakeover={() => launch({ force: true })} />
            </div>
          )}
          {queueErrors.length > 0 && (
            <QueueActivePrompt details={queueErrors[0].details} canChoose={canForce} busy={busy} onChoose={(mode) => launch({ onActiveQueue: mode })} />
          )}
          {hasWarnings && (
            <div className="text-xs text-warn grid gap-1">
              {result.stands_without_tests.length > 0 && <div>Стенд остался без единого закреплённого теста.</div>}
              {otherErrors.map((err) => <div key={`${err.stand_id}-${err.test_id}`}>{err.message}</div>)}
            </div>
          )}
          {error && <div role="alert" className="text-xs text-danger">{error}</div>}
          <Button type="button" variant="primary" onClick={() => onLaunched(result.id)}>Готово</Button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title="Запустить все тесты стенда" subtitle={standLabel} width="md">
      <form onSubmit={submit} className="grid gap-4">
        {versionsQ.error && <div role="alert" className="text-xs text-danger">Не удалось загрузить каталог РЦ. <Button size="sm" type="button" onClick={() => versionsQ.refetch()}>Повторить загрузку</Button></div>}
        <label className="grid gap-1 text-sm">РЦ
          <Dropdown mode="single" placeholder="Выберите РЦ" value={rc} onChange={selectVersion} disabled={busy} options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))} />
        </label>
        <label className="grid gap-1 text-sm">Ядро
          <Dropdown mode="single" placeholder="Выберите ядро" value={kernel} onChange={setKernel} options={(detected[rc] ?? versionsQ.data?.items.find((item) => item.id === rc)?.kernels ?? []).map((value) => ({ value, label: value }))} />
        </label>
        <label className="flex items-center gap-2 text-sm"><Checkbox checked={final} onChange={(event) => setFinal(event.target.checked)} />Финальный прогон</label>
        <label className="flex items-center gap-2 text-sm"><Checkbox checked={debug} onChange={(event) => setDebug(event.target.checked)} />Debug</label>
        <div className="text-xs text-dim">{debug ? "Отладка вне прогона: допуск по СТП и статус готовности не проверяются — весь пул стенда уходит в очередь как есть." : "Обычный запуск: каждый тест должен входить в активную СТП выбранного РЦ/ядра/режима."}</div>

        {ready && (
          <div className="surface-2 border border-token rounded p-3 grid gap-2">
            <div className="text-xs text-dim">Состав</div>
            {previewQ.loading ? (
              <div className="text-xs text-dim">Загружаем состав…</div>
            ) : previewQ.error ? (
              <div className="text-xs text-danger">{apiErrMsg(previewQ.error, "Не удалось получить состав")}</div>
            ) : emptyStand || entries.length === 0 ? (
              <div className="text-xs text-dim">К этому стенду не привязано ни одного теста.</div>
            ) : (
              <ul className="grid gap-1 text-xs">
                {entries.map((entry) => (
                  <li key={`${entry.test_id}-${entry.kernel}`} className="flex items-center justify-between gap-2">
                    <span>
                      <span className="font-medium">{entry.test_name}</span> <span className="mono">{entry.test_code}</span> · {entry.kernel} · {entry.mode === "smolensk" ? "Смоленск" : "Орёл"} —{" "}
                      {entry.action === "launch"
                        ? <span className="text-ok">будет запущен</span>
                        : <span className="text-dim">{entry.reason ?? SKIP_LABELS[entry.action] ?? "пропущен"}</span>}
                    </span>
                    {entry.action === "skip_not_in_stp" && (
                      <StpAddInlineButton entry={entry} onAdded={() => previewQ.refetch()} />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <div role="alert" className="text-xs text-danger">{error}</div>}
        <Button type="submit" variant="primary" disabled={busy || !ready || previewQ.loading || launchable.length === 0}>
          {busy ? "Постановка в очередь…" : "Запустить группу"}
        </Button>
      </form>
    </Modal>
  );
}
