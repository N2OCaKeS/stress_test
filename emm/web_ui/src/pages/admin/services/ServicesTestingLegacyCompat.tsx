/**
 * Публичный compat легаси-путей ALLTA (`/admin/services.testing.legacy_compat`, D17).
 *
 * Скрипты веток `stress_test` на стендах ходят на
 * `http://allta.devos.astralinux.ru/rest/api/get-repo-path`, `get-confluence-url`
 * и т.п. без токена. После переноса DNS эти пути обслуживает testing_service —
 * без авторизации, но только из подсетей, перечисленных здесь (сид —
 * `10.177.103.0/24`, подсеть стендов легаси).
 *
 * `get-jira-url`/`get-confluence-url` отдают URL отдела стенда: IP источника →
 * стенд → его отдел. Незнакомый IP (или стенды с этим IP в разных отделах) —
 * отдел по умолчанию, который выбирается здесь же. Проверка «что будет с этим
 * адресом» — внизу страницы (`GET /legacy-compat/resolve`).
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /legacy-compat/settings, GET/POST/PATCH/DELETE /legacy-compat/networks,
 *   GET /legacy-compat/resolve?ip=
 * Право — `(legacy_compat, view|update)`: системная роль admin testing_service
 * (department_admin не проходит — подсеть открывает доступ всем отделам).
 */

import { useState } from "react";
import { AlertCircle, Globe, Plus, Search, Trash2 } from "lucide-react";

import { ApiError, apiErrMsg } from "@/api/client";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";
import { useQuery } from "@/api/auth/useQuery";
import {
  createCompatNetwork,
  deleteCompatNetwork,
  getLegacyCompatSettings,
  listCompatNetworks,
  resolveCompatIp,
  updateCompatNetwork,
  updateLegacyCompatSettings,
} from "@/api/testing/legacyCompat";
import type {
  CompatNetwork,
  CompatResolveResult,
  LegacyCompatSettings,
} from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { useToast } from "@/contexts/ToastContext";

function writeError(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужна роль admin в testing_service).";
    if (e.status === 409) return "Такая подсеть уже есть в списке.";
    if (e.status === 422) return apiErrMsg(e, "Некорректная подсеть (пример: 10.177.103.0/24).");
  }
  return apiErrMsg(e, fallback);
}

/** Грубая проверка до отправки; точную (биты хоста и т.п.) делает backend. */
const CIDR_RE = /^[0-9a-fA-F:.]+(\/\d{1,3})?$/;

const REASON_TEXT: Record<CompatResolveResult["reason"], string> = {
  stand: "найден стенд с этим IP — отдел стенда",
  default: "стенда с этим IP нет — отдел по умолчанию",
  ambiguous: "стенды с этим IP в разных отделах — отдел по умолчанию",
  no_default: "стенда нет, отдел по умолчанию не выбран — get-*-url ответит 404",
};

export function ServicesTestingLegacyCompat() {
  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);
  const deptName = (id: string | null | undefined) =>
    (id && deptsQ.data?.find((d) => d.id === id)?.name) || id || "—";

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Globe className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Легаси /rest/api</h1>
          <div className="text-xs text-dim">
            доступ скриптов на стендах к легаси-путям ALLTA без авторизации · платформа
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        <div className="text-xs text-dim max-w-3xl">
          Скрипты веток на стендах обращаются к{" "}
          <span className="mono">http://allta.devos.astralinux.ru/rest/api/…</span>{" "}
          (<span className="mono">get-repo-path</span>, <span className="mono">get-confluence-url</span>,{" "}
          <span className="mono">get-jira-url</span>, <span className="mono">get-box-config</span> …).
          Платформа отвечает на эти пути без токена — только несекретные справочники и только
          запросам из подсетей ниже.
        </div>
        <DefaultDepartmentForm departments={deptsQ.data} deptName={deptName} />
        <NetworksEditor />
        <ResolveCheck deptName={deptName} />
      </div>
    </div>
  );
}

