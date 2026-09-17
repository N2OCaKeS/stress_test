/**
 * Каталог OS-версий (`/os`) — публичное read-only представление.
 *
 * Любой авторизованный пользователь видит список зарегистрированных версий
 * ОС: `GET /api/server/v1/os-versions` отдаёт каталог без dept-привязки и
 * без требования server-зоны (платформенным business-data-denied ролям
 * backend всё же ответит 403 — это его решение, мы лишь показываем ошибку).
 *
 * Полное управление каталогом (update/delete) остаётся в админке
 * `ServicesOsVersions` под action-матрицей server.admin / dep_admin. Здесь же
 * носителю того же права доступна регистрация новой версии вместе с
 * bootstrap-кредами для restore/prepare flow.
 *
 * Версия почти всегда создаётся с пустым `repositories` (оператору лень или
 * незачем вручную набирать sources.list). Для карточек с пустым списком тут
 * же, рядом со списком, доступна кнопка «Подтянуть по build-версии» —
 * `POST /os-versions/{id}/resolve-repositories`, который резолвит repo-строки
 * из индекса релизов (порт легаси `ReleaseToRepo`). Ручное редактирование
 * репозиториев остаётся в `ServicesOsVersions` как есть.
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  Eye,
  EyeOff,
  HardDrive,
  KeyRound,
  LayoutGrid,
  Link2,
  ListTree,
  Plus,
  Search,
  Wand2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { formatMsk } from "@/lib/datetime";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  createOsVersion,
  getOsVersionBootstrapPassword,
  listOsVersions,
  resolveOsVersionRepositories,
  updateOsVersionBootstrapPassword,
} from "@/api/server/osVersions";
import type {
  OffsetPaginatedResponse,
  OsVersion,
  OsVersionBootstrapPasswordStatus,
  OsVersionCreateRequest,
} from "@/api/server/types";
import { fromBase64 } from "@/lib/base64";
import { naturalCompare } from "@/lib/naturalSort";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { canManageOsVersions } from "@/pages/admin/services/ServicesOsVersions";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

// Сколько версий тянем за один запрос «Загрузить ещё». В проекте каталог
// небольшой (десятки записей), поэтому шага в полсотни хватает с запасом.
const PAGE_SIZE = 50;

type OsViewMode = "cards" | "strips";
type OsFamilyFilter = "all" | "other" | string;

function osFamily(name: string): string {
  const match = name.match(/^(\d+)\.(\d+)/);
  return match ? `${match[1]}.${match[2]}` : "other";
}

const MOCK_ITEMS: OsVersion[] = [
  {
    id: "osv_mock_astra17",
    name: "astra-1.7",
    description: "Astra Linux SE 1.7 (Орёл)",
    repositories: ["https://download.astralinux.ru/astra/stable/1.7"],
    kernels: ["5.4.0-1", "5.10.0-2"],
    is_urgent_update: false,
    rc_number: null,
    discovered_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "osv_mock_astra18",
    name: "astra-1.8",
    description: "Astra Linux SE 1.8",
    repositories: [
      "https://download.astralinux.ru/astra/stable/1.8/main",
      "https://download.astralinux.ru/astra/stable/1.8/extended",
    ],
    kernels: [],
    is_urgent_update: false,
    rc_number: null,
    discovered_at: "2026-02-10T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
  {
    id: "osv_mock_ubuntu2204",
    name: "ubuntu-22.04",
    description: null,
    repositories: [],
    kernels: [],
    is_urgent_update: true,
    rc_number: null,
    discovered_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

/** Бейдж «UU»/hotfix — предупреждающий, не блокирующий стиль (как остальные warn-бейджи). */
function UrgentUpdateBadge() {
  return (
    <Badge kind="warn"
      className="shrink-0 flex items-center gap-1"
      title="Срочное обновление вне обычного цикла РЦ (hotfix, legacy UU)"
    >
      <AlertTriangle className="w-3 h-3" /> UU
    </Badge>
  );
}

