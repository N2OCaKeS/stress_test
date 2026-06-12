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
  Edit3,
} from "lucide-react";
import { Link } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin, isDepAdmin, personaDeptId } from "@/lib/rbac";
import { useLabelsInvalidate } from "@/lib/labels";
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
import { apiErrMsg } from "@/api/client";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import type { Department, Service } from "@/api/auth/types";

type UiDept = {
  id: string;
  name: string;
  description?: string;
  user_count?: number;
};

export function ServicesDepartments() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const invalidateLabels = useLabelsInvalidate();
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
        description: d.description,
        user_count: d.user_count,
      }));
    }
    const users = usersQ.data?.items ?? [];
    return (deptsQ.data ?? []).map((d) => ({
      id: d.id,
      name: d.name,
      description: d.description ?? undefined,
      user_count: users.filter((u) => u.department_id === d.id).length,
    }));
  }, [mockMode, deptsQ.data, usersQ.data]);

  return (
    <InlineEditor
      title="Отделы · auth_service"
      icon={Building2}
      hint="департменты — единица изоляции ресурсов"
      items={items}
      loading={!mockMode && deptsQ.loading}
      error={!mockMode && deptsQ.error ? deptsQ.error.message : null}
      onRetry={() => deptsQ.refetch()}
      getId={(d) => d.id}
      canEdit={canEdit}
      readonlyNote={
        !canEdit ? "Управление депами — только account_admin / dep_admin" : undefined
      }
      listHeader={
        !mockMode ? (
          <div className="flex flex-col gap-1">
            {/* Счётчик юзеров считается по странице юзеров с капом 200; если
                всего больше — бейджи могут недосчитывать. Честно сигналим. */}
            <TruncationNotice
              shown={usersQ.data?.items.length ?? 0}
              total={usersQ.data?.total ?? null}
            />
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
              <div className="text-sm truncate">{item.name}</div>
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
                usersQ.refetch();
                void invalidateLabels("depts");
                onClose();
              }}
              mode="edit"
            />
          );
        return (
          <DeptDetail
            dept={d}
            mockMode={mockMode}
            canEdit={detailCanEdit}
            onChanged={() => {
              deptsQ.refetch();
              usersQ.refetch();
              void invalidateLabels("depts");
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
                  void invalidateLabels("depts");
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

/**
 * Обёртка вокруг `DeptView`, которая держит общий `listDepartmentServices`
 * query для текущего отдела. И секция «Сервисы отдела», и «Роли сервисов в
 * этом отделе» используют один и тот же массив гранатов; после grant/revoke
 * сверху одного вызова `refetch()` хватает, чтобы обе секции синхронно
 * обновились без перезагрузки страницы.
 */
function DeptDetail({
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
  const grantedQ = useQuery<string[]>(
    () => listDepartmentServices(dept.id),
    [dept.id],
    { enabled: !mockMode },
  );
  return (
    <DeptView
      dept={dept}
      mockMode={mockMode}
      canEdit={canEdit}
      onChanged={onChanged}
      granted={grantedQ.data ?? []}
      grantedLoading={grantedQ.loading}
      grantedError={grantedQ.error}
      refetchGranted={grantedQ.refetch}
    />
  );
}

function DeptView({
  dept,
  mockMode,
  canEdit,
  onChanged,
  granted,
  grantedLoading,
  grantedError,
  refetchGranted,
}: {
  dept: UiDept;
  mockMode: boolean;
  canEdit: boolean;
  onChanged: () => void;
  granted: string[];
  grantedLoading: boolean;
  grantedError: Error | null;
  refetchGranted: () => void;
}) {
  const { close, startEdit } = useInlineState();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const users = mockMode
    ? MOCK_USERS.filter((u) => u.dept_id === dept.id).length
    : dept.user_count ?? 0;
  const hasUsers = users > 0;
  const deleteDisabled = busy || hasUsers;

  async function onDelete() {
    if (mockMode) {
      close();
      return;
    }
    if (hasUsers) return;
    const reason = window.prompt(
      "Hard-delete отдела. Укажи причину (Q3 reorg / closed / ...):",
    );
    if (!reason) return;
    if (
      !window.confirm(
        `Снести ${dept.name}? CASCADE уносит группы, ботов и oauth_clients депа.`,
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
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  // Backend бросает 422 USERS_REMAIN_IN_DEPT, если в отделе остались юзеры.
  // Распознаём и подсвечиваем понятным сообщением + ссылкой на список юзеров.
  const usersRemainErr =
    err && err.startsWith("USERS_REMAIN_IN_DEPT") ? err : null;
  const genericErr = err && !usersRemainErr ? err : null;

  return (
    <div className="card max-w-2xl">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <Building2 className="w-4 h-4 text-accent" /> {dept.name}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button
              className="btn flex items-center gap-1"
              onClick={() => startEdit(dept.id)}
            >
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={deleteDisabled}
              title={
                hasUsers
                  ? `В отделе ещё ${users} юзер(ов) — сначала перевести их в другой отдел`
                  : undefined
              }
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="dept_id" v={<span className="mono">{dept.id}</span>} />
      <StatRow k="name" v={dept.name} />
      <StatRow
        k="description"
        v={
          dept.description ? (
            dept.description
          ) : (
            <span className="text-dim italic">—</span>
          )
        }
      />
      <StatRow
        k="users"
        v={
          <span className={`mono ${hasUsers ? "text-warn" : ""}`}>{users}</span>
        }
      />
      {usersRemainErr && (
        <div className="alert-danger mt-3 flex flex-col gap-1">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              В отделе остались активные юзеры ({users}). Сначала переведи их в
              другой отдел или удали.
            </div>
          </div>
          <Link
            to={`/admin/services.users?dept_id=${encodeURIComponent(dept.id)}`}
            className="btn btn-ghost text-xs self-start flex items-center gap-1"
          >
            <ExternalLink className="w-3 h-3" /> Перейти к юзерам отдела
          </Link>
        </div>
      )}
      {genericErr && <div className="alert-danger mt-3">{genericErr}</div>}
      {!mockMode && canEdit && (
        <DeptServicesSection
          deptId={dept.id}
          granted={granted}
          grantedLoading={grantedLoading}
          grantedError={grantedError}
          refetchGranted={refetchGranted}
        />
      )}
      {!mockMode && (
        <DeptRolesNav
          deptId={dept.id}
          granted={granted}
          grantedLoading={grantedLoading}
          grantedError={grantedError}
        />
      )}
      <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-1">
        <AlertTriangle className="w-3 h-3 text-warn" />
        Удаление возможно только при users = 0 (422 USERS_REMAIN_IN_DEPT).
        Сначала перевести юзеров в другой отдел через user-edit или снести.
      </div>
    </div>
  );
}

// Источник истины по привязкам — `GET /departments/{id}/services` (поднимается
// в `DeptDetail` и передаётся сюда через props). После grant/revoke зовём
// `refetchGranted`, который синхронно обновит и эту секцию, и `DeptRolesNav`.
function DeptServicesSection({
  deptId,
  granted,
  grantedLoading,
  grantedError,
  refetchGranted,
}: {
  deptId: string;
  granted: string[];
  grantedLoading: boolean;
  grantedError: Error | null;
  refetchGranted: () => void;
}) {
  const servicesQ = useQuery<Service[]>(() => listServices(), []);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const [picker, setPicker] = useState<string>("");

  const grantedSet = useMemo(() => new Set(granted), [granted]);

  async function grant(serviceName: string) {
    if (busy[serviceName]) return;
    setBusy((b) => ({ ...b, [serviceName]: true }));
    setErr(null);
    try {
      await grantServiceAccess(deptId, serviceName);
      refetchGranted();
      setPicker("");
    } catch (e) {
      setErr(apiErrMsg(e));
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
      refetchGranted();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy((b) => ({ ...b, [serviceName]: false }));
    }
  }

  const services = servicesQ.data ?? [];
  const grantedServices = services.filter((s) => grantedSet.has(s.service_name));
  const available = services.filter((s) => !grantedSet.has(s.service_name));

  return (
    <div className="mt-4 pt-3 border-t border-token">
      <div className="text-sm font-semibold mb-2 flex items-center gap-2">
        Сервисы отдела
        {(servicesQ.loading || grantedLoading) && (
          <Loader2 className="w-3 h-3 animate-spin" />
        )}
      </div>
      {servicesQ.error && (
        <div className="alert-danger text-[11px] mb-2">{servicesQ.error.message}</div>
      )}
      {grantedError && (
        <div className="alert-danger text-[11px] mb-2">{grantedError.message}</div>
      )}
      {err && <div className="alert-danger text-[11px] mb-2">{err}</div>}
      {grantedServices.length > 0 && (
        <div className="flex flex-col gap-1 mb-2">
          {grantedServices.map((s) => {
            const isLoging = s.service_name === "loging_service";
            return (
              <div
                key={s.service_name}
                className="flex flex-col gap-0.5"
              >
                <div className="flex items-center justify-between gap-2 text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="mono truncate">{s.service_name}</span>
                    <span className="badge">granted</span>
                  </div>
                  <div className="flex gap-1">
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
                  </div>
                </div>
                {isLoging && (
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
      {grantedServices.length === 0 && !grantedLoading && (
        <div className="text-[11px] text-dim italic mb-2">
          Ни одного сервиса не привязано. Выбери ниже и нажми Grant.
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
                {s.service_name}
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
  const [description, setDescription] = useState(initial?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isEdit = mode === "edit";
  const dirty = isEdit
    ? name !== (initial?.name ?? "") ||
      description !== (initial?.description ?? "")
    : !!name;

  async function submit() {
    if (mockMode) {
      onDone();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (mode === "new") {
        await createDepartment({ name });
      } else if (initial) {
        // PATCH-семантика: шлём только поля, которые реально поменялись.
        // Backend бросает 422 EMPTY_UPDATE, если пусто — кнопка-submit
        // дополнительно гасится через `dirty`-флаг.
        const body: { name?: string; description?: string } = {};
        if (name !== (initial.name ?? "")) {
          body.name = name;
        }
        if (description !== (initial.description ?? "")) {
          body.description = description;
        }
        await updateDepartment(initial.id, body);
      }
      onDone();
    } catch (e) {
      setErr(apiErrMsg(e));
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
        <FormRow label="name" hint="человеческое имя отдела (уникально)">
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormRow>
        <FormRow label="description" hint="пояснение / контакты / организационный смысл">
          <textarea
            className="input"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
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
          disabled={busy || !name || !dirty}
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
function DeptRolesNav({
  deptId,
  granted,
  grantedLoading,
  grantedError,
}: {
  deptId: string;
  granted: string[];
  grantedLoading: boolean;
  grantedError: Error | null;
}) {
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
  const grantedSet = useMemo(() => new Set(granted), [granted]);
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
      {(servicesQ.loading || grantedLoading) && (
        <Loader2 className="w-3 h-3 animate-spin text-dim" aria-label="Loading" />
      )}
      {(servicesQ.error || grantedError) && (
        <div className="alert-danger text-[11px] mb-2">
          {(servicesQ.error ?? grantedError)!.message}
        </div>
      )}
      {services.length === 0 && !servicesQ.loading && !grantedLoading && (
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
