/**
 * Каталог реестра боксов (`/boxes`) — образы гостевых ВМ отдела.
 *
 * Раскладка как у остальных CRUD-разделов: Shell + средняя панель (список
 * боксов + поиск + кнопка создания) + рабочая область (карточка бокса или
 * форма создания/редактирования).
 *
 * Реестр dept-scoped (`GET /api/server/v1/boxes` отдаёт боксы отдела вызывающего).
 * Просмотр — любой носитель server-зоны (reader/operator/admin, dep_admin);
 * CRUD — admin-плоскость (server.admin / server.operator / dep_admin); пароль
 * базовой учётки раскрывается только держателю `view_password` (admin / dep_admin).
 * Backend перепроверяет фактические права — клиентский гейт лишь прячет заведомо
 * отбойные кнопки.
 */

import { useMemo, useState } from "react";
import {
  Box as BoxIcon,
  Plus,
  Search,
  Pencil,
  Trash2,
  Eye,
  EyeOff,
  ArrowLeft,
  Link2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  listBoxes,
  getBox,
  createBox,
  updateBox,
  deleteBox,
  type Box,
  type BoxCreateInput,
  type BoxUpdateInput,
} from "@/api/server/boxes";
import type { OffsetPaginatedResponse } from "@/api/server/types";
import { fromBase64 } from "@/lib/base64";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useDeptLabel } from "@/lib/labels";
import { hasServerZoneAccess } from "@/lib/rbac";
import type { Persona } from "@/types/persona";
import { formatMsk } from "@/lib/datetime";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

// Управление реестром несёт admin-плоскость server-зоны: server.admin /
// server.operator либо dep_admin своего отдела. Backend перепроверит.
function canManageBoxes(persona: Persona): boolean {
  if (!hasServerZoneAccess(persona)) return false;
  const role = persona.service_roles?.server;
  return persona.platform_role === "dep_admin" || role === "admin" || role === "operator";
}

// Раскрытие пароля базовой учётки — под `view_password`. Проксируем на самую
// узкую admin-плоскость (server.admin / dep_admin); backend решает окончательно.
function canRevealBoxPassword(persona: Persona): boolean {
  if (!hasServerZoneAccess(persona)) return false;
  return persona.platform_role === "dep_admin" || persona.service_roles?.server === "admin";
}

const PAGE_LIMIT = 500;

const MOCK_BOXES: Box[] = [
  {
    id: "box_mock_alse18",
    name: "alse-1.8-base",
    format: "qcow2",
    download_url: "https://download.astralinux.ru/boxes/alse-1.8-base.qcow2",
    base_user_login: "u",
    os_versions: ["astra-1.8"],
    initial_snapshots: ["clean"],
    department_id: "core",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-10T00:00:00Z",
  },
  {
    id: "box_mock_vmstation",
    name: "vm_station",
    format: "tar",
    download_url: "ftp://ftp.local/boxes/vm_station.tar.gz",
    base_user_login: "u",
    os_versions: ["astra-1.7", "astra-1.8"],
    initial_snapshots: ["1.7_orel", "1.8_orel", "1.8_smolensk"],
    department_id: "core",
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-04-20T00:00:00Z",
  },
];