function DefaultDepartmentForm({
  departments,
  deptName,
}: {
  departments: Department[] | undefined;
  deptName: (id: string | null | undefined) => string;
}) {
  const toast = useToast();
  const cfgQ = useQuery<LegacyCompatSettings>(() => getLegacyCompatSettings(), []);
  const loaded = cfgQ.data;
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  // Свежие данные с сервера сбрасывают черновик — во время рендера, не в effect.
  const [source, setSource] = useState<LegacyCompatSettings | undefined>(undefined);
  if (loaded !== source) {
    setSource(loaded);
    setValue(loaded?.default_department_id ?? "");
  }

  const dirty = loaded != null && value.trim() !== (loaded.default_department_id ?? "");

  async function save() {
    if (pending || !dirty) return;
    setPending(true);
    try {
      await updateLegacyCompatSettings({ default_department_id: value.trim() || null });
      toast.success("Отдел по умолчанию сохранён");
      cfgQ.refetch();
    } catch (e) {
      toast.error(writeError(e, "Не удалось сохранить отдел по умолчанию"));
    } finally {
      setPending(false);
    }
  }

  const options: DropdownOption[] = [
    { value: "", label: "— не выбран —" },
    ...(departments ?? []).map((d) => ({ value: d.id, label: d.name })),
  ];
  // Сохранённый отдел, которого нет в списке (удалён/нет прав на список), не теряется.
  if (value && departments && !departments.some((d) => d.id === value)) {
    options.push({ value, label: value });
  }

  return (
    <section className="flex flex-col gap-3 max-w-xl" aria-label="Отдел по умолчанию">
      <div>
        <h2 className="font-semibold">Отдел для URL Jira/Confluence</h2>
        <div className="text-xs text-dim">
          <span className="mono">get-jira-url</span> и <span className="mono">get-confluence-url</span>{" "}
          отдают URL из «Интеграций отдела» того отдела, к которому относится стенд с IP источника.
          Если такого стенда нет — URL отдела, выбранного здесь.
        </div>
      </div>

      {cfgQ.loading && <div className="text-xs text-dim">Загрузка…</div>}
      {!cfgQ.loading && cfgQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>{apiErrMsg(cfgQ.error, "Настройки не загрузились")}</div>
        </div>
      )}

      {loaded != null && (
        <>
          {departments ? (
            <Dropdown
              mode="single"
              label="Отдел по умолчанию"
              placeholder="— не выбран —"
              options={options}
              value={value}
              onChange={setValue}
            />
          ) : (
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">id отдела по умолчанию</span>
              <input
                className="field-input mono"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="dep_…"
              />
            </label>
          )}
          {!value && (
            <div className="alert-warn text-xs">
              Отдел не выбран: запрос URL с IP, которого нет среди стендов, получит 404.
            </div>
          )}
          <div className="flex items-center gap-3">
            <Button type="button" variant="primary" onClick={save} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {!dirty && loaded.default_department_id && (
              <span className="text-xs text-dim">сейчас: {deptName(loaded.default_department_id)}</span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function NetworksEditor() {
  const toast = useToast();
  const confirm = useConfirm();
  const listQ = useQuery<CompatNetwork[]>(() => listCompatNetworks(), []);
  const items = listQ.data ?? [];
  const [cidr, setCidr] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function add() {
    const trimmed = cidr.trim();
    if (!CIDR_RE.test(trimmed)) {
      setErr("Укажите подсеть или адрес, например 10.177.103.0/24.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await createCompatNetwork({ cidr: trimmed, description: description.trim() || null });
      toast.success("Подсеть добавлена");
      setCidr("");
      setDescription("");
      listQ.refetch();
    } catch (e) {
      const msg = writeError(e, "Не удалось добавить подсеть");
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(item: CompatNetwork) {
    setBusy(true);
    try {
      await updateCompatNetwork(item.id, { enabled: !item.enabled });
      listQ.refetch();
    } catch (e) {
      toast.error(writeError(e, "Не удалось изменить подсеть"));
    } finally {
      setBusy(false);
    }
  }

  async function remove(item: CompatNetwork) {
    const ok = await confirm.confirm({
      message: `Удалить подсеть ${item.cidr}? Стенды из неё перестанут получать ответы /rest/api (403). Чтобы закрыть временно — выключите её.`,
      danger: true,
      confirmLabel: "Удалить",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteCompatNetwork(item.id);
      toast.success("Подсеть удалена");
      listQ.refetch();
    } catch (e) {
      toast.error(writeError(e, "Не удалось удалить подсеть"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 max-w-3xl" aria-label="Разрешённые подсети">
      <div>
        <h2 className="font-semibold">Разрешённые подсети</h2>
        <div className="text-xs text-dim">
          Запросы к /rest/api с других адресов получают 403. Выключенная подсеть остаётся в списке,
          но доступа не даёт.
        </div>
      </div>

      {listQ.loading && <div className="text-xs text-dim">Загрузка…</div>}
      {!listQ.loading && listQ.error != null && (
        <div className="alert-danger text-sm">
          {apiErrMsg(listQ.error, "Список подсетей не загрузился")}
          <Button variant="ghost" className="ml-2" type="button" onClick={() => listQ.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <div key={item.id} className="card w-full flex items-center gap-3" data-testid={`compat-network-${item.cidr}`}>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="mono font-semibold text-sm">{item.cidr}</span>
                {!item.enabled && <Badge kind="idle">выключена</Badge>}
              </div>
              {item.description && <div className="text-xs text-dim truncate">{item.description}</div>}
            </div>
            <Toggle
              checked={item.enabled}
              disabled={busy}
              onChange={() => toggle(item)}
              aria-label={`Подсеть ${item.cidr} включена`}
            />
            <Button
              size="sm"
              type="button"
              variant="danger"
              disabled={busy}
              onClick={() => remove(item)}
              aria-label={`Удалить ${item.cidr}`}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </div>
        ))}
        {!listQ.loading && listQ.error == null && items.length === 0 && (
          <div className="alert-warn text-xs">Список пуст — /rest/api не отвечает никому.</div>
        )}
      </div>

      <div className="card w-full flex flex-col gap-2" role="group" aria-label="Новая подсеть">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Подсеть или адрес</span>
            <input
              className="field-input mono"
              value={cidr}
              onChange={(e) => setCidr(e.target.value)}
              placeholder="10.177.103.0/24"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Описание</span>
            <input
              className="field-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="стенды отдела"
            />
          </label>
        </div>
        {err && <div role="alert" className="text-xs text-danger">{err}</div>}
        <div>
          <Button type="button" className="flex items-center gap-1" disabled={busy || !cidr.trim()} onClick={add}>
            <Plus className="w-4 h-4" /> Добавить подсеть
          </Button>
        </div>
      </div>
    </section>
  );
}

function ResolveCheck({ deptName }: { deptName: (id: string | null | undefined) => string }) {
  const [ip, setIp] = useState("");
  const [result, setResult] = useState<CompatResolveResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function check() {
    if (!ip.trim() || pending) return;
    setPending(true);
    setErr(null);
    try {
      setResult(await resolveCompatIp(ip.trim()));
    } catch (e) {
      setResult(null);
      setErr(apiErrMsg(e, "Не удалось проверить адрес"));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 max-w-xl" aria-label="Проверка адреса">
      <div>
        <h2 className="font-semibold">Проверить адрес</h2>
        <div className="text-xs text-dim">
          Пустит ли /rest/api запрос с этого IP и URL какого отдела отдаст get-*-url.
        </div>
      </div>
      <div className="flex items-end gap-2">
        <label className="flex flex-col gap-1 text-sm flex-1">
          <span className="field-label">IP стенда</span>
          <input
            className="field-input mono"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void check();
            }}
            placeholder="10.177.103.201"
          />
        </label>
        <Button type="button" className="flex items-center gap-1" disabled={pending || !ip.trim()} onClick={check}>
          <Search className="w-4 h-4" /> Проверить
        </Button>
      </div>
      {err && <div role="alert" className="text-xs text-danger">{err}</div>}
      {result && (
        <div className="card text-sm flex flex-col gap-1" data-testid="compat-resolve-result">
          <div>
            {result.allowed ? (
              <Badge kind="ok">доступ есть</Badge>
            ) : (
              <Badge kind="danger">403 — адрес вне разрешённых подсетей</Badge>
            )}
            {result.cidr && <span className="mono text-xs text-dim ml-2">{result.cidr}</span>}
          </div>
          <div>
            Отдел: <span className="font-semibold">{deptName(result.department_id)}</span>
            <span className="text-xs text-dim"> — {REASON_TEXT[result.reason]}</span>
          </div>
          {result.stand_ids.length > 0 && (
            <div className="text-xs text-dim mono">стенды: {result.stand_ids.join(", ")}</div>
          )}
        </div>
      )}
    </section>
  );
}
