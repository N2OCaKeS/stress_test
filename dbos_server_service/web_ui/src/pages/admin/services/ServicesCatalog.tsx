/**
 * Каталог сервисов платформы (`/admin/services.services_catalog`).
 *
 * Источник правды — `GET /api/auth/v1/services`. `account_admin` может
 * добавлять/удалять сервис; остальные admin-роли — только просмотр.
 * Внутри карточки сервиса — вложенная секция «Роли по отделам»: dropdown
 * с отделами + список ролей (read-only) и ссылка на полное управление
 * (`/admin/services.<svc>.roles`).
 */

import { useEffect, useState } from "react";
import { Layers, ShieldCheck, Trash2, ExternalLink, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import {
  InlineEditor,
  FormRow,
  LogingPlatformRoleBanner,
  StatRow,
  useInlineState,
} from "./_inline";
import { ServiceRolesInline } from "./_serviceRolesInline";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import {
  createService,
  deleteService,
  listServices,
} from "@/api/auth/services";
import { listDepartments } from "@/api/auth/departments";
import type {
  Department,
  Service,
  ServiceCreateRequest,
} from "@/api/auth/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { isDepAdmin, isPlatformWideAdmin, personaDeptId } from "@/lib/rbac";

// Backend service_name → admin item route segment. Идентификатор
// динамического пункта в `buildAdminItems` строится по шаблону
// `services.<service_name>.roles`; `auth_service` исключён там же
// (отдельных «ролей» у него в UI нет — есть platform_roles + users/depts).
function backendToAdminItem(serviceName: string): string | undefined {
  if (serviceName === "auth_service") return undefined;
  return `services.${serviceName}.roles`;
}

export function ServicesCatalog() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const canEdit = isPlatformWideAdmin(persona);

  const servicesQ = useQuery<Service[]>(
    () => listServices(),
    [],
    { enabled: !mockMode },
  );

  const items: Service[] = mockMode
    ? [
        {
          service_name: "auth_service",
          display_name: "DTQC-EMM auth (mock)",
          description: null,
          is_active: true,
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          service_name: "server_service",
          display_name: "DTQC-EMM server (mock)",
          description: null,
          is_active: true,
          created_at: "2026-01-01T00:00:00Z",
        },
      ]
    : servicesQ.data ?? [];

  return (
    <InlineEditor
      title="Сервисы · auth_service"
      icon={Layers}
      hint="каталог сервисов платформы · POST/DELETE — account_admin"
      items={items}
      getId={(s) => s.service_name}
      canEdit={canEdit}
      readonlyNote={
        canEdit
          ? undefined
          : "Регистрация и удаление сервисов — только account_admin."
      }
      listHeader={
        !mockMode ? (
          <div className="flex flex-col gap-1">
            {servicesQ.loading && (
              <Loader2 className="w-3 h-3 animate-spin text-dim" aria-label="Loading" />
            )}
            {servicesQ.error && (
              <div className="alert-danger text-[11px]">
                {servicesQ.error instanceof ApiError
                  ? `${servicesQ.error.errorCode}: ${servicesQ.error.message}`
                  : servicesQ.error.message}
              </div>
            )}
          </div>
        ) : null
      }
      renderRow={({ item, active, onSelect }) => (
        <button
          className={`cred-row text-left ${active ? "active" : ""}`}
          onClick={onSelect}
        >
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-accent" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{item.display_name}</div>
              <div className="text-[11px] text-dim truncate mono">
                {item.service_name}
              </div>
            </div>
            {item.is_active === false && (
              <span className="badge">disabled</span>
            )}
          </div>
        </button>
      )}
      renderDetail={(svc, { onClose }) => (
        <ServiceDetail
          svc={svc}
          canEdit={canEdit}
          mockMode={mockMode}
          onChanged={() => {
            servicesQ.refetch();
            onClose();
          }}
        />
      )}
      renderCreate={
        canEdit
          ? (onClose) => (
              <ServiceForm
                mockMode={mockMode}
                onDone={() => {
                  servicesQ.refetch();
                  onClose();
                }}
              />
            )
          : undefined
      }
    />
  );
}