/** Компактное отображение списка ядер: count-бейдж, полный список — в title. */
function KernelsBadge({ kernels }: { kernels: string[] }) {
  if (kernels.length === 0) return null;
  return (
    <Badge
      className="shrink-0"
      title={`Ядра: ${kernels.join(", ")}`}
    >
      {kernels.length} ядер
    </Badge>
  );
}

function RepoBadges({ repositories }: { repositories: string[] }) {
  if (repositories.length === 0) {
    return <span className="text-[11px] text-dim">репозитории не указаны</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      {repositories.map((repo, i) => {
        // Репозиторий приходит строкой sources.list (`deb <url> <suite> <components>`),
        // а не голым URL — ссылку вешаем только на извлечённый http(s)-адрес,
        // саму строку показываем как текст.
        const url = repo.match(/https?:\/\/[^\s]+/)?.[0];
        return (
          <Badge
            key={`${repo}-${i}`}
            title={repo}
            className="flex items-center gap-1 max-w-full"
          >
            <span className="truncate mono text-[11px] flex-1">{repo}</span>
            {url && (
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                title={`Открыть ${url}`}
                className="shrink-0 text-dim hover:text-accent"
              >
                <Link2 className="w-3 h-3" />
              </a>
            )}
          </Badge>
        );
      })}
    </div>
  );
}

/**
 * Кнопка/мини-форма «Подтянуть по build-версии» для карточки с пустым
 * `repositories`. Резолвит repo-строки через backend-индекс релизов и по
 * успеху просит родителя перечитать список — сама карточку не подменяет,
 * чтобы не разъезжаться с source-of-truth (`listQ.data`).
 */
function ResolveRepositoriesInline({
  versionId,
  onResolved,
}: {
  versionId: string;
  onResolved: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [buildVersion, setBuildVersion] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const bv = buildVersion.trim();
    if (!bv) {
      toast.warn("Укажите build-версию (X.Y.Z или X.Y.Z.W)");
      return;
    }
    setBusy(true);
    try {
      await resolveOsVersionRepositories(versionId, bv);
      toast.success("Репозитории подтянуты из индекса релизов");
      setOpen(false);
      setBuildVersion("");
      onResolved();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось подтянуть репозитории"));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button size="sm"
        className="flex items-center gap-1 self-start"
        onClick={() => setOpen(true)}
      >
        <Wand2 className="w-3 h-3" /> Подтянуть по build-версии
      </Button>
    );
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <input
        className="input mono text-xs w-40"
        placeholder="1.8.5.46"
        value={buildVersion}
        onChange={(e) => setBuildVersion(e.target.value)}
        disabled={busy}
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />
      <Button variant="primary" size="sm" disabled={busy} onClick={submit}>
        {busy ? "…" : "Подтянуть"}
      </Button>
      <Button size="sm"
        disabled={busy}
        onClick={() => {
          setOpen(false);
          setBuildVersion("");
        }}
      >
        Отмена
      </Button>
    </div>
  );
}

