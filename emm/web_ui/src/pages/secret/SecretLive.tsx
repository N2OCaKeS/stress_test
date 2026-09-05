/**
 * Live-страница /secret — Shell + Aside (список кред) + Workzone (detail).
 *
 * Тянет `secret_service` напрямую через `@/api/secret/*`. Покрывает
 * минимальный рабочий цикл: list (cursor) + create + reveal + delete +
 * recover, плюс read-only просмотр ACL / dept-grants на detail-панели.
 *
 * Mock-режим (`VITE_USE_MOCK_AUTH=true`) обслуживают SecretAccountAdmin /
 * SecretDepAdmin — диспетчеризация в Secret.tsx.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  Key,
  Plus,
  ArrowLeft,
  AlertCircle,
  Eye,
  EyeOff,
  Copy,
  Trash2,
  RotateCcw,
  Pencil,
  ArrowRightLeft,
  ShieldPlus,
  X,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { fromBase64 } from "@/lib/base64";
import { formatMsk } from "@/lib/datetime";
import { useLabelMaps, useUserLabel } from "@/lib/labels";
import { useQuery } from "@/api/auth/useQuery";
import { isSecretZoneBlocked } from "@/lib/rbac";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  createCredential,
  deleteCredential,
  getCredential,
  listCredentials,
  recoverCredential,
  revealCredential,
  transferCredential,
  updateCredential,
} from "@/api/secret/credentials";
import { addRoleAcl, listRoleAcls, revokeRoleAcl } from "@/api/secret/roleAcls";
import {
  addUserAcl,
  listUserAcls,
  revokeUserAcl,
} from "@/api/secret/userAcls";
import { listUsersByDepartment, resolveUser } from "@/api/auth/users";
import { listServiceRoles } from "@/api/auth/service_roles";
import type {
  Credential,
  CredentialCreateRequest,
  CredentialScope,
  CredentialUpdateRequest,
  TransferRequest,
} from "@/api/secret/types";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

const SCOPE_LABEL: Record<CredentialScope, string> = {
  personal: "personal",
  department: "department",
  cross_department: "cross-dept",
};

const STATUS_KIND: Record<string, "ok" | "danger"> = {
  active: "ok",
  blocked: "danger",
};

// Поля окна валидности оператор вводит в московском времени (MSK, UTC+3 без
// перехода на летнее), а backend хранит и сравнивает в UTC. Раньше naive-строку
// из datetime-local отправляли как UTC (`:00Z`) — введённое «сейчас» по MSK
// уезжало на +3 часа в будущее, и свежий секрет ловил SECRET_NOT_YET_VALID.
const MSK_OFFSET = "+03:00";

/** MSK-строка `YYYY-MM-DDTHH:mm` из datetime-local → UTC ISO для backend. */
function localToIso(local: string): string {
  return local.length === 16
    ? new Date(`${local}:00${MSK_OFFSET}`).toISOString()
    : local;
}

/** UTC ISO от backend → значение для `datetime-local` в MSK (`YYYY-MM-DDTHH:mm`). */
function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const msk = new Date(new Date(iso).getTime() + 3 * 60 * 60 * 1000);
  return msk.toISOString().slice(0, 16);
}