function ServiceDetail({
  svc,
  canEdit,
  mockMode,
  onChanged,
}: {
  svc: Service;
  canEdit: boolean;
  mockMode: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onDelete() {
    if (mockMode) {
      toast.warn("Mock-режим — удаление не отправляется на backend.");
      return;
    }
    if (
      !window.confirm(
        `Удалить сервис «${svc.service_name}»? CASCADE уносит dept-access и роли.`,
      )
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      await deleteService(svc.service_name);
      toast.success("Сервис удалён");
      onChanged();
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  const adminItemId = backendToAdminItem(svc.service_name);

  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <Layers className="w-4 h-4 text-accent" /> {svc.service_name}
        </h3>
        {canEdit && (
          <button
            className="btn btn-danger flex items-center gap-1"
            disabled={busy}
            onClick={onDelete}
          >
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        )}
      </div>
      <StatRow k="service_name" v={<span className="mono">{svc.service_name}</span>} />
      <StatRow k="display_name" v={svc.display_name} />
      <StatRow k="description" v={svc.description ?? "—"} />
      <StatRow
        k="is_active"
        v={svc.is_active === false ? "false" : "true"}
      />
      <StatRow k="created_at" v={<span className="mono">{svc.created_at}</span>} />
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}

      {!mockMode && svc.service_name === "loging_service" && (
        <div className="mt-4 pt-3 border-t border-token">
          <div className="text-sm font-semibold mb-2 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            Доступ к loging_service
          </div>
          <LogingPlatformRoleBanner />
        </div>
      )}
      {!mockMode && svc.service_name !== "loging_service" && (
        <ServiceRolesByDept
          serviceName={svc.service_name}
          adminItemId={adminItemId}
        />
      )}
    </div>
  );
}

// Просмотр + CRUD ролей выбранного отдела + ссылка на full management.
// CRUD доступен account_admin (любой отдел) и dep_admin (свой отдел).
function ServiceRolesByDept({
  serviceName,
  adminItemId,
}: {
  serviceName: string;
  adminItemId?: string;
}) {
  const { persona } = usePersona();
  const platformAdmin = isPlatformWideAdmin(persona);
  const depAdmin = isDepAdmin(persona);
  const myDept = personaDeptId(persona);

  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);
  const [deptId, setDeptId] = useState<string | null>(null);

  useEffect(() => {
    if (deptId) return;
    const list = deptsQ.data ?? [];
    if (list.length === 0) return;
    // dep_admin видит только свой отдел — сразу подставляем его.
    if (depAdmin && myDept) {
      const own = list.find((d) => d.id === myDept);
      if (own) {
        setDeptId(own.id);
        return;
      }
    }
    setDeptId(list[0].id);
  }, [deptId, deptsQ.data, depAdmin, myDept]);

  const canEdit =
    !!deptId && (platformAdmin || (depAdmin && myDept === deptId));

  return (
    <div className="mt-4 pt-3 border-t border-token">
      <div className="text-sm font-semibold mb-2 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        Роли по отделам
      </div>
      <div className="text-[11px] text-dim mb-2">
        Просмотр и базовый CRUD ролей сервиса в выбранном отделе. Bulk-assign /
        revoke юзеров — на отдельной странице
        {adminItemId ? (
          <Link
            to={`/admin/${adminItemId}`}
            className="ml-1 text-accent underline inline-flex items-center gap-0.5"
          >
            Полное управление <ExternalLink className="w-3 h-3" />
          </Link>
        ) : (
          <span className="ml-1 italic">
            (нет отдельной admin-страницы для {serviceName})
          </span>
        )}
        .
      </div>
      <label className="flex items-center gap-2 text-xs mb-2">
        <span className="text-dim">dept</span>
        <select
          className="input flex-1"
          value={deptId ?? ""}
          onChange={(e) => setDeptId(e.target.value || null)}
          disabled={
            deptsQ.loading ||
            (deptsQ.data?.length ?? 0) === 0 ||
            // dep_admin зафиксирован на собственном отделе
            (depAdmin && !platformAdmin)
          }
        >
          {(deptsQ.data ?? [])
            .filter((d) =>
              platformAdmin || !depAdmin ? true : d.id === myDept,
            )
            .map((d) => (
              <option key={d.id} value={d.id}>
                {d.display_name} ({d.id})
              </option>
            ))}
          {(deptsQ.data ?? []).length === 0 && (
            <option value="">— нет отделов —</option>
          )}
        </select>
      </label>
      {deptId && (
        <ServiceRolesInline
          departmentId={deptId}
          serviceName={serviceName}
          canEdit={canEdit}
          compact
        />
      )}
    </div>
  );
}

function ServiceForm({
  mockMode,
  onDone,
}: {
  mockMode: boolean;
  onDone: () => void;
}) {
  const { close } = useInlineState();
  const toast = useToast();
  const [serviceName, setServiceName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!serviceName.trim() || !displayName.trim()) {
      toast.warn("service_name и display_name обязательны");
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
      const body: ServiceCreateRequest = {
        service_name: serviceName.trim(),
        display_name: displayName.trim(),
        description: description.trim() || undefined,
      };
      await createService(body);
      toast.success("Сервис зарегистрирован");
      onDone();
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Layers className="w-4 h-4 text-accent" />
        Новый сервис
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow
          label="service_name"
          hint="идентификатор · slug · неизменяемо после создания"
        >
          <input
            className="input mono"
            value={serviceName}
            onChange={(e) => setServiceName(e.target.value)}
            placeholder="config_service"
          />
        </FormRow>
        <FormRow label="display_name">
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </FormRow>
        <FormRow label="description">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
      </div>
      {err && <div className="alert-danger mt-3 text-xs">{err}</div>}
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={close} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || !serviceName.trim() || !displayName.trim()}
        >
          {busy ? "..." : "Создать"}
        </button>
      </div>
    </div>
  );
}
