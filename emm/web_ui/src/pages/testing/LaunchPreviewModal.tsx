/**
 * Превью запуска теста: что уйдёт воркеру для
 * выбранных стенда, РЦ и ядра — переменные, dates.conf, файлы на стенде,
 * команды запуска и остановки. Собирает сервис теми же функциями, что и
 * настоящее задание (`POST /test-definitions/{id}/launch-preview`); секреты
 * приходят уже замаскированными (`***`). В очередь ничего не ставится.
 */
import { useState } from "react";
import { AlertCircle, Eye, Loader2 } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listOsVersions, resolveOsKernels } from "@/api/server/osVersions";
import { previewTestLaunch } from "@/api/testing/testDefinitions";
import { listTestSteps } from "@/api/testing/testSteps";
import { standName } from "@/api/testing/standCatalogue";
import type { LaunchPreview, LaunchPreviewVariable, TestDefinition, TestStand } from "@/api/testing/types";

const MODE_OPTIONS = [
  { value: "orel", label: "Орёл" },
  { value: "smolensk", label: "Смоленск" },
];

const FILE_ROLE_LABELS: Record<string, string> = {
  script: "скрипт запуска",
  token: "git-токен",
  dates: "dates.conf",
  testenv_marker: "маркер testenv",
  command_file: "команда (testenv)",
  extra: "файл профиля",
};

const STAGE_LABELS: Record<string, string> = {
  launch_profile: "Профиль запуска",
  paths: "Пути профиля",
  dates: "dates.conf",
  git_token: "git-токен",
  launch: "Скрипт и команды",
  stand_setup: "Настройка стенда",
};

function sourceLabel(row: LaunchPreviewVariable): string {
  if (row.source === "claim") return "задание (профиль запуска)";
  if (row.source === "override") return `override слота №${(row.slot_position ?? 0) + 1}`;
  return row.source;
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1">
      <div className="text-xs font-medium text-dim">{title}</div>
      {children}
    </section>
  );
}

function Code({ text, label }: { text: string | null | undefined; label: string }) {
  if (text === null || text === undefined) {
    return <div className="text-xs text-warn">не собрано — см. ошибки выше</div>;
  }
  return (
    <pre
      aria-label={label}
      className="surface-2 border border-token rounded p-2 mono text-[11px] whitespace-pre-wrap break-all max-h-64 overflow-auto"
    >
      {text}
    </pre>
  );
}

