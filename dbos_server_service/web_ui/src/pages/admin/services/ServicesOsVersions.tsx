/**
 * Каталог OS-версий (`/admin/services.server.os_versions`).
 *
 * Источник правды — `GET /api/server/v1/os-versions` (глобальный каталог,
 * без dept-привязки). Список читается публично; CRUD идёт под action-матрицей
 * `server_service`: create/update/delete несёт сервисная роль `server.admin`,
 * а также dep_admin (он держит admin в своём отделе). Platform-роли
 * (account_admin / loging_admin) режет middleware на 403
 * PLATFORM_ADMIN_BUSINESS_DATA_DENIED — поэтому страница им не показывается.
 *
 * Удаление упирается в FK `servers.os_version_id ondelete=RESTRICT`: версию,
 * на которую ссылается хотя бы один сервер, backend не даст снести (409
 * OS_VERSION_IN_USE) — это явно отражено в подсказке кнопки.
 */

import { useState } from "react";
import { HardDrive, Trash2, Link2, Pencil } from "lucide-react";
import {
  InlineEditor,
  FormRow,
  StatRow,
  useInlineState,
} from "./_inline";
import { formatMsk } from "@/lib/datetime";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  createOsVersion,
  deleteOsVersion,
  listOsVersions,
  updateOsVersion,
} from "@/api/server/osVersions";
import type {
  OffsetPaginatedResponse,
  OsVersion,
  OsVersionCreateRequest,
  OsVersionUpdateRequest,
} from "@/api/server/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";

// Управление каталогом несёт server.admin (и dep_admin в своём отделе).
// Зеркалит гейт ServicesServerPermissions — backend режет platform-роли.
export function canManageOsVersions(
  persona: ReturnType<typeof usePersona>["persona"],
): boolean {
  return (
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.server === "admin"
  );
}

// Каталог тянем одной страницей — версий в проекте десятки, не тысячи.
const PAGE_LIMIT = 500;

export function ServicesOsVersions() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const canEdit = canManageOsVersions(persona);

  const listQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: PAGE_LIMIT }),
    [],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  const items: OsVersion[] = mockMode
    ? [
        {
          id: "osv_mock_astra17",
          name: "astra-1.7",
          description: "Astra Linux SE 1.7 (Орёл)",
          repositories: ["https://download.astralinux.ru/astra/stable/1.7"],
          discovered_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "osv_mock_ubuntu2204",
          name: "ubuntu-22.04",
          description: null,
          repositories: [],
          discovered_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ]
    : listQ.data?.items ?? [];

  return (
    <InlineEditor
      title="OS-версии · server_service"
      icon={HardDrive}
      hint="глобальный каталог версий ОС · CRUD — server.admin / dep_admin"
      items={items}
      loading={!mockMode && listQ.loading}
      error={
        !mockMode && listQ.error
          ? listQ.error instanceof ApiError
            ? `${listQ.error.errorCode}: ${listQ.error.message}`
            : listQ.error.message
          : null
      }
      onRetry={() => listQ.refetch()}
      getId={(v) => v.id}
      canEdit={canEdit}
      readonlyNote={
        canEdit
          ? undefined
          : "Регистрация, правка и удаление версий — server.admin или department_admin."
      }
      emptyHint="Выберите версию слева или зарегистрируйте новую."
      renderRow={({ item, active, onSelect }) => (
        <button
          className={`cred-row text-left ${active ? "active" : ""}`}
          onClick={onSelect}
        >
          <div className="flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-accent" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              {item.description && (
                <div className="text-[11px] text-dim truncate">
                  {item.description}
                </div>
              )}
            </div>
            {item.repositories.length > 0 && (
              <span className="badge" title="репозиториев">
                {item.repositories.length} repo
              </span>
            )}
          </div>
        </button>
      )}
      renderDetail={(v, { onClose }) => (
        <OsVersionDetail
          version={v}
          canEdit={canEdit}
          mockMode={mockMode}
          onChanged={() => {
            listQ.refetch();
            onClose();
          }}
        />
      )}
      renderCreate={
        canEdit
          ? (onClose) => (
              <OsVersionForm
                mockMode={mockMode}
                onDone={() => {
                  listQ.refetch();
                  onClose();
                }}
              />
            )
          : undefined
      }
    />
  );
}

