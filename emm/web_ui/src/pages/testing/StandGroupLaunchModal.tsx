import { useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listOsVersions, resolveOsKernels } from "@/api/server/osVersions";
import { createTestRun, previewTestRun } from "@/api/testing/testRuns";
import type { TestRunCreateResponse } from "@/api/testing/types";

const SKIP_LABELS: Record<string, string> = {
  skip_debug_required: "Требуется debug",
  skip_stand_inactive: "Стенд неактивен",
  skip_not_in_stp: "Не входит в состав СТП",
};

/**
 * Запуск всех тестов, закреплённых через `pinned_stand_id` за одним стендом
 * (§E3/E4 плана 2026-09-11) — аналог `_standN group` из allta_app. Стенд
 * зафиксирован пропом, оператор выбирает только РЦ/ядро/режим, смотрит
 * предпросмотр состава и подтверждает запуск кампании на одном стенде.
 */
export function StandGroupLaunchModal({
  standId,
  standLabel,
  onClose,
  onLaunched,
}: {
  standId: string;
  standLabel?: string;
  onClose: () => void;
  onLaunched: (id: string) => void;
}) {
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  const [rc, setRc] = useState("");
  const [detected, setDetected] = useState<Record<string, string[]>>({});
  const [kernel, setKernel] = useState("");
  const [mode, setMode] = useState<"orel" | "smolensk">("orel");
  const [final, setFinal] = useState(false);
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
    () => previewTestRun({ os_version_id: rc.trim(), mode, kernel: kernel.trim(), test_run_stands: [standId], final }),
    [standId, rc, kernel, mode, final],
    { enabled: ready },
  );
  const entries = previewQ.data?.entries ?? [];
  const launchable = entries.filter((entry) => entry.action === "launch");
  const emptyStand = previewQ.data?.stands_without_tests.includes(standId) ?? false;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !ready || launchable.length === 0) return;
    const body = { os_version_id: rc.trim(), kernel: kernel.trim(), mode, test_run_stands: [standId], final };
    const fingerprint = JSON.stringify(body);
    if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
    setBusy(true); setError("");
    try { setResult(await createTestRun({ ...body, request_id: request.current.id })); }
    catch (err) { setError(apiErrMsg(err, "Не удалось запустить группу")); }
    finally { setBusy(false); }
  }

  if (result) {
    const hasWarnings = result.stands_without_tests.length > 0 || result.enqueue_errors.length > 0;
    return (
      <Modal open onOpenChange={(open) => { if (!open) onLaunched(result.id); }} title="Группа запущена" width="md">
        <div className="grid gap-3">
          <div className="text-sm">Кампания <span className="mono">{result.id}</span> создана.</div>
          {hasWarnings && (
            <div className="text-xs text-warn grid gap-1">
              {result.stands_without_tests.length > 0 && <div>Стенд остался без единого закреплённого теста.</div>}
              {result.enqueue_errors.map((err) => <div key={`${err.stand_id}-${err.test_id}`}>{err.message}</div>)}
            </div>
          )}
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
        <label className="grid gap-1 text-sm">Режим
          <Dropdown mode="single" value={mode} onChange={(value) => setMode(value as "orel" | "smolensk")} options={[{ value: "orel", label: "Орёл" }, { value: "smolensk", label: "Смоленск" }]} />
        </label>
        <label className="flex items-center gap-2 text-sm"><Checkbox checked={final} onChange={(event) => setFinal(event.target.checked)} />Финальный прогон</label>

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
                  <li key={`${entry.test_id}-${entry.kernel}`}>
                    <span className="font-medium">{entry.test_name}</span> <span className="mono">{entry.test_code}</span> · {entry.kernel} —{" "}
                    {entry.action === "launch"
                      ? <span className="text-ok">будет запущен</span>
                      : <span className="text-dim">{entry.reason ?? SKIP_LABELS[entry.action] ?? "пропущен"}</span>}
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
