import { useMemo, useState } from "react";
import {
  Building2,
  Trash2,
  AlertTriangle,
  Loader2,
  Plus,
  X,
  ShieldCheck,
  ExternalLink,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { Link } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin, isDepAdmin, personaDeptId } from "@/lib/rbac";
import { DEPTS as MOCK_DEPTS, USERS as MOCK_USERS } from "@/mocks/auth";
import {
  InlineEditor,
  FormRow,
  LogingPlatformRoleBanner,
  StatRow,
  useInlineState,
} from "./_inline";
import { ServiceRolesInline } from "./_serviceRolesInline";
import {
  createDepartment,
  deleteDepartment,
  grantServiceAccess,
  listDepartmentServices,
  listDepartments,
  revokeServiceAccess,
  updateDepartment,
} from "@/api/auth/departments";
import { listServices } from "@/api/auth/services";
import { listUsers } from "@/api/auth/users";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import type { Department, Service } from "@/api/auth/types";

type UiDept = {
  id: string;
  name: string;
  display_name: string;
  description?: string;
  user_count?: number;
};

export function ServicesDepartments() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  // account_admin — full CRUD; dep_admin — CRUD внутри собственного отдела
  // (фактически только delete/edit того, что уже принадлежит ему; create
  // отдела backend всё равно отдаёт только account_admin).
  const platformAdmin = isPlatformWideAdmin(persona);
  const depAdmin = isDepAdmin(persona);
  const canEdit = platformAdmin || depAdmin;

  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  const usersQ = useQuery(
    () => listUsers({ limit: 200 }),
    [],
    { enabled: !mockMode },
  );

  const items: UiDept[] = useMemo(() => {
    if (mockMode) {
      return MOCK_DEPTS.map((d) => ({
        id: d.id,
        name: d.name,
        display_name: d.name,
        description: d.description,
        user_count: d.user_count,
      }));
    }
    const users = usersQ.data?.items ?? [];
    return (deptsQ.data ?? []).map((d) => ({
      id: d.id,
      name: d.name,
      display_name: d.display_name,
      user_count: users.filter((u) => u.department_id === d.id).length,
    }));
  }, [mockMode, deptsQ.data, usersQ.data]);

  return (
    <InlineEditor
      title="Отделы · auth_service"
      icon={Building2}
      hint="департменты — единица изоляции ресурсов"
      items={items}
      getId={(d) => d.id}
      canEdit={canEdit}
      readonlyNote={
        !canEdit ? "Управление депами — только account_admin / dep_admin" : undefined
      }
      listHeader={
        !mockMode ? (
          <div className="flex flex-col gap-1">
            {deptsQ.loading && <div className="spinner" aria-label="Loading" />}
            {deptsQ.error && (
              <div className="alert-danger text-[11px]">{deptsQ.error.message}</div>
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
            <Building2 className="w-4 h-4 text-accent" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{item.display_name ?? item.name}</div>
              <div className="text-[11px] text-dim truncate">
                {item.description ?? <span className="mono">{item.id}</span>}
              </div>
            </div>
            <span className="badge">{item.user_count ?? 0}</span>
          </div>
        </button>
      )}
      renderDetail={(d, { editing, onClose }) => {
        // dep_admin может что-то менять только в собственном отделе.
        const myDept = personaDeptId(persona);
        const detailCanEdit =
          platformAdmin || (depAdmin && myDept === d.id);
        if (editing)
          return (
            <DeptForm
              initial={d}
              mockMode={mockMode}
              onDone={() => {
                deptsQ.refetch();
                onClose();
              }}
              mode="edit"
            />
          );
        return (
          <DeptView
            dept={d}
            mockMode={mockMode}
            canEdit={detailCanEdit}
            onChanged={() => {
              deptsQ.refetch();
              usersQ.refetch();
            }}
          />
        );
      }}
      renderCreate={
        platformAdmin
          ? (onClose) => (
              <DeptForm
                mockMode={mockMode}
                onDone={() => {
                  deptsQ.refetch();
                  onClose();
                }}
                mode="new"
              />
            )
          : undefined
      }
    />
  );
}

function DeptView({
  dept,
  mockMode,
  canEdit,
  onChanged,
}: {
  dept: UiDept;
  mockMode: boolean;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const { close } = useInlineState();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const users = mockMode
    ? MOCK_USERS.filter((u) => u.dept_id === dept.id).length
    : dept.user_count ?? 0;

  async function onDelete() {
    if (mockMode) {
      close();
      return;
    }
    const reason = window.prompt(
      "Hard-delete отдела. Укажи причину (Q3 reorg / closed / ...):",
    );
    if (!reason) return;
    if (
      !window.confirm(
        `Снести ${dept.display_name}? CASCADE уносит группы, ботов и oauth_clients депа.`,
      )
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      await deleteDepartment(dept.id, { reason });
      onChanged();
      close();
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <Building2 className="w-4 h-4 text-accent" /> {dept.display_name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            {/* Кнопка "Edit" отдела скрыта: endpoint
                `PATCH /departments/{id}` (или PUT) отсутствует в auth_service
                (есть только create / delete / привязка services). Изменить
                display_name можно только через delete+create. */}
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={busy}
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="dept_id" v={<span className="mono">{dept.id}</span>} />
      <StatRow k="name" v={dept.name} />
      <StatRow k="display_name" v={dept.display_name} />
      <StatRow k="users" v={<span className="mono">{users}</span>} />
      {err && <div className="alert-danger mt-3">{err}</div>}
      {!mockMode && canEdit && <DeptServicesSection deptId={dept.id} />}
      {!mockMode && <DeptRolesNav deptId={dept.id} />}
      <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-1">
        <AlertTriangle className="w-3 h-3 text-warn" />
        Удаление возможно только при users = 0 (422 USERS_REMAIN_IN_DEPT).
        Сначала перевести юзеров в другой отдел через user-edit или снести.
      </div>
    </div>
  );
}

// auth_service выдаёт только POST/DELETE для dept↔service связки — GET-листинга
// нет, поэтому секция хранит «локально известный» статус: сервисы, которые
// админ привязал или отвязал в этой сессии. Источник «доступных» — общий
// `/services` каталог.
function DeptServicesSection({ deptId }: { deptId: string }) {
  const servicesQ = useQuery<Service[]>(() => listServices(), []);
  const [bound, setBound] = useState<Record<string, boolean | undefined>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const [picker, setPicker] = useState<string>("");

  async function grant(serviceName: string) {
    if (busy[serviceName]) return;
    setBusy((b) => ({ ...b, [serviceName]: true }));
    setErr(null);
    try {
      await grantServiceAccess(deptId, serviceName);
      setBound((m) => ({ ...m, [serviceName]: true }));
      setPicker("");
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e));
    } finally {
      setBusy((b) => ({ ...b, [serviceName]: false }));
    }
  }

  async function revoke(serviceName: string) {
    if (busy[serviceName]) return;
    if (!window.confirm(`Отозвать access ${serviceName} у отдела?`)) return;
    setBusy((b) => ({ ...b, [serviceName]: true }));
    setErr(null);
    try {
      await revokeServiceAccess(deptId, serviceName);
      setBound((m) => ({ ...m, [serviceName]: false }));
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e));
    } finally {
      setBusy((b) => ({ ...b, [serviceName]: false }));
    }
  }

  const services = servicesQ.data ?? [];
  const known = services.filter((s) => bound[s.service_name] !== undefined);
  const available = services.filter((s) => bound[s.service_name] === undefined);

  return (
    <div className="mt-4 pt-3 border-t border-token">
      <div className="text-sm font-semibold mb-2 flex items-center gap-2">
        Сервисы отдела
        {servicesQ.loading && <Loader2 className="w-3 h-3 animate-spin" />}
      </div>
      <div className="text-[11px] text-dim mb-2">
        auth_service не отдаёт текущий список привязок — показаны только сервисы,
        с которыми взаимодействовали в этой сессии. После grant/revoke статус
        отражается локально.
      </div>
      {servicesQ.error && (
        <div className="alert-danger text-[11px] mb-2">{servicesQ.error.message}</div>
      )}
      {err && <div className="alert-danger text-[11px] mb-2">{err}</div>}
      {known.length > 0 && (
        <div className="flex flex-col gap-1 mb-2">
          {known.map((s) => {
            const isBound = bound[s.service_name];
            const isLoging = s.service_name === "loging_service";
            return (
              <div
                key={s.service_name}
                className="flex flex-col gap-0.5"
              >
                <div className="flex items-center justify-between gap-2 text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="mono truncate">{s.service_name}</span>
                    <span className="badge">{isBound ? "granted" : "revoked"}</span>
                  </div>
                  <div className="flex gap-1">
                    {isBound ? (
                      <button
                        className="btn btn-ghost text-xs flex items-center gap-1"
                        onClick={() => revoke(s.service_name)}
                        disabled={busy[s.service_name]}
                      >
                        {busy[s.service_name] ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <X className="w-3 h-3" />
                        )}
                        revoke
                      </button>
                    ) : (
                      <button
                        className="btn btn-ghost text-xs flex items-center gap-1"
                        onClick={() => grant(s.service_name)}
                        disabled={busy[s.service_name]}
                      >
                        {busy[s.service_name] ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <Plus className="w-3 h-3" />
                        )}
                        grant
                      </button>
                    )}
                  </div>
                </div>
                {isLoging && isBound && (
                  <div className="text-[11px] text-warn flex items-start gap-1 pl-1">
                    <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                    <span>
                      Грант не даёт регулярным юзерам отдела чтения аудита —
                      guard <span className="mono">loging_service</span> ждёт{" "}
                      <span className="mono">platform_role</span>. Доступ
                      выдаётся явно:{" "}
                      <span className="mono">loging_reader</span> или{" "}
                      <span className="mono">loging_admin</span>.
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="flex items-end gap-2">
        <label className="flex flex-col gap-1 text-sm flex-1">
          <span className="text-dim text-xs">service_name</span>
          <select
            className="input mono"
            value={picker}
            onChange={(e) => setPicker(e.target.value)}
          >
            <option value="">— выбрать сервис —</option>
            {available.map((s) => (
              <option key={s.service_name} value={s.service_name}>
                {s.service_name} · {s.display_name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn flex items-center gap-1"
          onClick={() => picker && grant(picker)}
          disabled={!picker || !!busy[picker]}
        >
          {picker && busy[picker] ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Plus className="w-4 h-4" />
          )}
          Grant
        </button>
      </div>
    </div>
  );
}

function DeptForm({
  initial,
  mockMode,
  onDone,
  mode,
}: {
  initial?: UiDept;
  mockMode: boolean;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (mockMode) {
      onDone();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (mode === "new") {
        await createDepartment({ name, display_name: displayName });
      } else if (initial) {
        const body: { name?: string; display_name?: string } = {};
        if (name !== initial.name) body.name = name;
        if (displayName !== initial.display_name) body.display_name = displayName;
        await updateDepartment(initial.id, body);
      }
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <Building2 className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый отдел" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name" hint="внутренний идентификатор (slug)">
          <input
            className="input mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={mode === "edit"}
          />
        </FormRow>
        <FormRow label="display_name">
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </FormRow>
      </div>
      {err && <div className="alert-danger mt-3">{err}</div>}
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || !name || !displayName}
        >
          {busy ? "..." : mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

// Backend service_name → admin item route. Идентификатор в каталоге admin —
// `services.<service_name>.roles` (см. `buildAdminItems` в adminCatalog.ts).
// auth_service пропускаем — для него отдельной страницы «ролей сервиса» нет.
function serviceToAdminItem(serviceName: string): string | undefined {
  if (serviceName === "auth_service") return undefined;
  return `services.${serviceName}.roles`;
}

/**
 * Под каждым сервисом из каталога — expandable inline-CRUD ролей в текущем
 * отделе. Плюс ссылка на полное управление (bulk-assign / revoke юзеров)
 * на странице сервиса. dept-id попадает в URL как `?dept_id=<id>` — у
 * целевого экрана это hint, не жёсткий фильтр.
 */
function DeptRolesNav({ deptId }: { deptId: string }) {
  const { persona } = usePersona();
  const platformAdmin = isPlatformWideAdmin(persona);
  const depAdmin = isDepAdmin(persona);
  const myDept = personaDeptId(persona);
  const canEdit =
    platformAdmin || (depAdmin && myDept === deptId);

  const servicesQ = useQuery<Service[]>(() => listServices(), []);
  // Filter to services that actually have an active grant for this dept.
  // Without the filter, dep_admin sees the full platform catalog and can try
  // creating roles for services the dept can't even use (backend would
  // accept the role row but it'd be wasted).
  const grantedQ = useQuery<string[]>(
    () => listDepartmentServices(deptId),
    [deptId],
  );
  const grantedSet = useMemo(
    () => new Set(grantedQ.data ?? []),
    [grantedQ.data],
  );
  const services = (servicesQ.data ?? []).filter((s) =>
    grantedSet.has(s.service_name),
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  function toggle(name: string) {
    setExpanded((m) => ({ ...m, [name]: !m[name] }));
  }

  return (
    <div className="mt-4 pt-3 border-t border-token">
      <div className="text-sm font-semibold mb-2 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        Роли сервисов в этом отделе
      </div>
      <div className="text-[11px] text-dim mb-2">
        Показаны только сервисы с активным grant'ом. Чтобы добавить новый
        сервис — выдай grant в «Сервисы отдела» выше.
      </div>
      {(servicesQ.loading || grantedQ.loading) && (
        <Loader2 className="w-3 h-3 animate-spin text-dim" aria-label="Loading" />
      )}
      {(servicesQ.error || grantedQ.error) && (
        <div className="alert-danger text-[11px] mb-2">
          {(servicesQ.error ?? grantedQ.error)!.message}
        </div>
      )}
      {services.length === 0 && !servicesQ.loading && !grantedQ.loading && (
        <div className="text-[11px] text-dim italic">
          Ни одного гранта нет — добавь сервис в секцию выше.
        </div>
      )}
      <div className="flex flex-col gap-1">
        {services.map((s) => {
          const adminItem = serviceToAdminItem(s.service_name);
          // auth_service не имеет dept-scoped ролей — только показываем строку.
          const isAuth = s.service_name === "auth_service";
          // loging_service гейтится platform_role'ами; service-роли в этой
          // связке guard'ами игнорируются — expand отключаем, banner вместо.
          const isLoging = s.service_name === "loging_service";
          const expandable = !isAuth && !isLoging;
          const isOpen = !!expanded[s.service_name];
          return (
            <div
              key={s.service_name}
              className="border-b border-dashed border-token last:border-b-0"
            >
              <div className="flex items-center gap-2 text-sm py-1">
                {expandable ? (
                  <button
                    className="btn btn-ghost text-xs flex items-center gap-1 px-1"
                    onClick={() => toggle(s.service_name)}
                    aria-expanded={isOpen}
                    aria-label={isOpen ? "свернуть" : "раскрыть"}
                  >
                    {isOpen ? (
                      <ChevronDown className="w-3 h-3" />
                    ) : (
                      <ChevronRight className="w-3 h-3" />
                    )}
                  </button>
                ) : (
                  <span className="w-5" />
                )}
                <span className="mono truncate flex-1">{s.service_name}</span>
                {adminItem && !isLoging ? (
                  <Link
                    to={`/admin/${adminItem}?dept_id=${encodeURIComponent(deptId)}`}
                    className="btn btn-ghost text-xs flex items-center gap-1"
                  >
                    <ExternalLink className="w-3 h-3" /> Полное управление
                  </Link>
                ) : adminItem && isLoging ? (
                  <span className="text-[11px] text-dim italic">
                    platform_role only
                  </span>
                ) : (
                  <span className="text-[11px] text-dim italic">
                    нет admin-страницы
                  </span>
                )}
              </div>
              {isLoging && (
                <div className="pl-6 pb-2">
                  <LogingPlatformRoleBanner compact />
                </div>
              )}
              {expandable && isOpen && (
                <div className="pl-6 pb-2">
                  <ServiceRolesInline
                    departmentId={deptId}
                    serviceName={s.service_name}
                    canEdit={canEdit}
                    compact
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
