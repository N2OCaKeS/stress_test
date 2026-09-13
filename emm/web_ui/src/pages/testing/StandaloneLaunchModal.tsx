import { useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listOsVersions, resolveOsKernels } from "@/api/server/osVersions";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import { listTestStands, getTestStand } from "@/api/testing/testStands";
import { launchQueueItem } from "@/api/testing/queueItems";

export function StandaloneLaunchModal({ onClose, onLaunched }: { onClose: () => void; onLaunched: (id: string) => void }) {
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
  const [mode, setMode] = useState<"orel" | "smolensk">("orel");
  const [debug, setDebug] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef({ fingerprint: "", id: "" });
  const test = testsQ.data?.items.find((item) => item.id === testId);
  const resolvedStand = debug ? standId : test?.pinned_stand_id ?? "";
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
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const body = { test_id: testId, stand_id: resolvedStand, os_version_id: rc.trim(), kernel: kernel.trim(), mode, debug_mode: debug };
    const fingerprint = JSON.stringify(body);
    if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
    setBusy(true); setError("");
    try { onLaunched((await launchQueueItem({ ...body, request_id: request.current.id })).id); }
    catch (error) { setError(apiErrMsg(error, "Не удалось запустить тест")); }
    finally { setBusy(false); }
  }
  const unavailable = !debug && test && test.readiness !== "ready";
  return <Modal open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title="Одиночный запуск" width="md">
    <form onSubmit={submit} className="grid gap-4">
      {(testsQ.error || standsQ.error || versionsQ.error) && <div role="alert" className="text-danger text-xs">Не удалось загрузить каталог или стенды. <Button size="sm" type="button" onClick={() => { testsQ.refetch(); standsQ.refetch(); versionsQ.refetch(); }}>Повторить загрузку</Button></div>}
      <label className="grid gap-1 text-sm">Тест
        <Dropdown mode="single" placeholder="Выберите тест" value={testId} onChange={setTestId} options={(testsQ.data?.items ?? []).map((item) => ({ value: item.id, label: `${item.code} · ${item.full_name}` }))} />
      </label>
      <label className="flex items-center gap-2 text-sm"><Checkbox checked={debug} onChange={(event) => setDebug(event.target.checked)} />Debug</label>
      <label className="grid gap-1 text-sm">Стенд
        <Dropdown mode="single" placeholder="Выберите стенд" value={resolvedStand} onChange={setStandId} disabled={!debug} options={(standsQ.data ?? []).map((item) => {
          const server = item.server as { display_name?: string; hostname?: string } | undefined;
          return { value: item.id, label: server?.display_name ?? server?.hostname ?? item.server_id };
        })} />
      </label>
      <label className="grid gap-1 text-sm">РЦ<Dropdown mode="single" placeholder="Выберите РЦ" value={rc} onChange={selectVersion} disabled={busy} options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))} /></label>
      <label className="grid gap-1 text-sm">Ядро<Dropdown mode="single" placeholder="Выберите ядро" value={kernel} onChange={setKernel} options={(detected[rc] ?? versionsQ.data?.items.find((item) => item.id === rc)?.kernels ?? []).map((value) => ({ value, label: value }))} /></label>
      <label className="grid gap-1 text-sm">Режим<Dropdown mode="single" value={mode} onChange={(value) => setMode(value as "orel" | "smolensk")} options={[{ value: "orel", label: "Орёл" }, { value: "smolensk", label: "Смоленск" }]} /></label>
      <div className="text-xs text-dim">{debug ? "Отладка вне прогона: результат не записывается в СТП и Zephyr." : "Запуск вне прогона: результат записывается в СТП выбранного РЦ, ядра и режима. Тест должен входить в эту СТП."}</div>
      {unavailable && <div className="text-xs text-warn">Статус теста допускает только debug.</div>}
      {!debug && test && !resolvedStand && <div className="text-xs text-warn">Для обычного запуска привяжите тест к стенду в каталоге.</div>}
      {error && <div role="alert" className="text-xs text-danger">{error}</div>}
      <Button type="submit" variant="primary" disabled={busy || !testId || !resolvedStand || !rc.trim() || !kernel.trim() || !!unavailable}>{busy ? "Постановка в очередь…" : "Запустить тест"}</Button>
    </form>
  </Modal>;
}
