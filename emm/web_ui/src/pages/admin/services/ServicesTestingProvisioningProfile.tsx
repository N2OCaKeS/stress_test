/**
 * Профиль подготовки стенда (`/admin/services.testing.provisioning_profile`, D10).
 *
 * Что считать «стенд поднялся» после перезагрузки при подготовке: какие
 * упавшие юниты допустимы при `degraded` (легаси — `astra-mount-lock.service`),
 * сколько раз перезагружать при ином `degraded`, закомментировать ли
 * `pam_lastlog.so inactive=`. Общий профиль меняет только admin
 * testing_service; отдел может завести свой на его основе.
 * Источник истины — `/api/testing/v1/provisioning-profiles`.
 */

import { useMemo, useState } from "react";
import { AlertCircle, ServerCog } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  createProvisioningProfile,
  listProvisioningProfiles,
  updateProvisioningProfile,
} from "@/api/testing/provisioningProfiles";
import type { ProvisioningProfile, ProvisioningProfileFields } from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { personaDeptId } from "@/lib/rbac";

interface Draft {
  units: string;
  attempts: string;
  pam: boolean;
  timeout: string;
}

function toDraft(p: ProvisioningProfile): Draft {
  return {
    units: p.allowed_failed_units.join("\n"),
    attempts: String(p.degraded_reboot_attempts),
    pam: p.disable_pam_lastlog_inactive,
    timeout: p.boot_wait_timeout_seconds == null ? "" : String(p.boot_wait_timeout_seconds),
  };
}

function toFields(d: Draft): ProvisioningProfileFields | string {
  const attempts = Number(d.attempts);
  if (!Number.isInteger(attempts) || attempts < 0 || attempts > 20) return "Перезагрузок — целое число от 0 до 20.";
  let timeout: number | null = null;
  if (d.timeout.trim()) {
    timeout = Number(d.timeout);
    if (!Number.isInteger(timeout) || timeout < 60 || timeout > 86400) {
      return "Ожидание подъёма — целое число секунд от 60 до 86400 или пусто.";
    }
  }
  return {
    allowed_failed_units: d.units.split(/\s+/).filter(Boolean),
    degraded_reboot_attempts: attempts,
    disable_pam_lastlog_inactive: d.pam,
    boot_wait_timeout_seconds: timeout,
  };
}

export function ServicesTestingProvisioningProfile() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <ServerCog className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Профиль подготовки</h1>
          <div className="text-xs text-dim">testing_service · готовность стенда после перезагрузки, PAM</div>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? <Editor departmentId={departmentId} /> : <div className="text-sm text-dim">Нет привязанного отдела.</div>}
      </div>
    </div>
  );
}

function Editor({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const q = useQuery(async () => (await listProvisioningProfiles(departmentId)).items, [departmentId]);
  const profiles = useMemo(() => q.data ?? [], [q.data]);
  const [selectedId, setSelectedId] = useState("");
  const selected =
    profiles.find((p) => p.id === selectedId) ??
    profiles.find((p) => p.department_id === departmentId && p.is_default) ??
    profiles.find((p) => p.is_default) ??
    profiles[0];
  const [draft, setDraft] = useState<Draft | null>(null);
  const [source, setSource] = useState<ProvisioningProfile | undefined>(undefined);
  if (selected !== source) {
    setSource(selected);
    setDraft(selected ? toDraft(selected) : null);
  }
  const [pending, setPending] = useState(false);

  async function run(action: () => Promise<unknown>, ok: string, fail: string) {
    setPending(true);
    try {
      await action();
      toast.success(ok);
      q.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, fail));
    } finally {
      setPending(false);
    }
  }

  if (q.loading && !q.data) return <div className="text-xs text-dim">Загрузка…</div>;
  if (q.error != null && !q.data) {
    return (
      <div className="alert-danger text-sm flex items-start gap-2">
        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
        {apiErrMsg(q.error, "Профили подготовки не загрузились")}
      </div>
    );
  }
  if (!selected || !draft) return <div className="text-sm text-dim">Профилей подготовки нет.</div>;
  const isGlobal = selected.department_id == null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(selected));

  function withFields(then: (fields: ProvisioningProfileFields) => Promise<unknown>, ok: string, fail: string) {
    const fields = toFields(draft!);
    if (typeof fields === "string") {
      toast.error(fields);
      return;
    }
    void run(() => then(fields), ok, fail);
  }

  return (
    <section className="card flex flex-col gap-3 max-w-2xl" aria-label="Профиль подготовки">
      <div className="flex items-end gap-3 flex-wrap">
        <label className="flex flex-col gap-1 text-sm min-w-[260px]">
          <span className="field-label">Профиль</span>
          <Dropdown
            mode="single" searchable={false}
            options={profiles.map((p) => ({
              value: p.id,
              label: `${p.name}${p.department_id ? "" : " (общий)"}${p.is_default ? " · по умолчанию" : ""}`,
            }))}
            value={selected.id}
            onChange={setSelectedId}
          />
        </label>
        {isGlobal && <span className="text-xs text-dim pb-2">Общий профиль меняет только администратор тестирования.</span>}
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">Допустимые упавшие юниты при degraded (по одному в строке)</span>
        <textarea
          aria-label="Допустимые упавшие юниты"
          className="field-input mono" rows={3}
          value={draft.units}
          onChange={(e) => setDraft({ ...draft, units: e.target.value })}
        />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Перезагрузок при ином degraded</span>
          <input aria-label="Перезагрузок при ином degraded" className="field-input mono" value={draft.attempts}
            onChange={(e) => setDraft({ ...draft, attempts: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Ждать подъёма, с (пусто — по умолчанию)</span>
          <input className="field-input mono" value={draft.timeout}
            onChange={(e) => setDraft({ ...draft, timeout: e.target.value })} />
        </label>
      </div>
      <Toggle
        label="Закомментировать pam_lastlog.so inactive= в common-auth (как легаси)"
        checked={draft.pam}
        onChange={(e) => setDraft({ ...draft, pam: e.target.checked })}
      />
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          variant="primary" disabled={pending || !dirty}
          onClick={() => withFields(
            (f) => updateProvisioningProfile(selected.id, f),
            "Профиль подготовки сохранён", "Не удалось сохранить профиль подготовки",
          )}
        >
          Сохранить
        </Button>
        {isGlobal && (
          <Button
            disabled={pending}
            onClick={() => withFields(
              (f) => createProvisioningProfile({
                ...f, department_id: departmentId, name: `${selected.name} (отдел)`, is_default: true,
              }),
              "Создан профиль отдела — он используется по умолчанию", "Не удалось создать профиль отдела",
            )}
          >
            Создать профиль отдела на его основе
          </Button>
        )}
        {!isGlobal && !selected.is_default && (
          <Button
            disabled={pending}
            onClick={() => run(
              () => updateProvisioningProfile(selected.id, { is_default: true }),
              "Профиль стал профилем отдела по умолчанию", "Не удалось сделать профиль профилем по умолчанию",
            )}
          >
            Сделать профилем отдела по умолчанию
          </Button>
        )}
      </div>
    </section>
  );
}
