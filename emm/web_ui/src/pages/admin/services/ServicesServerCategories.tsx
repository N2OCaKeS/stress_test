/**
 * Каталог категорий серверов по мощности (`/admin/services.server.categories`).
 *
 * Источник правды — `GET /api/server/v1/server-categories` (глобальный
 * каталог, без dept-привязки). Список читается публично; CRUD идёт под
 * action-матрицей `server_service`: create/update/delete несёт сервисная
 * роль `server.admin`, а также dep_admin (он держит admin в своём отделе).
 * Platform-роли (account_admin / loging_admin) режет middleware на 403
 * PLATFORM_ADMIN_BUSINESS_DATA_DENIED — поэтому страница им не показывается.
 *
 * Удаление упирается в FK `servers.category_id ondelete=RESTRICT`: категорию,
 * на которую ссылается хотя бы один сервер, backend не даст снести (409
 * SERVER_CATEGORY_IN_USE) — это явно отражено в подсказке кнопки.
 */

import { useState } from "react";
import { Gauge, Trash2, Pencil } from "lucide-react";
import { InlineEditor, FormRow, StatRow, useInlineState } from "./_inline";
import { formatMsk } from "@/lib/datetime";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  createServerCategory,
  deleteServerCategory,
  listServerCategories,
  updateServerCategory,
} from "@/api/server/serverCategories";
import type {
  OffsetPaginatedResponse,
  ServerCategory,
  ServerCategoryCreateRequest,
  ServerCategoryUpdateRequest,
} from "@/api/server/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";

// Управление каталогом несёт server.admin (и dep_admin в своём департаменте).
// Зеркалит гейт ServicesServerPermissions / canManageOsVersions — backend
// режет platform-роли.
export function canManageServerCategories(
  persona: ReturnType<typeof usePersona>["persona"],
): boolean {
  return (
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.server === "admin"
  );
}

// Категорий в проекте единицы (по мощности) — тянем одной страницей.
const PAGE_LIMIT = 500;

export function ServicesServerCategories() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const canEdit = canManageServerCategories(persona);

  const listQ = useQuery<OffsetPaginatedResponse<ServerCategory>>(
    () => listServerCategories({ limit: PAGE_LIMIT }),
    [],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  const items: ServerCategory[] = mockMode
    ? [
        {
          id: "cat_mock_low",
          code: "low_server",
          label: "LowServer",
          description: "Слабые тестовые сервера",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          created_by: null,
        },
        {
          id: "cat_mock_high",
          code: "high_server",
          label: "HighServer",
          description: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          created_by: null,
        },
      ]
    : listQ.data?.items ?? [];

  return (
    <InlineEditor
      title="Категории серверов · server_service"
      icon={Gauge}
      hint="глобальный каталог категорий по мощности · CRUD — server.admin / dep_admin"
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
      getId={(c) => c.id}
      canEdit={canEdit}
      readonlyNote={
        canEdit
          ? undefined
          : "Регистрация, правка и удаление категорий — server.admin или department_admin."
      }
      emptyHint="Выберите категорию слева или заведите новую."
      renderRow={({ item, active, onSelect }) => (
        <button
          className={`cred-row text-left ${active ? "active" : ""}`}
          onClick={onSelect}
        >
          <div className="flex items-center gap-2">
            <Gauge className="w-4 h-4 text-accent" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{item.label}</div>
              <div className="text-[11px] text-dim truncate mono">{item.code}</div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(c, { onClose }) => (
        <ServerCategoryDetail
          category={c}
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
              <ServerCategoryForm
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

function ServerCategoryDetail({
  category,
  canEdit,
  mockMode,
  onChanged,
}: {
  category: ServerCategory;
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
          `Удалить категорию «${category.label}»? Если на неё ссылается хотя бы ` +
          `один сервер, backend откажет (409 SERVER_CATEGORY_IN_USE) — сначала ` +
          `переназначьте категорию у таких серверов.`,
        danger: true,
        confirmLabel: "Удалить",
      }))
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      await deleteServerCategory(category.id);
      toast.success("Категория удалена");
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
      <ServerCategoryForm
        mockMode={mockMode}
        existing={category}
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
        <h3 className="font-semibold flex items-center gap-2">
          <Gauge className="w-4 h-4 text-accent" /> {category.label}
        </h3>
        {canEdit && (
          <div className="flex gap-2">
            <Button
              className="flex items-center gap-1"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              <Pencil className="w-4 h-4" /> Изменить
            </Button>
            <Button variant="danger"
              className="flex items-center gap-1"
              disabled={busy}
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Удалить
            </Button>
          </div>
        )}
      </div>
      <StatRow k="id" v={<span className="mono">{category.id}</span>} />
      <StatRow k="code" v={<span className="mono">{category.code}</span>} />
      <StatRow k="label" v={category.label} />
      <StatRow k="description" v={category.description ?? "—"} />
      <StatRow k="created_by" v={category.created_by ?? "—"} />
      <StatRow
        k="created_at"
        v={<span className="mono">{formatMsk(category.created_at)}</span>}
      />
      <StatRow
        k="updated_at"
        v={<span className="mono">{formatMsk(category.updated_at)}</span>}
      />
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}
    </div>
  );
}

export function ServerCategoryForm({
  mockMode,
  existing,
  onDone,
  onCancel,
}: {
  mockMode: boolean;
  existing?: ServerCategory;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const inline = useInlineState();
  const toast = useToast();
  const isEdit = !!existing;
  const [code, setCode] = useState(existing?.code ?? "");
  const [label, setLabel] = useState(existing?.label ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function cancel() {
    if (onCancel) onCancel();
    else inline.close();
  }

  async function submit() {
    if (!code.trim() || !label.trim()) {
      toast.warn("code и label обязательны");
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
      if (isEdit && existing) {
        const body: ServerCategoryUpdateRequest = {
          code: code.trim(),
          label: label.trim(),
          description: description.trim() || null,
        };
        await updateServerCategory(existing.id, body);
        toast.success("Категория обновлена");
      } else {
        const body: ServerCategoryCreateRequest = {
          code: code.trim(),
          label: label.trim(),
          description: description.trim() || undefined,
        };
        await createServerCategory(body);
        toast.success("Категория заведена");
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
        <Gauge className="w-4 h-4 text-accent" />
        {isEdit ? `Изменить категорию ${existing?.label}` : "Новая категория"}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow
          label="code"
          hint="машинный код · UNIQUE (low_server, middle_server, high_server, workstation, …)"
        >
          <input
            className="input mono"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="low_server"
          />
        </FormRow>
        <FormRow label="label" hint="человекочитаемое название">
          <input
            className="input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="LowServer"
          />
        </FormRow>
        <FormRow label="description" hint="произвольное описание для каталога">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
      </div>
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}
      <div className="mt-4 flex gap-2 justify-end">
        <Button onClick={cancel} disabled={busy}>
          Отмена
        </Button>
        <Button variant="primary"
          onClick={submit}
          disabled={busy || !code.trim() || !label.trim()}
        >
          {busy ? "..." : isEdit ? "Сохранить" : "Создать"}
        </Button>
      </div>
    </div>
  );
}