function OsVersionCard({
  version,
  canManage,
  onResolved,
}: {
  version: OsVersion;
  canManage: boolean;
  onResolved: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="card flex flex-col gap-2 break-inside-avoid mb-3">
      <button
        type="button"
        className="flex items-start gap-2 text-left w-full"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <HardDrive className="w-4 h-4 text-accent shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <div className="font-semibold mono truncate">{version.name}</div>
            {version.is_urgent_update && <UrgentUpdateBadge />}
          </div>
          {version.description && (
            <div className="text-xs text-dim">{version.description}</div>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <KernelsBadge kernels={version.kernels} />
          <Badge>{version.repositories.length} repo</Badge>
        </div>
        <ChevronDown
          className={`w-4 h-4 text-dim shrink-0 transition-transform ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {expanded && (
        <OsVersionDetails
          version={version}
          canManage={canManage}
          onResolved={onResolved}
        />
      )}
    </div>
  );
}

function OsVersionStrip({
  version,
  canManage,
  onResolved,
}: {
  version: OsVersion;
  canManage: boolean;
  onResolved: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="surface border border-token rounded flex flex-col">
      <button
        type="button"
        className="grid grid-cols-1 md:grid-cols-[220px_minmax(0,1fr)_96px_120px_24px] gap-3 items-center text-left px-4 py-3 hover-bg"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2 min-w-0">
          <HardDrive className="w-4 h-4 text-accent shrink-0" />
          <span className="font-semibold mono truncate">{version.name}</span>
          {version.is_urgent_update && <UrgentUpdateBadge />}
        </div>
        <div className="text-xs text-dim truncate">{version.description ?? "—"}</div>
        <div className="flex items-center gap-1 flex-wrap justify-self-start">
          <KernelsBadge kernels={version.kernels} />
          <Badge>{version.repositories.length} repo</Badge>
        </div>
        <span className="text-[11px] text-dim mono">{formatMsk(version.updated_at)}</span>
        <ChevronDown
          className={`w-4 h-4 text-dim transition-transform ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>
      {expanded && (
        <div className="px-4 pb-4">
          <OsVersionDetails
            version={version}
            canManage={canManage}
            onResolved={onResolved}
          />
        </div>
      )}
    </div>
  );
}

function OsVersionDetails({
  version,
  canManage,
  onResolved,
}: {
  version: OsVersion;
  canManage: boolean;
  onResolved: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 border-t border-token pt-3">
      <RepoBadges repositories={version.repositories} />
      {version.repositories.length === 0 && canManage && (
        <ResolveRepositoriesInline versionId={version.id} onResolved={onResolved} />
      )}
      {version.kernels.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-dim">ядра</span>
          <div className="flex flex-wrap gap-1">
            {version.kernels.map((kernel) => (
              <Badge key={kernel} className="mono text-[11px]">
                {kernel}
              </Badge>
            ))}
          </div>
        </div>
      )}
      {canManage && <BootstrapCredentials versionId={version.id} />}
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-dim">
        <span>
          обнаружена:{" "}
          <span className="mono">{formatMsk(version.discovered_at)}</span>
        </span>
        <span>
          обновлена: <span className="mono">{formatMsk(version.updated_at)}</span>
        </span>
      </div>
    </div>
  );
}

function BootstrapCredentials({ versionId }: { versionId: string }) {
  const toast = useToast();
  const [sshUsername, setSshUsername] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [saving, setSaving] = useState(false);

  const statusQ = useQuery<OsVersionBootstrapPasswordStatus>(
    () => getOsVersionBootstrapPassword(versionId, false),
    [versionId],
    { keepPreviousDataOnError: true },
  );

  const status = statusQ.data;

  async function reveal() {
    try {
      const data = await getOsVersionBootstrapPassword(versionId, true);
      setSshUsername(data.ssh_username ?? "");
      setPassword(data.password_b64 ? fromBase64(data.password_b64) : "");
      setRevealed(true);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось показать bootstrap-пароль"));
    }
  }

  async function save() {
    const login = sshUsername.trim();
    if (!login) {
      toast.warn("Укажите bootstrap-пользователя");
      return;
    }
    if (!password) {
      toast.warn("Укажите bootstrap-пароль");
      return;
    }
    setSaving(true);
    try {
      const data = await updateOsVersionBootstrapPassword(versionId, {
        ssh_username: login,
        password,
      });
      setSshUsername(data.ssh_username ?? login);
      setPassword("");
      setRevealed(false);
      statusQ.refetch();
      toast.success("Bootstrap-креды сохранены");
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось сохранить bootstrap-креды"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="surface-2 border border-token rounded p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <KeyRound className="w-4 h-4 text-accent" />
        <div className="text-sm font-medium">Bootstrap-пользователь ОС</div>
        <Badge className="ml-auto">
          {statusQ.loading
            ? "проверка..."
            : status?.has_password
              ? "пароль задан"
              : "пароль не задан"}
        </Badge>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[minmax(0,220px)_minmax(0,1fr)_auto] gap-2">
        <input
          className="input mono text-sm"
          placeholder="ssh username"
          value={revealed ? sshUsername : status?.ssh_username ?? sshUsername}
          onChange={(e) => {
            setSshUsername(e.target.value);
            setRevealed(true);
          }}
        />
        <input
          className="input mono text-sm"
          type={revealed ? "text" : "password"}
          placeholder={status?.has_password ? "пароль скрыт" : "password"}
          value={revealed ? password : ""}
          onChange={(e) => {
            setPassword(e.target.value);
            setRevealed(true);
          }}
        />
        <div className="flex gap-2">
          <Button size="sm"
            className="flex items-center gap-1"
            disabled={saving || !status?.has_password}
            onClick={revealed ? () => setRevealed(false) : reveal}
          >
            {revealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
            {revealed ? "Скрыть" : "Показать"}
          </Button>
          <Button variant="primary" size="sm" disabled={saving} onClick={save}>
            {saving ? "..." : "Сохранить"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function latestFamilyVersion(items: OsVersion[], family: string, excludeName: string) {
  if (family === "other") return null;
  return [...items]
    .filter((item) => item.name !== excludeName && osFamily(item.name) === family)
    .sort((a, b) => naturalCompare(b.name, a.name))[0] ?? null;
}

function AddOsVersionWorkspace({
  items,
  mockMode,
  onDone,
  onCancel,
}: {
  items: OsVersion[];
  mockMode: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [buildVersion, setBuildVersion] = useState("");
  const [reposText, setReposText] = useState("");
  const [kernelsText, setKernelsText] = useState("");
  const [isUrgentUpdate, setIsUrgentUpdate] = useState(false);
  const [sshUsername, setSshUsername] = useState("");
  const [password, setPassword] = useState("");
  const [credentialsTouched, setCredentialsTouched] = useState(false);
  const [defaultsFrom, setDefaultsFrom] = useState<string | null>(null);
  const [loadingDefaults, setLoadingDefaults] = useState(false);
  const [busy, setBusy] = useState(false);

  const trimmedName = name.trim();
  const family = osFamily(trimmedName);
  const source = useMemo(
    () => latestFamilyVersion(items, family, trimmedName),
    [items, family, trimmedName],
  );

  useEffect(() => {
    if (mockMode || credentialsTouched || !source) return;
    let alive = true;
    setLoadingDefaults(true);
    getOsVersionBootstrapPassword(source.id, true)
      .then((data) => {
        if (!alive) return;
        setSshUsername(data.ssh_username ?? "");
        setPassword(data.password_b64 ? fromBase64(data.password_b64) : "");
        setDefaultsFrom(source.name);
      })
      .catch(() => {
        if (!alive) return;
        setSshUsername("");
        setPassword("");
        setDefaultsFrom(source.name);
      })
      .finally(() => {
        if (alive) setLoadingDefaults(false);
      });
    return () => {
      alive = false;
    };
  }, [credentialsTouched, mockMode, source]);

  function parseRepos(): string[] {
    return reposText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function parseKernels(): string[] {
    return kernelsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function submit() {
    if (!trimmedName) {
      toast.warn("Укажите имя ОС");
      return;
    }
    if ((sshUsername.trim() && !password) || (!sshUsername.trim() && password)) {
      toast.warn("Bootstrap user и password нужно заполнять вместе");
      return;
    }
    if (mockMode) {
      toast.warn("Mock-режим — изменения не отправляются на backend.");
      onDone();
      return;
    }
    setBusy(true);
    try {
      const repositories = parseRepos();
      const body: OsVersionCreateRequest = {
        name: trimmedName,
        description: description.trim() || undefined,
        repositories,
        kernels: parseKernels(),
        is_urgent_update: isUrgentUpdate,
      };
      const created = await createOsVersion(body);
      const build = buildVersion.trim();
      if (build && repositories.length === 0) {
        await resolveOsVersionRepositories(created.id, build);
      }
      if (sshUsername.trim() && password) {
        await updateOsVersionBootstrapPassword(created.id, {
          ssh_username: sshUsername.trim(),
          password,
        });
      }
      toast.success("Версия ОС добавлена");
      onDone();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось добавить ОС"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-4 flex flex-col gap-4 w-full">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="font-semibold text-lg">Добавить ОС</h2>
          <div className="text-xs text-dim">
            {source
              ? `bootstrap-креды берутся из последней ${family}: ${source.name}`
              : family === "other"
                ? "для other заполните bootstrap-креды вручную"
                : `в семействе ${family} пока нет источника bootstrap-кредов`}
          </div>
        </div>
        <Button size="sm" onClick={onCancel} disabled={busy}>
          К списку ОС
        </Button>
      </div>

      <div className="card grid gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="grid gap-1">
            <span className="text-xs text-dim">Имя ОС</span>
            <input
              className="input mono"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setCredentialsTouched(false);
                setDefaultsFrom(null);
              }}
              placeholder="1.8.6.40"
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs text-dim">Build-версия</span>
            <input
              className="input mono"
              value={buildVersion}
              onChange={(e) => setBuildVersion(e.target.value)}
              placeholder="необязательно"
            />
          </label>
        </div>
        <label className="grid gap-1">
          <span className="text-xs text-dim">Описание</span>
          <input
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="build 1.8.6.40"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-dim">Репозитории</span>
          <textarea
            className="input mono text-xs"
            rows={4}
            value={reposText}
            onChange={(e) => setReposText(e.target.value)}
            placeholder="по одному URL или sources.list строке на строку"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-dim">Ядра</span>
          <textarea
            className="input mono text-xs"
            rows={3}
            value={kernelsText}
            onChange={(e) => setKernelsText(e.target.value)}
            placeholder="по одной версии ядра на строку, например 5.10.0-2"
          />
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
          <Checkbox
            checked={isUrgentUpdate}
            onChange={(e) => setIsUrgentUpdate(e.target.checked)}
          />
          <span>Срочное обновление (hotfix, вне обычного цикла РЦ)</span>
        </label>
        <div className="surface-2 border border-token rounded p-3 grid gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <KeyRound className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">Bootstrap-креды</span>
            {loadingDefaults && <Badge>загрузка...</Badge>}
            {defaultsFrom && !credentialsTouched && (
              <Badge>из {defaultsFrom}</Badge>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input
              className="input mono"
              value={sshUsername}
              onChange={(e) => {
                setSshUsername(e.target.value);
                setCredentialsTouched(true);
              }}
              placeholder="bootstrap user"
            />
            <input
              className="input mono"
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setCredentialsTouched(true);
              }}
              placeholder="bootstrap password"
            />
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button disabled={busy} onClick={onCancel}>
            Отмена
          </Button>
          <Button variant="primary" disabled={busy} onClick={submit}>
            {busy ? "Создание..." : "Добавить ОС"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function OsCatalogBody() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  const canCreate = canManageOsVersions(persona);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [viewMode, setViewMode] = useState<OsViewMode>("cards");
  const [familyFilter, setFamilyFilter] = useState<OsFamilyFilter>("all");

  const listQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit }),
    [limit],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  const all: OsVersion[] = mockMode ? MOCK_ITEMS : listQ.data?.items ?? [];
  const total = mockMode ? MOCK_ITEMS.length : listQ.data?.total ?? all.length;

  const familyOptions = useMemo(() => {
    const set = new Set(all.map((item) => osFamily(item.name)));
    const numeric = [...set]
      .filter((item) => item !== "other")
      .sort(naturalCompare);
    return set.has("other") ? [...numeric, "other"] : numeric;
  }, [all]);

  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (v) =>
        v.name.toLowerCase().includes(q) ||
        (v.description ?? "").toLowerCase().includes(q) ||
        v.repositories.some((r) => r.toLowerCase().includes(q)),
    );
  }, [all, search]);

  const filtered = useMemo(() => {
    if (familyFilter === "all") return searchFiltered;
    return searchFiltered.filter((v) => osFamily(v.name) === familyFilter);
  }, [familyFilter, searchFiltered]);

  const filterButtons = useMemo(
    () => [
      { id: "all" as const, label: "Все", count: searchFiltered.length },
      ...familyOptions.map((id) => ({
        id,
        label: id === "other" ? "other" : id,
        count: searchFiltered.filter((item) => osFamily(item.name) === id).length,
      })),
    ],
    [familyOptions, searchFiltered],
  );

  // «Загрузить ещё» имеет смысл, пока на руках меньше записей, чем total.
  const hasMore = !mockMode && all.length < total;
  const loadingMore = listQ.loading && all.length > 0;

  const errMsg =
    !mockMode && listQ.error
      ? listQ.error instanceof ApiError
        ? `${listQ.error.errorCode}: ${listQ.error.message}`
        : listQ.error.message
      : null;

  if (creating) {
    return (
      <AddOsVersionWorkspace
        items={all}
        mockMode={mockMode}
        onDone={() => {
          setCreating(false);
          listQ.refetch();
        }}
        onCancel={() => setCreating(false)}
      />
    );
  }

  return (
    <div className="p-4 flex flex-col gap-4 w-full">
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="font-semibold flex items-center gap-2 text-lg">
          <HardDrive className="w-5 h-5 text-accent" /> Каталог ОС
        </h2>
        <span className="text-[11px] text-dim">
          версии ОС, зарегистрированные в системе
          {canCreate ? "" : " · только просмотр"}
        </span>
        {canCreate && (
          <Button variant="primary" size="sm"
            className="flex items-center gap-1 ml-auto"
            onClick={() => setCreating(true)}
            disabled={creating}
          >
            <Plus className="w-4 h-4" /> Добавить версию ОС
          </Button>
        )}
      </div>

      <div className="surface border border-token rounded p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="relative min-w-[220px] flex-1 max-w-sm">
          <Search className="w-4 h-4 text-dim absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            className="input pl-8 w-full"
            placeholder="Поиск по имени, описанию, репозиторию…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {filterButtons.map((item) => (
            <Button
              key={item.id}
              type="button"
              onClick={() => setFamilyFilter(item.id)}
              size="sm"
              variant={familyFilter === item.id ? "primary" : "default"}
              className="inline-flex items-center gap-2"
            >
              <span>{item.label}</span>
              <span className="mono text-[11px] opacity-80">{item.count}</span>
            </Button>
          ))}
        </div>
        <div className="surface-2 border border-token rounded p-1 flex items-center gap-1">
          {[
            { id: "cards" as const, label: "Квадраты", icon: LayoutGrid },
            { id: "strips" as const, label: "Список", icon: ListTree },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <Button
                key={item.id}
                type="button"
                onClick={() => setViewMode(item.id)}
                size="sm"
                variant={viewMode === item.id ? "primary" : "default"}
                className="inline-flex items-center gap-2"
              >
                <Icon className="w-4 h-4" />
                <span>{item.label}</span>
              </Button>
            );
          })}
        </div>
      </div>

      {errMsg && (
        <div className="alert-danger text-xs flex items-center justify-between gap-3">
          <span>{errMsg}</span>
          <Button onClick={() => listQ.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      {!mockMode && listQ.loading && all.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <div className="spinner big" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-sm text-dim py-8 text-center">
          {search.trim()
            ? "Ничего не найдено по запросу."
            : "Каталог ОС пуст."}
        </div>
      ) : (
        <>
          {viewMode === "cards" && (
            <div className="columns-1 md:columns-2 xl:columns-3 2xl:columns-4 gap-3">
              {filtered.map((v) => (
                <OsVersionCard
                  key={v.id}
                  version={v}
                  canManage={canCreate && !mockMode}
                  onResolved={() => listQ.refetch()}
                />
              ))}
            </div>
          )}
          {viewMode === "strips" && (
            <div className="flex flex-col gap-2">
              {filtered.map((v) => (
                <OsVersionStrip
                  key={v.id}
                  version={v}
                  canManage={canCreate && !mockMode}
                  onResolved={() => listQ.refetch()}
                />
              ))}
            </div>
          )}
        </>
      )}

      {hasMore && (
        <div className="flex justify-center pt-1">
          <Button
            disabled={loadingMore}
            onClick={() => setLimit((n) => n + PAGE_SIZE)}
          >
            {loadingMore ? "Загрузка…" : "Загрузить ещё"}
          </Button>
        </div>
      )}
    </div>
  );
}

export function OsCatalog() {
  return (
    <Shell breadcrumb="Главная / ОС">
      <main className="flex-1 min-w-0 min-h-0 overflow-auto">
        <OsCatalogBody />
      </main>
    </Shell>
  );
}