export function LaunchPreviewModal({
  test,
  stands,
  onClose,
}: {
  test: TestDefinition;
  stands: TestStand[];
  onClose: () => void;
}) {
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), []);
  // У многоступенчатого теста превью собирается для выбранного шага.
  const stepsQ = useQuery(() => listTestSteps(test.id), [test.id]);
  const steps = stepsQ.data ?? [];
  const [stepIndex, setStepIndex] = useState("0");
  const [standId, setStandId] = useState(test.pinned_stand_id ?? "");
  const [rc, setRc] = useState("");
  const [detected, setDetected] = useState<Record<string, string[]>>({});
  const [kernel, setKernel] = useState("");
  const [mode, setMode] = useState(test.mode || "orel");
  const [debug, setDebug] = useState(false);
  const [testenv, setTestenv] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<LaunchPreview | null>(null);

  const kernels = detected[rc] ?? versionsQ.data?.items.find((item) => item.id === rc)?.kernels ?? [];

  async function selectVersion(value: string) {
    setRc(value);
    setKernel("");
    setError("");
    const known = versionsQ.data?.items.find((item) => item.id === value)?.kernels ?? [];
    if (known.length) {
      setKernel(known[0]);
      return;
    }
    setBusy(true);
    try {
      const version = await resolveOsKernels(value);
      setDetected((previous) => ({ ...previous, [value]: version.kernels }));
      setKernel(version.kernels[0] ?? "");
    } catch (e) {
      setError(apiErrMsg(e, "Не удалось обнаружить ядра ОС"));
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !standId || !rc || !kernel.trim()) return;
    setBusy(true);
    setError("");
    try {
      setPreview(await previewTestLaunch(test.id, {
        stand_id: standId, os_version_id: rc, kernel: kernel.trim(), mode, debug, testenv,
        step_index: Number(stepIndex) || 0,
      }));
    } catch (err) {
      setPreview(null);
      setError(apiErrMsg(err, "Не удалось собрать превью"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => !next && !busy && onClose()}
      title={`Превью запуска · ${test.code}`}
      subtitle="Что уйдёт воркеру: переменные, dates.conf, файлы на стенде и команды. Секреты — ***."
      icon={<Eye className="w-5 h-5 text-accent" />}
      width="lg"
    >
      <div className="flex flex-col gap-4">
        <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {versionsQ.error && (
            <div role="alert" className="text-xs text-danger md:col-span-2">
              {apiErrMsg(versionsQ.error, "Каталог версий ОС не загрузился")}
            </div>
          )}
          <label className="grid gap-1 text-sm">Стенд
            <Dropdown
              mode="single"
              searchable
              placeholder="Выберите стенд"
              value={standId}
              onChange={setStandId}
              options={stands.map((s) => ({ value: s.id, label: standName(s) }))}
            />
          </label>
          <label className="grid gap-1 text-sm">Режим
            <Dropdown mode="single" options={MODE_OPTIONS} value={mode} onChange={(v) => v && setMode(v)} />
          </label>
          <label className="grid gap-1 text-sm">РЦ
            <Dropdown
              mode="single"
              searchable
              placeholder="Выберите РЦ"
              value={rc}
              onChange={selectVersion}
              disabled={busy}
              options={(versionsQ.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
          </label>
          <label className="grid gap-1 text-sm">Ядро
            <Dropdown
              mode="single"
              placeholder="Выберите ядро"
              value={kernel}
              onChange={setKernel}
              options={kernels.map((value) => ({ value, label: value }))}
            />
          </label>
          {steps.length > 1 && (
            <label className="grid gap-1 text-sm md:col-span-2">Шаг
              <Dropdown
                mode="single"
                searchable={false}
                sortOptions={false}
                value={stepIndex}
                onChange={setStepIndex}
                options={steps.map((step, index) => ({
                  value: String(index),
                  label: `${index + 1}/${steps.length}${step.name ? ` · ${step.name}` : ""}${step.run_mode === "rerun" ? " · повторный запуск" : ""}`,
                }))}
              />
            </label>
          )}
          <div className="flex items-center gap-4 text-sm md:col-span-2">
            <label className="flex items-center gap-2">
              <Checkbox checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Debug
            </label>
            <label className="flex items-center gap-2">
              <Checkbox checked={testenv} onChange={(e) => setTestenv(e.target.checked)} /> Testenv (только подготовка)
            </label>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              className="ml-auto inline-flex items-center gap-1.5"
              disabled={busy || !standId || !rc || !kernel.trim()}
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
              Показать
            </Button>
          </div>
        </form>

        {error && <div role="alert" className="text-xs text-danger">{error}</div>}
        {preview && <PreviewResult preview={preview} />}
      </div>
    </Modal>
  );
}

export function PreviewResult({ preview }: { preview: LaunchPreview }) {
  return (
    <div className="flex flex-col gap-3" data-testid="launch-preview-result">
      {preview.errors.length > 0 && (
        <div role="alert" className="alert alert-danger flex items-start gap-2 text-xs">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1 flex flex-col gap-1">
            <div>Запуск с этими параметрами провалится на подготовке задания:</div>
            {preview.errors.map((e, index) => (
              <div key={index}>
                <span className="font-medium">{STAGE_LABELS[e.stage] ?? e.stage}</span>
                {" — "}
                <span className="mono">{e.error_code}</span>: {e.message}
                {typeof e.details.hint === "string" && <span className="text-dim"> ({e.details.hint})</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="text-xs text-dim flex items-center gap-2 flex-wrap">
        <span className="mono">
          RC={preview.launch_context.RC} · KERNEL={preview.launch_context.KERNEL} · MODE={preview.launch_context.MODE}
        </span>
        {preview.launch_profile && (
          <span>
            · профиль «{preview.launch_profile.name ?? preview.launch_profile.profile_id}» v{preview.launch_profile.version}
          </span>
        )}
        {preview.step && preview.step.count > 1 && (
          <span>
            · шаг {preview.step.index + 1}/{preview.step.count}{preview.step.name ? ` «${preview.step.name}»` : ""}
            {preview.step.run_mode === "rerun" ? " (повторный запуск)" : ""}
          </span>
        )}
        {preview.use_pty !== null && <span>· pty: {preview.use_pty ? "да" : "нет"}</span>}
        {preview.debug && <Badge kind="warn">debug</Badge>}
        {preview.testenv && <Badge kind="warn">testenv</Badge>}
      </div>

      <Block title="Команда запуска">
        <Code label="Команда запуска" text={preview.launch_command_masked} />
      </Block>

      <Block title="dates.conf">
        <Code label="dates.conf" text={preview.dates_content_masked} />
      </Block>

      {/*: шаг настройки стенда — выполняется при подготовке стенда,
          раньше starter.sh. */}
      {preview.stand_setup && (
        <Block title="Настройка стенда">
          <div className="text-xs text-dim">
            {preview.stand_setup.kernel_cmdline_extra.length > 0 && (
              <>Параметры ядра: <span className="mono">{preview.stand_setup.kernel_cmdline_extra.join(" ")}</span> · </>
            )}
            от {preview.stand_setup.run_as === "root" ? "root" : "тестовой учётки"} ·{" "}
            {preview.stand_setup.phase === "after_boot" ? "после подготовки" : "до смены ядра"}
            {preview.stand_setup.reboot_after ? " · затем перезагрузка" : ""}
          </div>
          {preview.stand_setup.script && <Code label="Скрипт настройки стенда" text={preview.stand_setup.script} />}
        </Block>
      )}

      <Block title={`Файлы на стенде · ${preview.files.length}`}>
        {preview.files.length === 0 && <div className="text-xs text-warn">не собраны — см. ошибки выше</div>}
        {preview.files.map((file) => (
          <div key={file.path} className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-xs flex-wrap">
              <span className="mono">{file.path}</span>
              <span className="text-dim">{FILE_ROLE_LABELS[file.role] ?? file.role}</span>
              <span className="mono text-dim">{file.mode}</span>
              {file.sensitive && <Badge kind="warn">секрет</Badge>}
            </div>
            <Code label={file.path} text={file.content} />
          </div>
        ))}
        {preview.cleanup_globs.length > 0 && (
          <div className="text-xs text-dim">
            Перед записью удаляются: <span className="mono">{preview.cleanup_globs.join(" ")}</span>
          </div>
        )}
      </Block>

      <Block title="Команда остановки">
        <Code label="Команда остановки" text={preview.stop_command} />
      </Block>

      <Block title={`Переменные · ${preview.variables.length}`}>
        <div className="overflow-auto max-h-80 border border-token rounded">
          <table className="mini">
            <thead>
              <tr>
                <th>Код</th>
                <th>Значение</th>
                <th>Источник</th>
              </tr>
            </thead>
            <tbody>
              {preview.variables.map((row) => (
                <tr key={`${row.code}:${row.source}:${row.slot_position ?? ""}`}>
                  <td className="mono" title={row.label ?? undefined}>{row.code}</td>
                  <td className="mono break-all">
                    {row.value === "" ? <span className="text-dim">(пусто)</span> : row.value}
                    {row.sensitive && <> <Badge kind="warn">секрет</Badge></>}
                  </td>
                  <td className="text-xs text-dim">{sourceLabel(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Block>
    </div>
  );
}