function RepositoriesList({ repositories }: { repositories: string[] }) {
  if (repositories.length === 0) {
    return <span className="text-dim">—</span>;
  }
  return (
    <ul className="flex flex-col gap-1">
      {repositories.map((url, i) => (
        <li key={`${url}-${i}`} className="flex items-start gap-1.5 min-w-0">
          <Link2 className="w-3.5 h-3.5 text-dim shrink-0 mt-0.5" />
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="text-accent underline break-all text-xs mono"
          >
            {url}
          </a>
        </li>
      ))}
    </ul>
  );
}

function OsVersionDetail({
  version,
  canEdit,
  mockMode,
  onChanged,
}: {
  version: OsVersion;
  canEdit: boolean;
  mockMode: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onDelete() {
    if (mockMode) {
      toast.warn("Mock-режим — удаление не отправляется на backend.");
      return;
    }
    if (
      !(await confirm.confirm({
        message:
          `Удалить версию «${version.name}»? Если на неё ссылается хотя бы один ` +
          `сервер, backend откажет (409 OS_VERSION_IN_USE) — сначала переназначьте ` +
          `версию у таких серверов через os-sync.`,
        danger: true,
        confirmLabel: "Удалить",
      }))
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      await deleteOsVersion(version.id);
      toast.success("Версия удалена");
      onChanged();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <OsVersionForm
        mockMode={mockMode}
        existing={version}
        onDone={() => {
          setEditing(false);
          onChanged();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div className="card w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <HardDrive className="w-4 h-4 text-accent" /> {version.name}
        </h3>
        {canEdit && (
          <div className="flex gap-2">
            <button
              className="btn flex items-center gap-1"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              <Pencil className="w-4 h-4" /> Изменить
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={busy}
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Удалить
            </button>
          </div>
        )}
      </div>
      <StatRow k="id" v={<span className="mono">{version.id}</span>} />
      <StatRow k="name" v={<span className="mono">{version.name}</span>} />
      <StatRow k="description" v={version.description ?? "—"} />
      <StatRow
        k="repositories"
        v={<RepositoriesList repositories={version.repositories} />}
      />
      <StatRow
        k="discovered_at"
        v={<span className="mono">{formatMsk(version.discovered_at)}</span>}
      />
      <StatRow
        k="updated_at"
        v={<span className="mono">{formatMsk(version.updated_at)}</span>}
      />
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}
    </div>
  );
}

export function OsVersionForm({
  mockMode,
  existing,
  onDone,
  onCancel,
}: {
  mockMode: boolean;
  existing?: OsVersion;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const inline = useInlineState();
  const toast = useToast();
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  // Репозитории редактируются как многострочный textarea: одна строка — один URL.
  const [reposText, setReposText] = useState(
    (existing?.repositories ?? []).join("\n"),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function parseRepos(): string[] {
    return reposText
      .split("\n")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }

  function cancel() {
    if (onCancel) onCancel();
    else inline.close();
  }

  async function submit() {
    if (!name.trim()) {
      toast.warn("name обязателен");
      return;
    }
    if (mockMode) {
      toast.warn("Mock-режим — изменения не отправляются на backend.");
      onDone();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const repos = parseRepos();
      if (isEdit && existing) {
        const body: OsVersionUpdateRequest = {
          name: name.trim(),
          description: description.trim() || null,
          repositories: repos,
        };
        await updateOsVersion(existing.id, body);
        toast.success("Версия обновлена");
      } else {
        const body: OsVersionCreateRequest = {
          name: name.trim(),
          description: description.trim() || undefined,
          repositories: repos,
        };
        await createOsVersion(body);
        toast.success("Версия зарегистрирована");
      }
      onDone();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <HardDrive className="w-4 h-4 text-accent" />
        {isEdit ? `Изменить версию ${existing?.name}` : "Новая OS-версия"}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow
          label="name"
          hint="каноническое машинное имя · UNIQUE (astra-1.7, ubuntu-22.04, …)"
        >
          <input
            className="input mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="astra-1.7"
          />
        </FormRow>
        <FormRow label="description" hint="произвольное описание для каталога">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
        <FormRow
          label="repositories"
          hint="по одному http(s)-URL на строку · до 64 штук"
        >
          <textarea
            className="input mono text-xs"
            rows={4}
            value={reposText}
            onChange={(e) => setReposText(e.target.value)}
            placeholder={"https://download.astralinux.ru/astra/stable/1.7\nhttps://..."}
          />
        </FormRow>
      </div>
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={cancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || !name.trim()}
        >
          {busy ? "..." : isEdit ? "Сохранить" : "Создать"}
        </button>
      </div>
    </div>
  );
}
