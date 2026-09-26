/**
 * Блок «Проверка внешних сервисов перед запуском» на странице
 * очереди тестирования.
 *
 * `department_test_settings.preflight` — список HTTP-проб (URL + критерий
 * доступности), DNS-серверы и порт, интервал опроса и общий таймаут
 * ожидания. testing_service отдаёт эти значения воркеру в каждом claim, так
 * что правка действует на следующий запуск без перезапуска воркера.
 * Дефолты — легаси `available_astra_services_checker` (строго 200, 180 с,
 * 120 минут).
 *
 * Отдельная форма со своей кнопкой сохранения: объект `preflight` уходит в
 * PUT целиком и не должен смешиваться с остальными полями страницы.
 */

import { useMemo, useState } from "react";
import { Plus, Radar, Trash2 } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { upsertDepartmentTestSettings } from "@/api/testing/departmentTestSettings";
import type {
  PreflightOkStatus,
  PreflightSettings,
} from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { useToast } from "@/contexts/ToastContext";

const OK_STATUS_OPTIONS: DropdownOption[] = [
  { value: "200", label: "Строго 200 (как в легаси)" },
  { value: "lt500", label: "Любой ответ < 500" },
];

const URL_RE = /^https?:\/\/\S+$/;

interface ProbeDraft {
  key: number;
  url: string;
  ok_status: PreflightOkStatus;
}

interface Draft {
  enabled: boolean;
  http: ProbeDraft[];
  dnsHosts: string;
  dnsPort: string;
  pollInterval: string;
  timeout: string;
  probeTimeout: string;
}

let probeKeySeq = 0;
function nextKey(): number {
  probeKeySeq += 1;
  return probeKeySeq;
}

function toDraft(p: PreflightSettings): Draft {
  return {
    enabled: p.enabled,
    http: p.http.map((probe) => ({ key: nextKey(), url: probe.url, ok_status: probe.ok_status })),
    dnsHosts: p.dns_hosts.join("\n"),
    dnsPort: String(p.dns_port),
    pollInterval: String(p.poll_interval_seconds),
    timeout: String(p.timeout_seconds),
    probeTimeout: String(p.probe_timeout_seconds),
  };
}

function parseIntIn(raw: string, min: number, max: number): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= min && value <= max ? value : null;
}

/** Черновик → тело `preflight`; строка — первая найденная ошибка валидации. */
function draftToPreflight(d: Draft): PreflightSettings | string {
  const http = d.http
    .map((probe) => ({ url: probe.url.trim(), ok_status: probe.ok_status }))
    .filter((probe) => probe.url !== "");
  const badUrl = http.find((probe) => !URL_RE.test(probe.url));
  if (badUrl) return `Некорректный URL: ${badUrl.url} (нужен http:// или https://, без пробелов)`;
  const dnsHosts = d.dnsHosts
    .split(/[\s,]+/)
    .map((host) => host.trim())
    .filter((host) => host !== "");
  const dnsPort = parseIntIn(d.dnsPort, 1, 65535);
  if (dnsPort === null) return "Порт DNS — целое число от 1 до 65535.";
  const pollInterval = parseIntIn(d.pollInterval, 1, 86400);
  if (pollInterval === null) return "Интервал опроса — целое число секунд от 1 до 86400.";
  const timeout = parseIntIn(d.timeout, 0, 7 * 86400);
  if (timeout === null) return "Таймаут ожидания — целое число секунд от 0 до 604800.";
  const probeTimeout = parseIntIn(d.probeTimeout, 1, 600);
  if (probeTimeout === null) return "Таймаут одной пробы — целое число секунд от 1 до 600.";
  return {
    enabled: d.enabled,
    http,
    dns_hosts: dnsHosts,
    dns_port: dnsPort,
    poll_interval_seconds: pollInterval,
    timeout_seconds: timeout,
    probe_timeout_seconds: probeTimeout,
  };
}

