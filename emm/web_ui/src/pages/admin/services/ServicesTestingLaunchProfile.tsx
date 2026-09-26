/**
 * Профиль запуска теста (`/admin/services.testing.launch_profile`, D16).
 *
 * Текст `starter.sh`, клонирование ветки, пути на стенде, команды запуска и
 * остановки, pty и testenv — данные в testing_service, а не код воркера.
 * Правка содержимого всегда создаёт новую версию; уже запущенный тест
 * остаётся на своей. Здесь же — история версий с построчным сравнением.
 *
 * Общий профиль (для всех отделов) меняет только admin testing_service;
 * отдел может завести свой на его основе и сделать его профилем по
 * умолчанию. Источник истины — `/api/testing/v1/launch-profiles`.
 */

import { useMemo, useState } from "react";
import { AlertCircle, History, Rocket } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  createLaunchProfile,
  createLaunchProfileVersion,
  listLaunchProfileVersions,
  listLaunchProfiles,
  updateLaunchProfile,
} from "@/api/testing/launchProfiles";
import type { LaunchProfile, LaunchProfileVersion, LaunchProfileVersionInput } from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { personaDeptId } from "@/lib/rbac";
import { diffLines } from "@/lib/diffLines";

const PATH_LABELS: [keyof LaunchProfileVersionInput["paths"], string][] = [
  ["script", "starter.sh"],
  ["dates", "Файл аргументов теста (dates)"],
  ["token", "Файл git-токена"],
  ["testenv_marker", "testenv-маркер"],
  ["command_file", "Файл команды (testenv-режим)"],
];

const MODE_OPTIONS = [
  { value: "branch", label: "Только ветка теста (--single-branch, как легаси)" },
  { value: "full", label: "Все ветки" },
];

function toInput(v: LaunchProfileVersion): LaunchProfileVersionInput {
  return {
    comment: "",
    starter_script: v.starter_script,
    clone: { repo_url: v.clone.repo_url, mode: v.clone.mode, depth: v.clone.depth ?? null },
    paths: { ...v.paths },
    launch_command_template: v.launch_command_template,
    stop_command_template: v.stop_command_template,
    stop_grace_seconds: v.stop_grace_seconds,
    use_pty: v.use_pty,
    testenv: { ...v.testenv },
    rerun_script: v.rerun_script ?? "",
    extra_files: (v.extra_files ?? []).map((f) => ({ ...f })),
  };
}

function sameContent(a: LaunchProfileVersionInput, b: LaunchProfileVersionInput): boolean {
  const strip = (x: LaunchProfileVersionInput) => JSON.stringify({ ...x, comment: undefined });
  return strip(a) === strip(b);
}

export function ServicesTestingLaunchProfile() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Rocket className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Профиль запуска</h1>
          <div className="text-xs text-dim">testing_service · starter.sh, пути на стенде, запуск и остановка</div>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? <LaunchProfileEditor departmentId={departmentId} /> : (
          <div className="text-sm text-dim">Нет привязанного отдела.</div>
        )}
      </div>
    </div>
  );
}