export function SecretLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const { prompt } = useConfirm();
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");
  const action = params.get("action"); // "new" | null

  // account_admin / logging_* без департамента отрезаны от secret_service на
  // уровне backend (SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT) — даже список кред
  // вернёт 403. Не дёргаем API и показываем объяснение вместо мёртвой страницы.
  const zoneBlocked = isSecretZoneBlocked(persona);

  const [search, setSearch] = useState("");
  const [filterScope, setFilterScope] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");

  const PAGE_SIZE = 50;
  const listQ = useQuery(
    () =>
      listCredentials({
        limit: PAGE_SIZE,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
      }),
    [filterScope, filterStatus],
    { enabled: !zoneBlocked },
  );

  // Аккумулятор для cursor «load more»: первая страница приходит из listQ,
  // последующие — дозагружаем по next_cursor и добавляем сюда. Сбрасываем,
  // когда меняются фильтры (новый набор данных).
  const [extraItems, setExtraItems] = useState<
    Array<Credential & { status?: string }>
  >([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setExtraItems([]);
    setCursor(listQ.data?.next_cursor ?? null);
  }, [listQ.data]);

  // account_admin не привязан к департаменту, а secret_service — dept-scoped:
  // бэкенд отбивает любые операции с кредами 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT.
  // Поэтому платформенный админ не получает кнопки управления.
  const canManage =
    persona.platform_role === "dep_admin" ||
    persona.service_roles.secret === "admin" ||
    persona.service_roles.secret === "operator";

  // Создание personal-секрета backend разрешает любому dept-context user'у:
  // scope=personal → owner = текущий пользователь. Платформенные роли без
  // департамента сюда не доходят (zoneBlocked отбивает страницу выше), так что
  // любой, кто видит этот экран, вправе завести себе хотя бы personal-креду.
  // dep/cross-scope в форме остаётся за canManage.
  const canCreate = !zoneBlocked;

  // Список может прийти как полный (CredentialList) или guest-урезанный
  // (CredentialGuestList). Поля name/id/service/scope есть в обоих shape'ах —
  // их и показываем в aside. Guest-shape не несёт owner_user_id/status —
  // по их отсутствию и распознаём read-only режим без reveal-доступа.
  const rawItems = listQ.data?.items ?? [];
  const firstRow = rawItems[0] as (Credential & { status?: string }) | undefined;
  const isGuestList =
    rawItems.length > 0 && firstRow !== undefined && !("owner_user_id" in firstRow);
  const items = [
    ...(rawItems as Array<Credential & { status?: string }>),
    ...extraItems,
  ];

  async function handleLoadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listCredentials({
        limit: PAGE_SIZE,
        scope: filterScope || undefined,
        status: filterStatus || undefined,
        cursor,
      });
      setExtraItems((prev) => [
        ...prev,
        ...(page.items as Array<Credential & { status?: string }>),
      ]);
      setCursor(page.next_cursor ?? null);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось догрузить список"));
    } finally {
      setLoadingMore(false);
    }
  }

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return items;
    return items.filter(
      (c) =>
        c.name.toLowerCase().includes(term) ||
        c.service.toLowerCase().includes(term) ||
        c.id.toLowerCase().includes(term),
    );
  }, [items, search]);

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function startCreate() {
    const next = new URLSearchParams(params);
    next.set("action", "new");
    next.delete("id");
    setParams(next, { replace: true });
  }
  function closeAction() {
    const next = new URLSearchParams(params);
    next.delete("action");
    setParams(next, { replace: true });
  }

  async function handleCreate(body: CredentialCreateRequest) {
    try {
      const created = await createCredential(body);
      toast.success(`Учётные данные ${created.name} созданы`);
      listQ.refetch();
      const next = new URLSearchParams(params);
      next.set("id", created.id);
      next.delete("action");
      setParams(next, { replace: true });
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание не удалось"));
    }
  }

  async function handleDelete(cred: Credential) {
    const { ok, reason } = await prompt({
      title: "Удалить учётные данные",
      message: `Удалить учётные данные ${cred.name}? Операция необратима.`,
      reason: true,
      reasonLabel: "Причина (обязательна для admin-override)",
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteCredential(cred.id, {
        reason: reason.trim() || undefined,
      });
      toast.success(`Учётные данные ${cred.name} удалены`);
      selectId(null);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${items.length} учётным данным…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-dim">
          <Dropdown
            mode="single"
            options={[
              { value: "personal", label: "personal" },
              { value: "department", label: "department" },
              { value: "cross_department", label: "cross_department" },
            ]}
            value={filterScope}
            onChange={setFilterScope}
            placeholder="все области"
          />
          <Dropdown
            mode="single"
            options={[
              { value: "active", label: "active" },
              { value: "blocked", label: "blocked" },
            ]}
            value={filterStatus}
            onChange={setFilterStatus}
            placeholder="все статусы"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {listQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {listQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(listQ.error, "Список не загрузился")}</div>
              <Button variant="ghost"
                className="mt-2"
                onClick={() => listQ.refetch()}
              >
                Повторить
              </Button>
            </div>
          </div>
        )}
        {!listQ.loading && !listQ.error && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Список пуст.
          </div>
        )}
        <div className="px-2 flex flex-col gap-0.5">
          {filtered.map((c) => (
            <CredRow
              key={c.id}
              cred={c}
              active={selectedId === c.id}
              onSelect={() => selectId(c.id)}
            />
          ))}
        </div>
        {!listQ.loading && !listQ.error && cursor && !search.trim() && (
          <div className="px-3 pt-2">
            <Button variant="ghost"
              className="w-full text-xs"
              onClick={handleLoadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Загрузка…" : "Загрузить ещё"}
            </Button>
          </div>
        )}
      </div>

      {canCreate && (
        <div className="border-t border-token p-3 shrink-0">
          <Button variant="primary"
            className="w-full flex items-center justify-center gap-2"
            onClick={startCreate}
          >
            <Plus className="w-4 h-4" /> Создать учётные данные
          </Button>
        </div>
      )}
    </aside>
  );

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="secret_service / credentials">
        <BlockedPane />
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="secret_service / credentials" middle={aside}>
      {action === "new" && canCreate ? (
        <CreatePane
          defaultDeptId={persona.dept_id}
          canManage={canManage}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedId ? (
        <DetailPane
          credId={selectedId}
          canManage={canManage}
          isGuest={isGuestList}
          currentUserId={persona.id}
          canPickUsers={
            persona.platform_role === "dep_admin" ||
            persona.platform_role === "account_admin"
          }
          actorDeptId={persona.dept_id}
          onDelete={handleDelete}
          onChanged={() => listQ.refetch()}
        />
      ) : (
        <EmptyPane canCreate={canCreate} onCreate={startCreate} />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function BlockedPane() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
        <div className="text-sm font-medium mb-2">
          Раздел недоступен для платформенной роли
        </div>
        <div className="text-xs text-dim">
          secret_service — хранилище в рамках департамента. Учётка{" "}
          <b>account_admin</b> / <b>logging_admin</b> / <b>logging_reader</b> не
          привязана к департаменту и не имеет доступа к кредам — работайте под
          департаментной ролью (dep_admin или secret.*).
        </div>
      </div>
    </section>
  );
}

function CredRow({
  cred,
  active,
  onSelect,
}: {
  cred: Credential & { status?: string };
  active: boolean;
  onSelect: () => void;
}) {
  const statusKind = cred.status ? STATUS_KIND[cred.status] : undefined;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        <Key className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">{cred.name}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="mono truncate">{cred.service}</span>
            <span>·</span>
            <span>{SCOPE_LABEL[cred.scope] ?? cred.scope}</span>
          </div>
        </div>
        {cred.status && (
          <Badge kind={statusKind ?? "neutral"}>
            {cred.status}
          </Badge>
        )}
      </div>
    </button>
  );
}

function EmptyPane({
  canCreate,
  onCreate,
}: {
  canCreate: boolean;
  onCreate: () => void;
}) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <Key className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim mb-3">
          Выберите учётные данные слева для просмотра деталей.
        </div>
        {canCreate && (
          <Button variant="primary"
            className="inline-flex items-center gap-1"
            onClick={onCreate}
          >
            <Plus className="w-4 h-4" /> Создать учётные данные
          </Button>
        )}
      </div>
    </section>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function DetailPane({
  credId,
  canManage,
  isGuest,
  currentUserId,
  canPickUsers,
  actorDeptId,
  onDelete,
  onChanged,
}: {
  credId: string;
  canManage: boolean;
  isGuest: boolean;
  currentUserId: string;
  canPickUsers: boolean;
  actorDeptId: string | null;
  onDelete: (c: Credential) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const { depts } = useLabelMaps();
  const credQ = useQuery(() => getCredential(credId), [credId]);
  const cred = credQ.data;
  const ownerUserName = useUserLabel(cred?.owner_user_id);
  const createdByName = useUserLabel(cred?.created_by);

  const isPersonal = cred?.scope === "personal";
  // Роли и департаментные гранты для department/cross_department настраиваются
  // в разделе администрирования. На карточке остаётся только владельческий
  // шеринг личного секрета: RoleACL на свою креду и доступ конкретным
  // пользователям. Оба признака поэтому гейтятся личным владением.
  const canManageUserAcl =
    isPersonal && cred?.owner_user_id === currentUserId;
  const canManageAcl =
    isPersonal && (canManage || cred?.owner_user_id === currentUserId);
  // Guest без reveal-доступа не нагружаем ACL-листингами (бэк всё равно отобьёт
  // 403), показываем только метаданные. RoleACL/UserACL запрашиваем лишь на
  // личных кредах — управление правами dept/cross живёт в администрировании.
  const aclQ = useQuery(() => listRoleAcls(credId), [credId], {
    enabled: !isGuest && isPersonal,
  });
  const userAclQ = useQuery(() => listUserAcls(credId), [credId], {
    enabled: !isGuest && canManageUserAcl,
  });

  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  // 429-countdown: per-IP rate-limit или actor-lockout кладёт
  // retry_after_seconds; гасим Reveal на это окно и тикаем секундами для UX.
  // (5-минутный reveal-throttle сам по себе не 429 — он лишь меняет severity
  // audit-события, секрет всё равно отдаётся.)
  const [throttleUntil, setThrottleUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // Модалки управления.
  const [editing, setEditing] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [addingAcl, setAddingAcl] = useState(false);
  const [addingUserAcl, setAddingUserAcl] = useState(false);

  // Общий гейт для прямых мутаций detail-панели (recover / delete / снятие
  // ACL и grant) — блокирует двойной клик, пока запрос в полёте.
  const [acting, setActing] = useState(false);

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);

  // Сбрасываем reveal/throttle при переходе на другую креду.
  useEffect(() => {
    setRevealed(null);
    setThrottleUntil(0);
  }, [credId]);

  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));

  async function handleReveal() {
    if (revealing || throttleLeft > 0) return;
    setRevealing(true);
    try {
      const res = await revealCredential(credId);
      // secret_b64 — base64(plaintext); декодируем для показа.
      let plain = res.secret_b64;
      try {
        plain = fromBase64(res.secret_b64);
      } catch {
        // если не валидный base64 — показываем как есть
      }
      setRevealed(plain);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 300;
        setThrottleUntil(Date.now() + secs * 1000);
        setNow(Date.now());
        toast.error(`Слишком часто, повторите через ${secs} сек`);
      } else {
        toast.error(apiErrMsg(e, "Показ не удался"));
      }
    } finally {
      setRevealing(false);
    }
  }

  async function handleRecover() {
    if (acting) return;
    setActing(true);
    try {
      await recoverCredential(credId);
      toast.success("Учётные данные разблокированы");
      setRevealed(null);
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Разблокировка не удалась"));
    } finally {
      setActing(false);
    }
  }

  async function handleEditSubmit(body: CredentialUpdateRequest) {
    try {
      await updateCredential(credId, body);
      toast.success("Учётные данные обновлены");
      setEditing(false);
      setRevealed(null);
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление не удалось"));
    }
  }

  async function handleTransferSubmit(body: TransferRequest) {
    try {
      await transferCredential(credId, body);
      toast.success("Владение передано, учётные данные разблокированы");
      setTransferring(false);
      // Владелец сменился — раскрытый plaintext больше не должен висеть
      // на карточке, маскируем обратно.
      setRevealed(null);
      credQ.refetch();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Передача не удалась"));
    }
  }

  async function handleAclAdd(body: {
    dept_id: string;
    role_name: string;
    can_read: boolean;
    can_write: boolean;
  }) {
    try {
      await addRoleAcl(credId, body);
      toast.success("RoleACL выдан");
      setAddingAcl(false);
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Выдача ACL не удалась"));
    }
  }

  async function handleAclRevoke(aclId: string) {
    if (acting) return;
    if (!(await confirm({ message: "Снять этот RoleACL?", danger: true })))
      return;
    setActing(true);
    try {
      await revokeRoleAcl(credId, aclId);
      toast.success("RoleACL снят");
      aclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие ACL не удалось"));
    } finally {
      setActing(false);
    }
  }

  async function handleUserAclAdd(body: {
    user_id: string;
    can_read: boolean;
    can_write: boolean;
  }) {
    try {
      await addUserAcl(credId, body);
      toast.success("Доступ выдан");
      setAddingUserAcl(false);
      userAclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Выдача доступа не удалась"));
    }
  }

  async function handleUserAclRevoke(aclId: string) {
    if (acting) return;
    if (
      !(await confirm({
        message: "Снять доступ этого пользователя?",
        danger: true,
      }))
    )
      return;
    setActing(true);
    try {
      await revokeUserAcl(credId, aclId);
      toast.success("Доступ снят");
      userAclQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Снятие доступа не удалось"));
    } finally {
      setActing(false);
    }
  }

  async function handleCopy() {
    if (!revealed || typeof navigator === "undefined" || !navigator.clipboard)
      return;
    try {
      await navigator.clipboard.writeText(revealed);
      toast.success("Скопировано");
    } catch {
      toast.warn("Не удалось скопировать");
    }
  }

  if (credQ.loading) {
    return (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="text-sm text-dim">Загрузка…</div>
      </section>
    );
  }
  if (credQ.error || !cred) {
    return (
      <section className="flex-1 min-w-0 flex items-center justify-center">
        <div className="alert alert-danger max-w-md flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(credQ.error, "Карточка не загрузилась")}</div>
            <Button variant="ghost"
              className="mt-2"
              onClick={() => credQ.refetch()}
            >
              Повторить
            </Button>
          </div>
        </div>
      </section>
    );
  }

  const blocked = cred.status === "blocked";

  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center text-2xl">
          🔑
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate">{cred.name}</h1>
            <Badge kind={blocked ? "danger" : "ok"}>
              {cred.status}
            </Badge>
            <span className="text-xs text-dim">
              область: <b>{SCOPE_LABEL[cred.scope] ?? cred.scope}</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{cred.id}</span>
            <span>·</span>
            <span>
              сервис: <b>{cred.service}</b>
            </span>
          </div>
        </div>
        {canManage && (
          <div className="flex items-center gap-2 shrink-0">
            {!blocked && (
              <Button
                onClick={() => setEditing(true)}
                disabled={acting}
                title="Изменить"
              >
                <Pencil className="w-4 h-4 inline-block" /> Изменить
              </Button>
            )}
            {blocked && (
              <>
                <Button
                  onClick={handleRecover}
                  disabled={acting}
                  title="Разблокировать"
                >
                  <RotateCcw className="w-4 h-4 inline-block" /> Разблокировать
                </Button>
                <Button
                  onClick={() => setTransferring(true)}
                  disabled={acting}
                  title="Передать владение"
                >
                  <ArrowRightLeft className="w-4 h-4 inline-block" /> Передать
                </Button>
              </>
            )}
            <Button variant="danger"
              onClick={() => onDelete(cred)}
              disabled={acting}
              title="Удалить"
            >
              <Trash2 className="w-4 h-4 inline-block" /> Удалить
            </Button>
          </div>
        )}
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        {/* Login — нешифруемые метаданные, показываем открыто над секретом */}
        {cred.login && (
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs uppercase tracking-wider text-dim">
                Логин
              </div>
              <Button
                onClick={() => navigator.clipboard?.writeText(cred.login ?? "")}
                title="Скопировать логин"
              >
                <Copy className="w-4 h-4 inline-block" />
              </Button>
            </div>
            <div className="mono text-lg p-3 surface-2 rounded border border-token">
              {cred.login}
            </div>
          </div>
        )}

        {/* Secret value */}
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wider text-dim">
              Значение
            </div>
            {isGuest ? (
              <span className="text-[11px] text-dim">
                guest: показ недоступен
              </span>
            ) : (
              <div className="flex items-center gap-2">
                {throttleLeft > 0 && (
                  <span className="text-[11px] text-warn">
                    лимит: {throttleLeft}с
                  </span>
                )}
                {revealed !== null ? (
                  <Button
                    onClick={() => setRevealed(null)}
                    title="Скрыть значение"
                  >
                    <EyeOff className="w-4 h-4 inline-block" />{" "}
                    <span>Скрыть</span>
                  </Button>
                ) : (
                  <Button
                    onClick={handleReveal}
                    disabled={revealing || blocked || throttleLeft > 0}
                  >
                    <Eye className="w-4 h-4 inline-block" />{" "}
                    <span>
                      {revealing
                        ? "…"
                        : throttleLeft > 0
                        ? `${throttleLeft}с`
                        : "Показать"}
                    </span>
                  </Button>
                )}
                <Button
                  onClick={handleCopy}
                  disabled={!revealed}
                >
                  <Copy className="w-4 h-4 inline-block" />
                </Button>
              </div>
            )}
          </div>
          <div
            className={`mono text-lg p-3 surface-2 rounded border border-token ${
              revealed === null ? "secret-mask" : ""
            }`}
          >
            {revealed ?? "••••••••••••••••••••••••••"}
          </div>
          {isGuest && (
            <div className="text-xs text-dim mt-2">
              Guest видит метаданные; показ закрыт — запросите доступ у dep_admin.
            </div>
          )}
        </div>

        {/* Meta */}
        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3">
            Метаданные
          </div>
          <div className="text-sm">
            <MetaRow
              label="Отдел-владелец"
              value={
                cred.owner_dept_id
                  ? depts.get(cred.owner_dept_id) ?? cred.owner_dept_id
                  : "—"
              }
            />
            <MetaRow
              label="Владелец-пользователь"
              value={cred.owner_user_id ? ownerUserName : "—"}
              title={cred.owner_user_id ?? undefined}
            />
            <MetaRow
              label="Кем создан"
              value={createdByName}
              title={cred.created_by}
            />
            <MetaRow label="Создан" value={formatMsk(cred.created_at)} />
            <MetaRow label="Обновлён" value={formatMsk(cred.updated_at)} />
            <MetaRow label="visible_to_dept" value={String(cred.visible_to_dept)} />
            <MetaRow label="valid_from" value={formatMsk(cred.valid_from)} />
            <MetaRow label="valid_to" value={formatMsk(cred.valid_to)} />
            {blocked && (
              <>
                <MetaRow label="blocked_at" value={formatMsk(cred.blocked_at)} />
                <MetaRow
                  label="blocked_reason"
                  value={cred.blocked_reason ?? "—"}
                />
              </>
            )}
          </div>
        </div>

        {/* Access — RoleACL (только личный секрет; dept/cross — в админке) */}
        {!isGuest && isPersonal && (
          <div className="surface border border-token rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim">
                RoleACL ({aclQ.data?.items.length ?? 0})
              </div>
              {canManageAcl && (
                <Button variant="ghost"
                  className="text-xs flex items-center gap-1"
                  onClick={() => setAddingAcl(true)}
                >
                  <ShieldPlus className="w-3.5 h-3.5" /> Выдать
                </Button>
              )}
            </div>
            {aclQ.loading && <div className="text-xs text-dim">Загрузка…</div>}
            {aclQ.error && (
              <div className="text-xs text-danger">
                {apiErrMsg(aclQ.error, "ACL не загрузился")}
              </div>
            )}
            {!aclQ.loading && !aclQ.error && (
              <div className="text-sm flex flex-col gap-1">
                {(aclQ.data?.items ?? []).length === 0 && (
                  <div className="text-xs text-dim">Нет выданных ACL.</div>
                )}
                {(aclQ.data?.items ?? []).map((a) => (
                  <div key={a.id} className="stat-row items-center">
                    <span className="text-dim">
                      {depts.get(a.dept_id) ?? a.dept_id} / {a.role_name}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="mono text-xs">
                        {a.can_read ? "r" : "-"}
                        {a.can_write ? "w" : "-"}
                      </span>
                      {canManageAcl && (
                        <Button variant="ghost"
                          className="p-1"
                          title="Снять ACL"
                          disabled={acting}
                          onClick={() => handleAclRevoke(a.id)}
                        >
                          <X className="w-3.5 h-3.5" />
                        </Button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Доступ пользователям — UserACL */}
        {!isGuest && canManageUserAcl && (
          <div className="surface border border-token rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim">
                Доступ пользователям ({userAclQ.data?.items.length ?? 0})
              </div>
              <Button variant="ghost"
                className="text-xs flex items-center gap-1"
                onClick={() => setAddingUserAcl(true)}
              >
                <ShieldPlus className="w-3.5 h-3.5" /> Выдать
              </Button>
            </div>
            {userAclQ.loading && (
              <div className="text-xs text-dim">Загрузка…</div>
            )}
            {userAclQ.error && (
              <div className="text-xs text-danger">
                {apiErrMsg(userAclQ.error, "Список доступа не загрузился")}
              </div>
            )}
            {!userAclQ.loading && !userAclQ.error && (
              <div className="text-sm flex flex-col gap-1">
                {(userAclQ.data?.items ?? []).length === 0 && (
                  <div className="text-xs text-dim">
                    Нет выданных доступов пользователям.
                  </div>
                )}
                {(userAclQ.data?.items ?? []).map((a) => (
                  <UserAclRow
                    key={a.id}
                    userId={a.user_id}
                    canRead={a.can_read}
                    canWrite={a.can_write}
                    acting={acting}
                    onRevoke={() => handleUserAclRevoke(a.id)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Права dept/cross-секрета настраиваются в администрировании */}
        {!isGuest && !isPersonal && (
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-1">
              Права доступа
            </div>
            <div className="text-xs text-dim">
              Роли, департаментные гранты и доступы пользователей к секретам
              отдела настраиваются в разделе администрирования.
            </div>
          </div>
        )}
      </div>

      {editing && cred && (
        <EditModal
          cred={cred}
          onClose={() => setEditing(false)}
          onSubmit={handleEditSubmit}
        />
      )}
      {transferring && cred && (
        <TransferModal
          scope={cred.scope}
          onClose={() => setTransferring(false)}
          onSubmit={handleTransferSubmit}
        />
      )}
      {addingAcl && (
        <AclModal
          isCross={false}
          actorDeptId={actorDeptId}
          onClose={() => setAddingAcl(false)}
          onSubmit={handleAclAdd}
        />
      )}
      {addingUserAcl && (
        <UserAclModal
          canPick={canPickUsers}
          pickerDeptId={cred?.owner_dept_id ?? null}
          resolveDeptId={actorDeptId}
          onClose={() => setAddingUserAcl(false)}
          onSubmit={handleUserAclAdd}
        />
      )}
    </section>
  );
}

function MetaRow({
  label,
  value,
  title,
}: {
  label: string;
  value: string;
  title?: string;
}) {
  return (
    <div className="stat-row">
      <span className="text-dim">{label}</span>
      <span
        className="mono text-xs truncate max-w-[60%]"
        title={title ?? undefined}
      >
        {value}
      </span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function CreatePane({
  defaultDeptId,
  canManage,
  onCancel,
  onSubmit,
}: {
  defaultDeptId: string | null;
  canManage: boolean;
  onCancel: () => void;
  onSubmit: (body: CredentialCreateRequest) => void | Promise<void>;
}) {
  const { depts } = useLabelMaps();
  const [name, setName] = useState("");
  const [service, setService] = useState("");
  const [scope, setScope] = useState<CredentialScope>("personal");
  const [login, setLogin] = useState("");
  const [secret, setSecret] = useState("");
  const [visibleToDept, setVisibleToDept] = useState(false);
  // valid_from по умолчанию — текущее московское время, чтобы свежий секрет был
  // валиден сразу. Оператор может сдвинуть или очистить поле.
  const [validFrom, setValidFrom] = useState(() =>
    isoToLocal(new Date().toISOString()),
  );
  const [validTo, setValidTo] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const needsDept = scope === "department" || scope === "cross_department";
  // Владелец dept/cross-кред'ы — собственный отдел создателя; backend всё равно
  // приклеит owner к dept'у актора, поэтому позволять вписывать произвольный id
  // нет смысла. Поле залочено: показываем имя отдела (fallback на id), а в
  // запрос уходит сам id.
  const ownerDeptId = defaultDeptId ?? "";
  const ownerDeptLabel = ownerDeptId
    ? depts.get(ownerDeptId) ?? ownerDeptId
    : "";
  // valid_to обязан быть в будущем и строго позже valid_from — backend
  // отбивает 422; гасим submit заранее, чтобы не ловить ошибку формой.
  const windowInvalid =
    (validTo !== "" && new Date(localToIso(validTo)).getTime() <= Date.now()) ||
    (validFrom !== "" &&
      validTo !== "" &&
      new Date(localToIso(validTo)).getTime() <=
        new Date(localToIso(validFrom)).getTime());
  const valid =
    name.trim() &&
    service.trim() &&
    secret.trim() &&
    (!needsDept || ownerDeptId.trim()) &&
    !windowInvalid;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const body: CredentialCreateRequest = {
      name: name.trim(),
      service: service.trim(),
      scope,
      secret,
      login: login.trim() || null,
      // owner_dept_id обязателен для dept-scope, запрещён для personal.
      owner_dept_id: needsDept ? ownerDeptId.trim() : null,
      visible_to_dept: needsDept ? visibleToDept : false,
      // datetime-local даёт naive-строку без зоны; backend трактует naive как
      // UTC. NULL = open-ended с этой стороны.
      valid_from: validFrom ? localToIso(validFrom) : null,
      valid_to: validTo ? localToIso(validTo) : null,
    };
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 w-full">
        <div className="flex items-center gap-2 mb-4">
          <Button variant="ghost"
            className="flex items-center gap-1"
            onClick={onCancel}
            type="button"
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </Button>
          <div className="text-sm text-dim">Создание учётных данных</div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">name *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={64}
              placeholder="core-postgres-replica"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">service *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={service}
              onChange={(e) => setService(e.target.value)}
              required
              maxLength={64}
              placeholder="postgres / jira / github…"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">scope *</span>
            <Dropdown
              mode="single"
              options={[
                { value: "personal", label: "personal" },
                ...(canManage
                  ? [
                      { value: "department", label: "department" },
                      { value: "cross_department", label: "cross_department" },
                    ]
                  : []),
              ]}
              value={scope}
              onChange={(v) => setScope(v as CredentialScope)}
            />
            {!canManage && (
              <span className="text-[11px] text-dim">
                department / cross_department доступны dep_admin и
                secret.operator/admin.
              </span>
            )}
          </label>
          {needsDept && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">owner dept</span>
              <input
                className="surface-2 border border-token rounded px-2 py-1 text-dim"
                value={ownerDeptLabel || "—"}
                readOnly
                disabled
                title={ownerDeptId || undefined}
              />
              <span className="text-[11px] text-dim">
                Владелец — ваш отдел; изменить нельзя.
              </span>
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">login</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              maxLength={4096}
              placeholder="опционально"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">secret *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1 mono"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
              maxLength={8192}
              placeholder="plaintext — шифруется at-rest"
            />
          </label>
          {needsDept && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={visibleToDept}
                onChange={(e) => setVisibleToDept(e.target.checked)}
              />
              <span className="text-dim text-xs">
                visible_to_dept (guest видит факт существования)
              </span>
            </label>
          )}
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">valid_from (MSK)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-2 py-1"
                value={validFrom}
                onChange={(e) => setValidFrom(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">valid_to (MSK, в будущем)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-2 py-1"
                value={validTo}
                onChange={(e) => setValidTo(e.target.value)}
              />
            </label>
          </div>
          {windowInvalid && (
            <div className="text-xs text-danger">
              valid_to должен быть в будущем и строго позже valid_from.
            </div>
          )}

          <div className="flex items-center gap-2 mt-2">
            <Button variant="primary"
              type="submit"
              disabled={submitting || !valid}
            >
              {submitting ? "Создаём…" : "Создать"}
            </Button>
            <Button type="button" onClick={onCancel}>
              Отмена
            </Button>
          </div>
        </form>
      </div>
    </section>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Модалки управления

/**
 * Выбор отдела по имени. Если карта отделов непустая — dropdown с именами
 * (значение запроса — сырой `dep_*` id). Если карта пуста (dep_admin видит
 * только свой отдел через identity-сид, глобальный список гейтится 403) —
 * текстовый ввод сырого id с подсказкой.
 */
function DeptPicker({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
}) {
  const { depts } = useLabelMaps();
  const options = useMemo(
    () => [...depts.entries()].sort((a, b) => a[1].localeCompare(b[1])),
    [depts],
  );

  if (options.length === 0) {
    return (
      <input
        className="surface-2 border border-token rounded px-2 py-1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={64}
        required
        placeholder={placeholder ?? "dep_…"}
        title={value || undefined}
      />
    );
  }

  return (
    <Dropdown
      mode="single"
      searchable
      options={options.map(([id, name]) => ({ value: id, label: name }))}
      value={value}
      onChange={onChange}
      placeholder="— выберите отдел —"
    />
  );
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header flex items-center justify-between">
          <div className="text-sm font-semibold">{title}</div>
          <Button variant="ghost" className="p-1" onClick={onClose} aria-label="Закрыть">
            <X className="w-4 h-4" />
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

function EditModal({
  cred,
  onClose,
  onSubmit,
}: {
  cred: Credential;
  onClose: () => void;
  onSubmit: (body: CredentialUpdateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState(cred.name);
  const [login, setLogin] = useState(cred.login ?? "");
  const [secret, setSecret] = useState("");
  const [validFrom, setValidFrom] = useState(isoToLocal(cred.valid_from));
  const [validTo, setValidTo] = useState(isoToLocal(cred.valid_to));
  const [submitting, setSubmitting] = useState(false);

  // backend требует valid_to > valid_from; гасим submit, чтобы не словить 422.
  const windowInvalid =
    validFrom !== "" &&
    validTo !== "" &&
    new Date(localToIso(validTo)).getTime() <=
      new Date(localToIso(validFrom)).getTime();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || windowInvalid) return;
    // Шлём только реально изменённые поля. Пустой secret = не перешифровывать.
    // Очистку окна через PATCH backend намеренно не маппит (NULL не снимает
    // защиту), поэтому пустое поле = «не трогать», а не «обнулить».
    const body: CredentialUpdateRequest = {};
    if (name.trim() && name.trim() !== cred.name) body.name = name.trim();
    if (login.trim() !== (cred.login ?? "")) body.login = login.trim() || null;
    if (secret) body.secret = secret;
    if (validFrom && validFrom !== isoToLocal(cred.valid_from))
      body.valid_from = localToIso(validFrom);
    if (validTo && validTo !== isoToLocal(cred.valid_to))
      body.valid_to = localToIso(validTo);
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title={`Изменить ${cred.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">name</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={64}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">login</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            maxLength={4096}
            placeholder="пусто = очистить login"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">новый секрет (перешифровать)</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 mono"
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            maxLength={8192}
            placeholder="пусто = оставить текущий секрет"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">valid_from (MSK)</span>
            <input
              type="datetime-local"
              className="surface-2 border border-token rounded px-2 py-1"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">valid_to (MSK)</span>
            <input
              type="datetime-local"
              className="surface-2 border border-token rounded px-2 py-1"
              value={validTo}
              onChange={(e) => setValidTo(e.target.value)}
            />
          </label>
        </div>
        {windowInvalid && (
          <div className="text-xs text-danger">
            valid_to должен быть строго позже valid_from.
          </div>
        )}
        <div className="flex items-center gap-2 mt-1">
          <Button variant="primary"
            type="submit"
            disabled={submitting || windowInvalid}
          >
            {submitting ? "Сохраняем…" : "Сохранить"}
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

function TransferModal({
  scope,
  onClose,
  onSubmit,
}: {
  scope: CredentialScope;
  onClose: () => void;
  onSubmit: (body: TransferRequest) => void | Promise<void>;
}) {
  // personal → новый owner-user; department/cross_department → новый owner-dept.
  const isPersonal = scope === "personal";
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const valid = target.trim() && reason.trim();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    const body: TransferRequest = isPersonal
      ? { new_owner_user_id: target.trim(), reason: reason.trim() }
      : { new_owner_dept_id: target.trim(), reason: reason.trim() };
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Передать владение" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <div className="text-xs text-dim">
          Передача допустима только для заблокированной кред'ы и снимает блокировку.
          Гейтится admin secret_service владеющего dept'а.
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            {isPersonal ? "новый владелец-пользователь *" : "новый владелец-отдел *"}
          </span>
          {isPersonal ? (
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              maxLength={64}
              required
              placeholder="usr_…"
            />
          ) : (
            <DeptPicker
              value={target}
              onChange={setTarget}
              placeholder="dep_…"
            />
          )}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">reason * (попадёт в audit)</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={256}
            required
          />
        </label>
        <div className="flex items-center gap-2 mt-1">
          <Button variant="primary"
            type="submit"
            disabled={submitting || !valid}
          >
            {submitting ? "Передаём…" : "Передать"}
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

export function AclModal({
  isCross,
  actorDeptId,
  onClose,
  onSubmit,
}: {
  isCross: boolean;
  actorDeptId: string | null;
  onClose: () => void;
  onSubmit: (body: {
    dept_id: string;
    role_name: string;
    can_read: boolean;
    can_write: boolean;
  }) => void | Promise<void>;
}) {
  const { depts } = useLabelMaps();
  // personal/department: ACL действует в отделе-владельце = свой отдел актора,
  // выбор не нужен (залочено). cross_department: dept_id — recipient-отдел,
  // даём dept-пикер.
  const lockedDept = !isCross && !!actorDeptId;
  const [deptId, setDeptId] = useState(lockedDept ? (actorDeptId ?? "") : "");
  const [roleName, setRoleName] = useState("reader");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Каталог ролей (dept × secret_service). Грузим как deptId определён: для
  // department он залочен на отдел владельца сразу, для cross — после выбора
  // recipient-отдела. Если каталог недоступен (403 у сервис-админа без доступа
  // к ролям) или пуст — откатываемся на ручной ввод имени роли, чтобы не ломать
  // его сценарий выдачи.
  const rolesQ = useQuery(
    () => listServiceRoles(deptId, "secret_service"),
    [deptId],
    { enabled: !!deptId },
  );
  const roleOptions = rolesQ.data ?? [];
  const useRoleSelect = !rolesQ.loading && !rolesQ.error && roleOptions.length > 0;

  // Подставляем первую доступную роль, когда подъехал список и текущее значение
  // в нём отсутствует — иначе select показал бы пункт, не совпадающий со state.
  useEffect(() => {
    if (!useRoleSelect) return;
    if (!roleOptions.some((r) => r.role_name === roleName)) {
      setRoleName(roleOptions[0]?.role_name ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useRoleSelect, deptId]);

  const valid = deptId.trim() && roleName.trim() && (canRead || canWrite);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    setSubmitting(true);
    Promise.resolve(
      onSubmit({
        dept_id: deptId.trim(),
        role_name: roleName.trim(),
        can_read: canRead,
        can_write: canWrite,
      }),
    ).finally(() => setSubmitting(false));
  }

  return (
    <ModalShell title="Выдать RoleACL" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        {isCross && (
          <div className="text-xs text-warn">
            Cross-dept recipient требует заранее выданного DeptGrant — иначе backend
            отбивает (DEPT_GRANT_REQUIRED).
          </div>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">отдел *</span>
          {lockedDept ? (
            <input
              className="surface-2 border border-token rounded px-2 py-1 text-dim"
              value={depts.get(deptId) ?? deptId}
              readOnly
              disabled
              title={deptId}
            />
          ) : (
            <DeptPicker
              value={deptId}
              onChange={setDeptId}
              placeholder="dep_…"
            />
          )}
          {lockedDept && (
            <span className="text-[11px] text-dim">
              ACL действует в вашем отделе.
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">role_name *</span>
          {useRoleSelect ? (
            <Dropdown
              mode="single"
              options={roleOptions.map((r) => ({
                value: r.role_name,
                label: r.description ? `${r.role_name} — ${r.description}` : r.role_name,
              }))}
              value={roleName}
              onChange={setRoleName}
            />
          ) : (
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              maxLength={64}
              required
              placeholder="reader / operator / …"
            />
          )}
          {!useRoleSelect && !!deptId && !rolesQ.loading && (
            <span className="text-[11px] text-dim">
              Каталог ролей недоступен — впишите имя роли вручную.
            </span>
          )}
        </label>
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canRead}
              onChange={(e) => setCanRead(e.target.checked)}
            />
            <span className="text-dim text-xs">can_read</span>
          </label>
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canWrite}
              onChange={(e) => setCanWrite(e.target.checked)}
            />
            <span className="text-dim text-xs">can_write</span>
          </label>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <Button variant="primary"
            type="submit"
            disabled={submitting || !valid}
          >
            {submitting ? "Выдаём…" : "Выдать"}
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}

/** Строка списка UserACL: резолвит `usr_*` в username хуком (нельзя в .map). */
function UserAclRow({
  userId,
  canRead,
  canWrite,
  acting,
  onRevoke,
}: {
  userId: string;
  canRead: boolean;
  canWrite: boolean;
  acting: boolean;
  onRevoke: () => void;
}) {
  const username = useUserLabel(userId);
  return (
    <div className="stat-row items-center">
      <span className="text-dim" title={userId}>
        {username}
      </span>
      <span className="flex items-center gap-2">
        <span className="mono text-xs">
          {canRead ? "r" : "-"}
          {canWrite ? "w" : "-"}
        </span>
        <Button variant="ghost"
          className="p-1"
          title="Снять доступ"
          disabled={acting}
          onClick={onRevoke}
        >
          <X className="w-3.5 h-3.5" />
        </Button>
      </span>
    </div>
  );
}

/**
 * Выдача user-ACL: выбор пользователя с резолвом в `usr_*`.
 *
 * Два режима. Если у актора есть доступ к списку юзеров отдела
 * (`canPick` — dep_admin/account_admin, и известен `pickerDeptId`) — показываем
 * пикер: select со списком username отдела, сразу с готовым `usr_*` id, плюс
 * поиск по подстроке. Иначе (сервис-админ secret.admin без dep-admin прав)
 * остаётся ручной ввод username с точечным резолвом через `GET /users/resolve`
 * (виден свой отдел) — бэкенд list-юзеров такому актору отдаёт 403. Сырой
 * `usr_*` id принимается в обоих режимах.
 */
export function UserAclModal({
  canPick,
  pickerDeptId,
  resolveDeptId,
  onClose,
  onSubmit,
}: {
  canPick: boolean;
  pickerDeptId: string | null;
  resolveDeptId: string | null;
  onClose: () => void;
  onSubmit: (body: {
    user_id: string;
    can_read: boolean;
    can_write: boolean;
  }) => void | Promise<void>;
}) {
  const pickEnabled = canPick && !!pickerDeptId;
  const usersQ = useQuery(
    () => listUsersByDepartment(pickerDeptId as string, { limit: 200 }),
    [pickerDeptId],
    { enabled: pickEnabled },
  );
  const userItems = usersQ.data?.items ?? [];
  const usePicker = pickEnabled && !usersQ.loading && !usersQ.error && userItems.length > 0;

  const [input, setInput] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [canRead, setCanRead] = useState(true);
  const [canWrite, setCanWrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [resolveErr, setResolveErr] = useState<string | null>(null);

  const valid = usePicker
    ? !!selectedId && (canRead || canWrite)
    : input.trim() && (canRead || canWrite);

  // username → usr_*: в режиме пикера id уже выбран. В ручном режиме сырой id
  // пропускаем как есть, иначе точечно дёргаем /users/resolve.
  async function resolveUserId(): Promise<string | null> {
    if (usePicker) return selectedId || null;
    const raw = input.trim();
    if (!raw) return null;
    if (raw.startsWith("usr_")) return raw;
    try {
      const r = await resolveUser(raw);
      return r.user_id;
    } catch {
      return null;
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !valid) return;
    setSubmitting(true);
    setResolveErr(null);
    const userId = await resolveUserId();
    if (!userId) {
      setResolveErr(
        "Пользователь не найден в вашем отделе. Можно ввести id (usr_…) напрямую.",
      );
      setSubmitting(false);
      return;
    }
    try {
      await Promise.resolve(
        onSubmit({ user_id: userId, can_read: canRead, can_write: canWrite }),
      );
    } finally {
      setSubmitting(false);
    }
  }

  // resolveDeptId участвует только в ручном резолве через бэкенд (виден свой
  // отдел) — отдельный запрос не нужен, но держим параметр явным, чтобы вызов
  // не зависел от неявного контекста.
  void resolveDeptId;

  return (
    <ModalShell title="Выдать доступ пользователю" onClose={onClose}>
      <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
        <div className="text-xs text-dim">
          {usePicker
            ? "Доступ выдаётся конкретному пользователю отдела в дополнение к ролевым ACL. Выберите пользователя из списка."
            : "Доступ выдаётся конкретному пользователю в дополнение к ролевым ACL. Введите username — он будет сопоставлен с id (виден ваш отдел). Сырой id (usr_…) тоже принимается."}
        </div>
        {usePicker ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">пользователь *</span>
            <Dropdown
              mode="single"
              searchable
              options={userItems.map((u) => ({ value: u.id, label: u.username }))}
              value={selectedId}
              onChange={(v) => {
                setSelectedId(v);
                setResolveErr(null);
              }}
              placeholder="выберите пользователя…"
            />
            {resolveErr && (
              <span className="text-[11px] text-danger">{resolveErr}</span>
            )}
          </label>
        ) : (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">username *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                setResolveErr(null);
              }}
              maxLength={64}
              required
              placeholder="username или usr_…"
            />
            {resolveErr && (
              <span className="text-[11px] text-danger">{resolveErr}</span>
            )}
          </label>
        )}
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canRead}
              onChange={(e) => setCanRead(e.target.checked)}
            />
            <span className="text-dim text-xs">can_read</span>
          </label>
          <label className="flex items-center gap-2">
            <Checkbox
              checked={canWrite}
              onChange={(e) => setCanWrite(e.target.checked)}
            />
            <span className="text-dim text-xs">can_write</span>
          </label>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <Button variant="primary"
            type="submit"
            disabled={submitting || !valid}
          >
            {submitting ? "Выдаём…" : "Выдать"}
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
