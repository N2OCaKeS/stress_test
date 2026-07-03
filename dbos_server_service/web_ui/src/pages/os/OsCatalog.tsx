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
 * носителю того же права доступна только регистрация новой версии — через ту
 * же форму `OsVersionForm`, чтобы не плодить дубль логики создания.
 */

import { useMemo, useState } from "react";
import { HardDrive, Link2, Search, Plus } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { formatMsk } from "@/lib/datetime";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import { listOsVersions } from "@/api/server/osVersions";
import type { OffsetPaginatedResponse, OsVersion } from "@/api/server/types";
import { usePersona } from "@/contexts/PersonaContext";
import {
  OsVersionForm,
  canManageOsVersions,
} from "@/pages/admin/services/ServicesOsVersions";

// Сколько версий тянем за один запрос «Загрузить ещё». В проекте каталог
// небольшой (десятки записей), поэтому шага в полсотни хватает с запасом.
const PAGE_SIZE = 50;

const MOCK_ITEMS: OsVersion[] = [
  {
    id: "osv_mock_astra17",
    name: "astra-1.7",
    description: "Astra Linux SE 1.7 (Орёл)",
    repositories: ["https://download.astralinux.ru/astra/stable/1.7"],
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
    discovered_at: "2026-02-10T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  },
  {
    id: "osv_mock_ubuntu2204",
    name: "ubuntu-22.04",
    description: null,
    repositories: [],
    discovered_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

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
          <div
            key={`${repo}-${i}`}
            title={repo}
            className="badge flex items-center gap-1 max-w-full"
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
          </div>
        );
      })}
    </div>
  );
}

function OsVersionCard({ version }: { version: OsVersion }) {
  return (
    <div className="card flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <HardDrive className="w-4 h-4 text-accent shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="font-semibold mono truncate">{version.name}</div>
          {version.description && (
            <div className="text-xs text-dim">{version.description}</div>
          )}
        </div>
      </div>
      <RepoBadges repositories={version.repositories} />
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-dim border-t border-token pt-2">
        <span>
          обнаружена: <span className="mono">{formatMsk(version.discovered_at)}</span>
        </span>
        <span>
          обновлена: <span className="mono">{formatMsk(version.updated_at)}</span>
        </span>
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

  const listQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit }),
    [limit],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  const all: OsVersion[] = mockMode ? MOCK_ITEMS : listQ.data?.items ?? [];
  const total = mockMode ? MOCK_ITEMS.length : listQ.data?.total ?? all.length;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (v) =>
        v.name.toLowerCase().includes(q) ||
        (v.description ?? "").toLowerCase().includes(q) ||
        v.repositories.some((r) => r.toLowerCase().includes(q)),
    );
  }, [all, search]);

  // «Загрузить ещё» имеет смысл, пока на руках меньше записей, чем total.
  const hasMore = !mockMode && all.length < total;
  const loadingMore = listQ.loading && all.length > 0;

  const errMsg =
    !mockMode && listQ.error
      ? listQ.error instanceof ApiError
        ? `${listQ.error.errorCode}: ${listQ.error.message}`
        : listQ.error.message
      : null;

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
          <button
            className="btn btn-primary btn-sm flex items-center gap-1 ml-auto"
            onClick={() => setCreating(true)}
            disabled={creating}
          >
            <Plus className="w-4 h-4" /> Добавить версию ОС
          </button>
        )}
      </div>

      {creating && (
        <OsVersionForm
          mockMode={mockMode}
          onDone={() => {
            setCreating(false);
            listQ.refetch();
          }}
          onCancel={() => setCreating(false)}
        />
      )}

      <div className="relative max-w-sm">
        <Search className="w-4 h-4 text-dim absolute left-2.5 top-1/2 -translate-y-1/2" />
        <input
          className="input pl-8 w-full"
          placeholder="Поиск по имени, описанию, репозиторию…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {errMsg && (
        <div className="alert-danger text-xs flex items-center justify-between gap-3">
          <span>{errMsg}</span>
          <button className="btn" onClick={() => listQ.refetch()}>
            Повторить
          </button>
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
        <div className="flex flex-col gap-3">
          {filtered.map((v) => (
            <OsVersionCard key={v.id} version={v} />
          ))}
        </div>
      )}

      {hasMore && (
        <div className="flex justify-center pt-1">
          <button
            className="btn"
            disabled={loadingMore}
            onClick={() => setLimit((n) => n + PAGE_SIZE)}
          >
            {loadingMore ? "Загрузка…" : "Загрузить ещё"}
          </button>
        </div>
      )}
    </div>
  );
}

export function OsCatalog() {
  return (
    <Shell breadcrumb="Главная / ОС">
      <OsCatalogBody />
    </Shell>
  );
}