function LaunchProfileEditor({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const profilesQ = useQuery(async () => (await listLaunchProfiles(departmentId)).items, [departmentId]);
  const profiles = useMemo(() => profilesQ.data ?? [], [profilesQ.data]);
  const [selectedId, setSelectedId] = useState<string>("");
  const selected: LaunchProfile | undefined =
    profiles.find((p) => p.id === selectedId) ??
    profiles.find((p) => p.department_id === departmentId && p.is_default) ??
    profiles.find((p) => p.is_default) ??
    profiles[0];

  const [draft, setDraft] = useState<LaunchProfileVersionInput | null>(null);
  const [draftFor, setDraftFor] = useState<LaunchProfileVersion | null>(null);
  const current = selected?.current_version ?? null;
  // Черновик сбрасывается во время рендера при смене профиля/версии (как в
  // соседних формах администрирования).
  if (current !== draftFor) {
    setDraftFor(current);
    setDraft(current ? toInput(current) : null);
  }

  const versionsQ = useQuery(
    async () => (selected ? await listLaunchProfileVersions(selected.id) : []),
    [selected?.id, current?.id],
  );
  const [compareId, setCompareId] = useState<string>("");
  const [pending, setPending] = useState(false);

  const dirty = draft && current ? !sameContent(draft, toInput(current)) : false;
  const isGlobal = selected?.department_id == null;

  function patch(changes: Partial<LaunchProfileVersionInput>) {
    setDraft((prev) => (prev ? { ...prev, ...changes } : prev));
  }

  async function run(action: () => Promise<unknown>, successText: string, errorText: string) {
    setPending(true);
    try {
      await action();
      toast.success(successText);
      profilesQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, errorText));
    } finally {
      setPending(false);
    }
  }

  function validDraft(): string | null {
    if (!draft) return "Нет данных профиля";
    if (!draft.starter_script.trim()) return "Текст starter.sh не может быть пустым";
    if (!draft.launch_command_template.trim()) return "Команда запуска не может быть пустой";
    if (!draft.stop_command_template.trim()) return "Команда остановки не может быть пустой";
    if (!draft.clone.repo_url.trim()) return "Укажите URL репозитория";
    return null;
  }

  async function saveVersion() {
    const error = validDraft();
    if (error || !selected || !draft) {
      toast.error(error ?? "Нет профиля");
      return;
    }
    await run(
      () => createLaunchProfileVersion(selected.id, draft),
      "Сохранена новая версия профиля",
      "Не удалось сохранить версию профиля",
    );
  }

  async function copyToDepartment() {
    const error = validDraft();
    if (error || !draft || !selected) {
      toast.error(error ?? "Нет профиля");
      return;
    }
    await run(
      () => createLaunchProfile({
        department_id: departmentId,
        name: `${selected.name} (отдел)`,
        is_default: true,
        version: { ...draft, comment: `Создан на основе «${selected.name}» v${current?.version ?? "?"}` },
      }),
      "Создан профиль отдела — он используется по умолчанию",
      "Не удалось создать профиль отдела",
    );
  }

  if (profilesQ.loading && !profilesQ.data) return <div className="text-xs text-dim">Загрузка…</div>;
  if (profilesQ.error != null && !profilesQ.data) {
    return (
      <div className="alert-danger text-sm flex items-start gap-2">
        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
        {apiErrMsg(profilesQ.error, "Профили запуска не загрузились")}
      </div>
    );
  }
  if (!selected || !draft) return <div className="text-sm text-dim">Профилей запуска нет.</div>;

  const compareWith = (versionsQ.data ?? []).find((v) => v.id === compareId);

  return (
    <div className="flex flex-col gap-4 max-w-4xl">
      <div className="flex items-end gap-3 flex-wrap">
        <label className="flex flex-col gap-1 text-sm min-w-[280px]">
          <span className="field-label">Профиль</span>
          <Dropdown
            mode="single"
            searchable={false}
            options={profiles.map((p) => ({
              value: p.id,
              label: `${p.name}${p.department_id ? "" : " (общий)"}${p.is_default ? " · по умолчанию" : ""}`,
            }))}
            value={selected.id}
            onChange={(v) => {
              setSelectedId(v);
              setCompareId("");
            }}
          />
        </label>
        <div className="text-xs text-dim pb-2">
          Версия {current?.version ?? "—"}
          {isGlobal ? " · общий профиль: меняет только администратор тестирования" : ""}
        </div>
        {isGlobal && (
          <Button onClick={copyToDepartment} disabled={pending}>Создать профиль отдела на его основе</Button>
        )}
        {!isGlobal && !selected.is_default && (
          <Button
            onClick={() => run(
              () => updateLaunchProfile(selected.id, { is_default: true }),
              "Профиль стал профилем отдела по умолчанию",
              "Не удалось сделать профиль профилем по умолчанию",
            )}
            disabled={pending}
          >
            Сделать профилем отдела по умолчанию
          </Button>
        )}
      </div>

      <section className="card flex flex-col gap-3" aria-label="starter.sh">
        <div className="text-sm font-semibold">starter.sh</div>
        <div className="text-xs text-dim">
          Подстановки — <span className="mono">{"{{CODE}}"}</span>: переменные каталога и{" "}
          <span className="mono">{"{{GIT_TOKEN_PATH}} {{GIT_CLONE_ARGS}} {{GIT_REPO_URL}} {{STARTER_PATH}} {{DATES_PATH}}"}</span>.
          Позиционные аргументы — из команды запуска.
        </div>
        <textarea
          aria-label="Текст starter.sh"
          className="field-input mono text-xs"
          rows={18}
          value={draft.starter_script}
          onChange={(e) => patch({ starter_script: e.target.value })}
        />
      </section>

      <section className="card flex flex-col gap-3" aria-label="Повторный запуск">
        <div className="text-sm font-semibold">Повторный запуск (шаги многоступенчатого теста)</div>
        <div className="text-xs text-dim">
          Для шагов «повторный запуск»: код уже склонирован первым шагом, скрипт кладётся вместо starter.sh по
          тому же пути и запускается той же командой (аргументы те же). Подстановки —{" "}
          <span className="mono">{"{{CODE}}"}</span>, в т.ч. <span className="mono">{"{{STEP_INDEX}} {{STEP_NAME}}"}</span>.
          Пусто — такие шаги с этим профилем не запускаются.
        </div>
        <textarea
          aria-label="Скрипт повторного запуска"
          className="field-input mono text-xs"
          rows={10}
          value={draft.rerun_script ?? ""}
          onChange={(e) => patch({ rerun_script: e.target.value })}
        />
      </section>

      <section className="card flex flex-col gap-3" aria-label="Дополнительные файлы">
        <div className="text-sm font-semibold">Дополнительные файлы на стенде</div>
        <div className="text-xs text-dim">
          Кладутся на стенд вместе со starter.sh: путь — <span className="mono">{"{CODE}"}</span>, содержимое —{" "}
          <span className="mono">{"{{CODE}}"}</span> (например <span className="mono">{"{{TEST_PASSWORD}}"}</span> или
          адрес стенда сценария через переменную-ссылку на стенд). Файл с секретом в лог не пишется. Строка dates
          целиком — <span className="mono">{"{{DATES_INLINE}}"}</span>.
        </div>
        {(draft.extra_files ?? []).map((file, index) => {
          const files = draft.extra_files ?? [];
          const set = (changes: Partial<(typeof files)[number]>) =>
            patch({ extra_files: files.map((f, i) => (i === index ? { ...f, ...changes } : f)) });
          return (
            <div key={index} className="flex flex-col gap-2 border border-token rounded p-2" data-testid="extra-file">
              <div className="flex items-center gap-2">
                <input aria-label={`Путь файла ${index + 1}`} className="field-input mono flex-1" value={file.path}
                  onChange={(e) => set({ path: e.target.value })} placeholder="{TEST_HOME}/tokens.json" />
                <input aria-label={`Права файла ${index + 1}`} className="field-input mono w-20" value={file.mode}
                  onChange={(e) => set({ mode: e.target.value })} />
                <Toggle label="секрет" checked={file.sensitive} onChange={(e) => set({ sensitive: e.target.checked })} />
                <Button size="sm" onClick={() => patch({ extra_files: files.filter((_, i) => i !== index) })}>
                  Убрать
                </Button>
              </div>
              <textarea aria-label={`Содержимое файла ${index + 1}`} className="field-input mono text-xs" rows={3}
                value={file.content} onChange={(e) => set({ content: e.target.value })} />
            </div>
          );
        })}
        <div>
          <Button size="sm" onClick={() => patch({
            extra_files: [...(draft.extra_files ?? []), { path: "", content: "", mode: "0644", sensitive: false }],
          })}>
            Добавить файл
          </Button>
        </div>
      </section>

      <section className="card flex flex-col gap-3" aria-label="Клонирование">
        <div className="text-sm font-semibold">Клонирование ветки теста</div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">URL репозитория</span>
          <input
            className="field-input mono"
            value={draft.clone.repo_url}
            onChange={(e) => patch({ clone: { ...draft.clone, repo_url: e.target.value } })}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1 text-sm">
            <span className="field-label">Что клонировать</span>
            <Dropdown
              mode="single"
              searchable={false}
              sortOptions={false}
              options={MODE_OPTIONS}
              value={draft.clone.mode}
              onChange={(v) => patch({ clone: { ...draft.clone, mode: v as "branch" | "full" } })}
            />
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Глубина (--depth), пусто — вся история</span>
            <input
              className="field-input mono"
              inputMode="numeric"
              value={draft.clone.depth ?? ""}
              onChange={(e) => {
                const raw = e.target.value.trim();
                patch({ clone: { ...draft.clone, depth: /^\d+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null } });
              }}
            />
          </label>
        </div>
        <div className="text-xs text-dim">Токен git — credential интеграций отдела (git_credential_id).</div>
      </section>

      <section className="card flex flex-col gap-3" aria-label="Пути на стенде">
        <div className="text-sm font-semibold">Пути на стенде</div>
        <div className="text-xs text-dim">
          Подстановки <span className="mono">{"{TEST_HOME}"}</span> (тестовая учётка) и{" "}
          <span className="mono">{"{QUEUE_ITEM_ID}"}</span>.
        </div>
        <div className="grid grid-cols-2 gap-3">
          {PATH_LABELS.map(([key, label]) => (
            <label key={key} className="flex flex-col gap-1 text-sm">
              <span className="field-label">{label}</span>
              <input
                aria-label={`Путь: ${label}`}
                className="field-input mono"
                value={draft.paths[key]}
                onChange={(e) => patch({ paths: { ...draft.paths, [key]: e.target.value } })}
              />
            </label>
          ))}
        </div>
      </section>

      <section className="card flex flex-col gap-3" aria-label="Запуск и остановка">
        <div className="text-sm font-semibold">Запуск и остановка</div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Команда запуска (токены по пробелам, {"{CODE}"} в каждом)</span>
          <input
            aria-label="Команда запуска"
            className="field-input mono"
            value={draft.launch_command_template}
            onChange={(e) => patch({ launch_command_template: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">
            Команда остановки ({"{{STARTER_PGREP_PATTERN}}"}, {"{{STOP_GRACE_SECONDS}}"})
          </span>
          <textarea
            aria-label="Команда остановки"
            className="field-input mono text-xs"
            rows={8}
            value={draft.stop_command_template}
            onChange={(e) => patch({ stop_command_template: e.target.value })}
          />
        </label>
        <div className="grid grid-cols-2 gap-3 items-end">
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Пауза между TERM и KILL, с</span>
            <input
              className="field-input mono"
              inputMode="numeric"
              value={String(draft.stop_grace_seconds)}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isInteger(n) && n >= 0 && n <= 600) patch({ stop_grace_seconds: n });
              }}
            />
          </label>
          <Toggle
            label="Терминал (pty): лог построчно, как легаси"
            checked={draft.use_pty}
            onChange={(e) => patch({ use_pty: e.target.checked })}
          />
        </div>
      </section>

      <section className="card flex flex-col gap-3" aria-label="testenv">
        <div className="text-sm font-semibold">testenv-маркер</div>
        <div className="text-xs text-dim">
          «Включено» — только в одиночном запуске с «только подготовить стенд»; прогон РЦ и повторы — всегда
          «выключено».
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Значение «включено»</span>
            <input className="field-input mono" value={draft.testenv.on_value}
              onChange={(e) => patch({ testenv: { ...draft.testenv, on_value: e.target.value } })} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Значение «выключено»</span>
            <input className="field-input mono" value={draft.testenv.off_value}
              onChange={(e) => patch({ testenv: { ...draft.testenv, off_value: e.target.value } })} />
          </label>
        </div>
        <Toggle
          label="Удалять прочие testenv_*.conf перед записью"
          checked={draft.testenv.cleanup_other}
          onChange={(e) => patch({ testenv: { ...draft.testenv, cleanup_other: e.target.checked } })}
        />
      </section>

      <div className="flex items-center gap-3 flex-wrap">
        <input
          aria-label="Комментарий к версии"
          className="field-input flex-1 min-w-[240px]"
          placeholder="Комментарий к версии (что изменилось)"
          value={draft.comment ?? ""}
          onChange={(e) => patch({ comment: e.target.value })}
        />
        <Button variant="primary" onClick={saveVersion} disabled={pending || !dirty}>
          {pending ? "Сохраняем…" : "Сохранить как новую версию"}
        </Button>
        {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
      </div>

      <section className="card flex flex-col gap-2" aria-label="История версий">
        <div className="text-sm font-semibold flex items-center gap-2">
          <History className="w-4 h-4 text-accent" /> История версий
        </div>
        <ul className="flex flex-col gap-1">
          {(versionsQ.data ?? []).map((v) => (
            <li key={v.id} className="flex items-center gap-2 text-sm" data-testid="launch-profile-version">
              <span className="mono">v{v.version}</span>
              <span className="text-dim text-xs">{new Date(v.created_at).toLocaleString("ru-RU")}</span>
              <span className="flex-1 truncate text-xs">{v.comment || ""}</span>
              {v.id !== current?.id && (
                <Button size="sm" variant="ghost" onClick={() => setCompareId(v.id === compareId ? "" : v.id)}>
                  {v.id === compareId ? "Скрыть сравнение" : "Сравнить с текущей"}
                </Button>
              )}
            </li>
          ))}
        </ul>
        {compareWith && current && (
          <pre className="surface-2 border border-token rounded p-2 text-xs mono overflow-x-auto" aria-label="Сравнение версий">
            {diffLines(compareWith.starter_script, current.starter_script).map((line, idx) => (
              <div
                key={idx}
                className={line.kind === "add" ? "text-ok" : line.kind === "del" ? "text-danger" : "text-dim"}
              >
                {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
                {line.text}
              </div>
            ))}
          </pre>
        )}
      </section>
    </div>
  );
}