/** Средняя панель: поиск + список боксов + кнопка создания. */
function BoxAside({
  boxes,
  loading,
  error,
  onRetry,
  selectedId,
  onSelect,
  onCreate,
  canCreate,
  search,
  onSearch,
}: {
  boxes: Box[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  canCreate: boolean;
  search: string;
  onSearch: (v: string) => void;
}) {
  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="p-3 flex flex-col gap-2 border-b border-token shrink-0">
        <div className="flex items-center gap-2">
          <BoxIcon className="w-5 h-5 text-accent" />
          <h2 className="font-semibold text-sm flex-1">Боксы</h2>
          {canCreate && (
            <Button variant="primary" size="sm"
              className="flex items-center gap-1"
              onClick={onCreate}
              title="Зарегистрировать новый бокс"
            >
              <Plus className="w-4 h-4" /> Новый
            </Button>
          )}
        </div>
        <div className="relative">
          <Search className="w-4 h-4 text-dim absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            className="input pl-8 w-full"
            placeholder="Поиск по имени, формату, ОС…"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-1">
        {error && (
          <div className="alert-danger text-xs flex items-center justify-between gap-2">
            <span className="truncate">{error}</span>
            <Button size="sm" onClick={onRetry}>
              Повторить
            </Button>
          </div>
        )}
        {loading && boxes.length === 0 ? (
          <div className="flex items-center justify-center py-10">
            <div className="spinner" />
          </div>
        ) : boxes.length === 0 ? (
          <div className="text-sm text-dim py-8 text-center">
            {search.trim() ? "Ничего не найдено." : "Реестр боксов пуст."}
          </div>
        ) : (
          boxes.map((b) => (
            <button
              key={b.id}
              className={`cred-row text-left ${selectedId === b.id ? "active" : ""}`}
              onClick={() => onSelect(b.id)}
            >
              <div className="flex items-center gap-2">
                <BoxIcon className="w-4 h-4 text-accent shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate mono">{b.name}</div>
                  <div className="text-[11px] text-dim truncate">
                    {b.format}
                    {b.os_versions.length > 0 ? ` · ${b.os_versions.join(", ")}` : ""}
                  </div>
                </div>
              </div>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}

/** Строка-чип со списком значений (ОС, снимки). */
function ChipList({ values, empty }: { values: string[]; empty: string }) {
  if (values.length === 0) {
    return <span className="text-[11px] text-dim">{empty}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((v) => (
        <Badge key={v} className="mono text-[11px]">
          {v}
        </Badge>
      ))}
    </div>
  );
}

/** Карточка выбранного бокса + reveal пароля + действия. */
function BoxDetail({
  box,
  canManage,
  canReveal,
  mock,
  onEdit,
  onDelete,
}: {
  box: Box;
  canManage: boolean;
  canReveal: boolean;
  mock: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const deptLabel = useDeptLabel(box.department_id);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const toast = useToast();

  async function reveal() {
    if (revealed !== null) {
      setRevealed(null);
      return;
    }
    if (mock) {
      setRevealed("mock-password");
      return;
    }
    setRevealing(true);
    try {
      const full = await getBox(box.id);
      if (full.base_user_password_b64) {
        setRevealed(fromBase64(full.base_user_password_b64));
      } else {
        toast.warn("Пароль недоступен (нет права view_password или он не задан)");
      }
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось раскрыть пароль"));
    } finally {
      setRevealing(false);
    }
  }

  const urlHref = /^https?:\/\//i.test(box.download_url) ? box.download_url : null;

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 w-full max-w-2xl flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <BoxIcon className="w-6 h-6 text-accent shrink-0" />
          <h2 className="font-semibold text-lg mono truncate flex-1">{box.name}</h2>
          {canManage && (
            <>
              <Button size="sm"
                className="flex items-center gap-1"
                onClick={onEdit}
                title="Редактировать бокс"
              >
                <Pencil className="w-4 h-4" /> Изменить
              </Button>
              <Button size="sm"
                className="text-danger flex items-center gap-1"
                onClick={onDelete}
                title="Удалить бокс"
              >
                <Trash2 className="w-4 h-4" /> Удалить
              </Button>
            </>
          )}
        </div>

        <div className="card flex flex-col gap-3">
          <div className="grid grid-cols-[9rem_1fr] gap-y-2 gap-x-3 text-sm items-center">
            <span className="text-dim text-xs">Формат</span>
            <span className="mono">{box.format}</span>

            <span className="text-dim text-xs">Источник</span>
            <span className="flex items-center gap-1 min-w-0">
              <span className="mono truncate">{box.download_url}</span>
              {urlHref && (
                <a
                  href={urlHref}
                  target="_blank"
                  rel="noreferrer"
                  className="text-dim hover:text-accent shrink-0"
                  title="Открыть ссылку"
                >
                  <Link2 className="w-3.5 h-3.5" />
                </a>
              )}
            </span>

            <span className="text-dim text-xs">Базовая учётка</span>
            <span className="mono">{box.base_user_login}</span>

            <span className="text-dim text-xs">Пароль учётки</span>
            <span className="flex items-center gap-2">
              <span className="mono">
                {revealed !== null ? revealed : "••••••••"}
              </span>
              {canReveal && (
                <Button variant="ghost" size="sm"
                  className="flex items-center gap-1"
                  onClick={reveal}
                  disabled={revealing}
                  title={revealed !== null ? "Скрыть" : "Показать пароль"}
                >
                  {revealed !== null ? (
                    <EyeOff className="w-3.5 h-3.5" />
                  ) : (
                    <Eye className="w-3.5 h-3.5" />
                  )}
                  {revealing ? "…" : revealed !== null ? "Скрыть" : "Показать"}
                </Button>
              )}
            </span>

            <span className="text-dim text-xs">Отдел</span>
            <span>{deptLabel}</span>

            <span className="text-dim text-xs">Версии ОС</span>
            <ChipList values={box.os_versions} empty="не указаны" />

            <span className="text-dim text-xs">Стартовые снимки</span>
            <ChipList values={box.initial_snapshots} empty="нет" />
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-dim border-t border-token pt-2">
            <span>
              создан: <span className="mono">{formatMsk(box.created_at)}</span>
            </span>
            <span>
              обновлён: <span className="mono">{formatMsk(box.updated_at)}</span>
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

/** Форма создания/редактирования бокса. Без `box` — режим создания. */
function BoxForm({
  box,
  departmentId,
  onCancel,
  onSaved,
  mock,
}: {
  box?: Box;
  departmentId: string;
  onCancel: () => void;
  onSaved: () => void;
  mock: boolean;
}) {
  const editing = !!box;
  const toast = useToast();
  const [name, setName] = useState(box?.name ?? "");
  const [format, setFormat] = useState(box?.format ?? "qcow2");
  const [downloadUrl, setDownloadUrl] = useState(box?.download_url ?? "");
  const [baseLogin, setBaseLogin] = useState(box?.base_user_login ?? "u");
  const [password, setPassword] = useState("");
  const [osVersions, setOsVersions] = useState((box?.os_versions ?? []).join(", "));
  const [snapshots, setSnapshots] = useState(
    (box?.initial_snapshots ?? []).join(", "),
  );
  const [submitting, setSubmitting] = useState(false);

  const urlError =
    downloadUrl.trim() && !/^(https?|ftp|smb):\/\//i.test(downloadUrl.trim())
      ? "Ожидается URL со схемой https/http/ftp/smb"
      : null;
  const valid =
    !!name.trim() &&
    !!format.trim() &&
    !!downloadUrl.trim() &&
    !urlError &&
    !!baseLogin.trim();

  function splitList(s: string): string[] {
    return s
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      if (mock) {
        toast.info("mock-режим: изменения не сохраняются");
        onSaved();
        return;
      }
      if (editing) {
        const body: BoxUpdateInput = {
          name: name.trim(),
          format: format.trim(),
          download_url: downloadUrl.trim(),
          base_user_login: baseLogin.trim(),
          os_versions: splitList(osVersions),
          initial_snapshots: splitList(snapshots),
        };
        if (password) body.password = password;
        await updateBox(box!.id, body);
        toast.success("Бокс обновлён");
      } else {
        const body: BoxCreateInput = {
          name: name.trim(),
          format: format.trim(),
          download_url: downloadUrl.trim(),
          base_user_login: baseLogin.trim(),
          os_versions: splitList(osVersions),
          initial_snapshots: splitList(snapshots),
          department_id: departmentId,
        };
        if (password) body.password = password;
        await createBox(body);
        toast.success("Бокс зарегистрирован");
      }
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить бокс"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <form onSubmit={submit} className="p-5 w-full max-w-2xl flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Button variant="ghost"
            type="button"
            className="flex items-center gap-1"
            onClick={onCancel}
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </Button>
          <h2 className="font-semibold text-lg">
            {editing ? `Бокс ${box!.name}` : "Новый бокс"}
          </h2>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Имя *</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="alse-1.8-base"
              autoFocus
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Формат *</span>
            <input
              className="input"
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              placeholder="qcow2 / tar / raw"
              list="box-formats"
            />
            <datalist id="box-formats">
              <option value="qcow2" />
              <option value="qcow" />
              <option value="raw" />
              <option value="tar" />
              <option value="tar.gz" />
              <option value="tgz" />
              <option value="tar.xz" />
            </datalist>
          </label>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Ссылка на образ *</span>
          <input
            className="input"
            value={downloadUrl}
            onChange={(e) => setDownloadUrl(e.target.value)}
            placeholder="https://… / ftp://… / smb://…"
          />
          {urlError && <span className="text-[11px] text-danger">{urlError}</span>}
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Базовая учётка *</span>
            <input
              className="input"
              value={baseLogin}
              onChange={(e) => setBaseLogin(e.target.value)}
              placeholder="u"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">
              Пароль учётки {editing ? "(пусто — не менять)" : ""}
            </span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={editing ? "оставить прежний" : "пароль базовой учётки"}
              autoComplete="new-password"
            />
          </label>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Версии ОС (через запятую)</span>
          <input
            className="input"
            value={osVersions}
            onChange={(e) => setOsVersions(e.target.value)}
            placeholder="astra-1.7, astra-1.8"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Стартовые снимки (через запятую)</span>
          <input
            className="input"
            value={snapshots}
            onChange={(e) => setSnapshots(e.target.value)}
            placeholder="clean, 1.8_orel"
          />
        </label>

        <div className="flex items-center gap-2">
          <Button type="button" onClick={onCancel}>
            Отмена
          </Button>
          <div className="flex-1" />
          <Button variant="primary"
            type="submit"
            disabled={!valid || submitting}
          >
            {submitting
              ? "Сохраняем…"
              : editing
                ? "Сохранить"
                : "Зарегистрировать"}
          </Button>
        </div>
      </form>
    </section>
  );
}

function BoxCatalogInner() {
  const { persona } = usePersona();
  const mock = useMockMode();
  const toast = useToast();
  const { confirm } = useConfirm();

  const canManage = canManageBoxes(persona);
  const canReveal = canRevealBoxPassword(persona);
  const zoneBlocked = !hasServerZoneAccess(persona);
  const deptId = persona.dept_id ?? "";

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "create" | "edit">("view");
  const [search, setSearch] = useState("");

  const listQ = useQuery<OffsetPaginatedResponse<Box>>(
    () => listBoxes({ limit: PAGE_LIMIT }),
    [],
    { enabled: !mock && !zoneBlocked, keepPreviousDataOnError: true },
  );

  const all: Box[] = mock ? MOCK_BOXES : listQ.data?.items ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (b) =>
        b.name.toLowerCase().includes(q) ||
        b.format.toLowerCase().includes(q) ||
        b.os_versions.some((v) => v.toLowerCase().includes(q)),
    );
  }, [all, search]);

  const selected = all.find((b) => b.id === selectedId) ?? null;

  const errMsg =
    !mock && listQ.error
      ? listQ.error instanceof ApiError
        ? `${listQ.error.errorCode}: ${listQ.error.message}`
        : listQ.error.message
      : null;

  async function handleDelete(box: Box) {
    const ok = await confirm({
      title: "Удалить бокс?",
      message: `Запись реестра «${box.name}» будет удалена.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      if (!mock) await deleteBox(box.id);
      toast.success("Бокс удалён");
      setSelectedId(null);
      setMode("view");
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить бокс"));
    }
  }

  const aside = (
    <BoxAside
      boxes={filtered}
      loading={!mock && listQ.loading}
      error={errMsg}
      onRetry={() => listQ.refetch()}
      selectedId={selectedId}
      onSelect={(id) => {
        setSelectedId(id);
        setMode("view");
      }}
      onCreate={() => {
        setSelectedId(null);
        setMode("create");
      }}
      canCreate={canManage}
      search={search}
      onSearch={setSearch}
    />
  );

  let main: React.ReactNode;
  if (zoneBlocked) {
    main = (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="text-sm text-dim">
          Реестр боксов доступен ролям server-зоны вашего отдела.
        </div>
      </section>
    );
  } else if (mode === "create") {
    main = (
      <BoxForm
        departmentId={deptId}
        mock={mock}
        onCancel={() => setMode("view")}
        onSaved={() => {
          setMode("view");
          listQ.refetch();
        }}
      />
    );
  } else if (mode === "edit" && selected) {
    main = (
      <BoxForm
        box={selected}
        departmentId={selected.department_id}
        mock={mock}
        onCancel={() => setMode("view")}
        onSaved={() => {
          setMode("view");
          listQ.refetch();
        }}
      />
    );
  } else if (selected) {
    main = (
      <BoxDetail
        box={selected}
        canManage={canManage}
        canReveal={canReveal}
        mock={mock}
        onEdit={() => setMode("edit")}
        onDelete={() => handleDelete(selected)}
      />
    );
  } else {
    main = (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="text-sm text-dim text-center px-6">
          Выберите бокс слева
          {canManage ? " или зарегистрируйте новый." : "."}
        </div>
      </section>
    );
  }

  return (
    <Shell breadcrumb="server_service / боксы" middle={aside}>
      {main}
    </Shell>
  );
}

export function BoxCatalog() {
  return <BoxCatalogInner />;
}