export function PreflightSettingsForm({
  departmentId,
  loaded,
  onSaved,
}: {
  departmentId: string;
  loaded: PreflightSettings;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(() => toDraft(loaded));
  const [pending, setPending] = useState(false);
  // Свежие данные с сервера (после сохранения/refetch) сбрасывают черновик.
  // Подстройка во время рендера, а не в effect: effect на маунте пересоздал
  // бы строки (новые key) уже после первого рендера.
  const [source, setSource] = useState(loaded);
  if (source !== loaded) {
    setSource(loaded);
    setDraft(toDraft(loaded));
  }

  const dirty = useMemo(() => {
    const parsed = draftToPreflight(draft);
    return typeof parsed === "string" || JSON.stringify(parsed) !== JSON.stringify(loaded);
  }, [draft, loaded]);

  function patch(changes: Partial<Draft>) {
    setDraft((prev) => ({ ...prev, ...changes }));
  }

  function patchProbe(key: number, changes: Partial<ProbeDraft>) {
    setDraft((prev) => ({
      ...prev,
      http: prev.http.map((probe) => (probe.key === key ? { ...probe, ...changes } : probe)),
    }));
  }

  async function save(body: PreflightSettings | null, successText: string) {
    setPending(true);
    try {
      await upsertDepartmentTestSettings(departmentId, { preflight: body });
      toast.success(successText);
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить проверку внешних сервисов"));
    } finally {
      setPending(false);
    }
  }

  async function handleSave() {
    if (pending) return;
    const parsed = draftToPreflight(draft);
    if (typeof parsed === "string") {
      toast.error(parsed);
      return;
    }
    await save(parsed, "Проверка внешних сервисов сохранена");
  }

  async function handleReset() {
    if (pending) return;
    await save(null, "Проверка внешних сервисов сброшена к значениям по умолчанию");
  }

  return (
    <section className="card flex flex-col gap-3 max-w-2xl" aria-labelledby="preflight-title">
      <div className="text-sm font-semibold flex items-center gap-2" id="preflight-title">
        <Radar className="w-4 h-4 text-accent" /> Проверка внешних сервисов перед запуском
      </div>
      <div className="text-xs text-dim">
        Перед запуском теста воркер ждёт, пока все HTTP-адреса ответят и отзовётся хотя бы один
        DNS-сервер. Изменения действуют на следующий запуск.
      </div>

      <Toggle
        label="Проверять внешние сервисы перед запуском теста"
        checked={draft.enabled}
        onChange={(e) => patch({ enabled: e.target.checked })}
      />

      <div className="flex flex-col gap-2">
        <span className="field-label">HTTP-адреса</span>
        {draft.http.length === 0 && (
          <div className="text-xs text-dim">Нет адресов — HTTP-часть проверки отключена.</div>
        )}
        {draft.http.map((probe, index) => (
          <div key={probe.key} className="flex items-center gap-2">
            <input
              className="field-input mono flex-1 min-w-0"
              aria-label={`URL ${index + 1}`}
              value={probe.url}
              placeholder="https://"
              onChange={(e) => patchProbe(probe.key, { url: e.target.value })}
            />
            <Dropdown
              mode="single"
              options={OK_STATUS_OPTIONS}
              value={probe.ok_status}
              searchable={false}
              sortOptions={false}
              onChange={(value) => patchProbe(probe.key, { ok_status: value as PreflightOkStatus })}
            />
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Удалить URL ${index + 1}`}
              onClick={() => patch({ http: draft.http.filter((p) => p.key !== probe.key) })}
            >
              <Trash2 className="w-4 h-4" />
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            className="flex items-center gap-1"
            onClick={() => patch({ http: [...draft.http, { key: nextKey(), url: "", ok_status: "200" }] })}
          >
            <Plus className="w-3 h-3" /> Добавить адрес
          </Button>
        </div>
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">DNS-серверы (по одному в строке; достаточно любого одного)</span>
        <textarea
          className="field-input mono"
          rows={3}
          value={draft.dnsHosts}
          onChange={(e) => patch({ dnsHosts: e.target.value })}
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Порт DNS (TCP)</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.dnsPort}
            onChange={(e) => patch({ dnsPort: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Интервал повтора, с</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.pollInterval}
            onChange={(e) => patch({ pollInterval: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Общий таймаут ожидания, с</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.timeout}
            onChange={(e) => patch({ timeout: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Таймаут одной пробы, с</span>
          <input
            className="field-input mono"
            inputMode="numeric"
            value={draft.probeTimeout}
            onChange={(e) => patch({ probeTimeout: e.target.value })}
          />
        </label>
      </div>

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={pending || !dirty}>
          {pending ? "Сохраняем…" : "Сохранить проверку сервисов"}
        </Button>
        <Button variant="ghost" onClick={handleReset} disabled={pending}>
          Сбросить к значениям по умолчанию
        </Button>
        {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
      </div>
    </section>
  );
}
