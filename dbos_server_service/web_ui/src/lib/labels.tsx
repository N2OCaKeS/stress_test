/**
 * LabelsProvider — общий кэш человекочитаемых имён для отделов, групп,
 * сервисов и серверов.
 *
 * Поле `name` — единственное human-readable; raw id (`dep_*`, `grp_*`,
 * `server_service`, `srv_*`) остаются как «техническое» (mono-tooltip / hint).
 *
 * Один маунт на всё приложение: грузим списки один раз, делим Map'ы между
 * всеми компонентами. В mock-режиме backend не дёргаем — useDeptLabel et al
 * сами фоллбэкаются на id.
 *
 * Карта серверов грузится лениво: список серверов гейтится server-зоной
 * (dep_admin / server.*), платформенные admin'ы (account_admin / loging_*) к
 * нему доступа не имеют. Дёргаем его только когда `useServerLabel` реально
 * вызван (страницы worker/server-зоны), чтобы не ловить 403 на чужих экранах.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { listDepartments } from "@/api/auth/departments";
import { listGroups } from "@/api/auth/groups";
import { listServices } from "@/api/auth/services";
import { getUser } from "@/api/auth/users";
import { listServers } from "@/api/server/servers";
import { getAccount } from "@/api/server/accounts";
import { getCredential } from "@/api/secret/credentials";
import { USE_MOCK_AUTH, useAuthOptional } from "@/contexts/AuthContext";

type Domain = "depts" | "groups" | "services" | "servers" | "all";

interface LabelsState {
  depts: Map<string, string>;
  groups: Map<string, string>;
  services: Map<string, string>;
  servers: Map<string, string>;
  ready: boolean;
  /** Принудительно перезалить карту нужного домена с backend. Зовётся из
   *  страниц после успешного create/delete/rename. `domain="all"` гонит
   *  не-ленивые домены (без серверов) параллельно. В mock-режиме no-op. */
  invalidate: (domain?: Domain) => Promise<void>;
  /** Запросить ленивую загрузку карты серверов. No-op после первого зова
   *  и в mock-режиме; зовётся хуком `useServerLabel`. */
  ensureServers: () => void;
}

const EMPTY: LabelsState = {
  depts: new Map(),
  groups: new Map(),
  services: new Map(),
  servers: new Map(),
  ready: false,
  invalidate: async () => {},
  ensureServers: () => {},
};

const LabelsContext = createContext<LabelsState>(EMPTY);

async function loadDepts(): Promise<Map<string, string>> {
  const m = new Map<string, string>();
  try {
    const items = await listDepartments();
    for (const d of items) m.set(d.id, d.name);
  } catch {
    // ignore — оставляем старую карту до следующего invalidate.
  }
  return m;
}

async function loadGroups(): Promise<Map<string, string>> {
  const m = new Map<string, string>();
  try {
    const items = await listGroups({ limit: 200 });
    for (const g of items) m.set(g.id, g.name);
  } catch {
    // ignore
  }
  return m;
}

async function loadServices(): Promise<Map<string, string>> {
  const m = new Map<string, string>();
  try {
    const items = await listServices();
    for (const s of items) m.set(s.service_name, s.service_name);
  } catch {
    // ignore
  }
  return m;
}

async function loadServers(): Promise<Map<string, string>> {
  const m = new Map<string, string>();
  try {
    // department_admin / server.* видят только серверы своего отдела —
    // backend сам фильтрует выдачу по scope, отдельный department_id не нужен.
    const res = await listServers({ limit: 200 });
    for (const s of res.items) m.set(s.id, s.display_name || s.hostname);
  } catch {
    // 403 у платформенных admin'ов либо сетевой сбой — оставляем пустую карту,
    // useServerLabel зафоллбэчится на сырой id.
  }
  return m;
}

export function LabelsProvider({ children }: { children: ReactNode }) {
  const auth = useAuthOptional();
  // department_admin не может перечислить все отделы (auth_service отдаёт 403
  // на глобальный список), но собственный отдел всегда лежит в identity. Этим
  // сидом гарантируем, что его имя резолвится даже без глобального доступа —
  // иначе свой dep_* показывался бы сырым id в nav/карточках/задачах.
  const ownDeptId = auth?.user?.department_id ?? null;
  const ownDeptName = auth?.user?.department_name ?? null;

  const [depts, setDepts] = useState<Map<string, string>>(new Map());
  const [groups, setGroups] = useState<Map<string, string>>(new Map());
  const [services, setServices] = useState<Map<string, string>>(new Map());
  const [servers, setServers] = useState<Map<string, string>>(new Map());
  const [ready, setReady] = useState(false);
  // Карта серверов грузится один раз по первому `ensureServers`; ref
  // гасит повторные запросы при каждом ре-рендере списка задач.
  const serversRequested = useRef(false);

  const refreshServers = useCallback(async () => {
    setServers(await loadServers());
  }, []);

  const ensureServers = useCallback(() => {
    if (USE_MOCK_AUTH || serversRequested.current) return;
    serversRequested.current = true;
    void refreshServers();
  }, [refreshServers]);

  const refreshAll = useCallback(async () => {
    const [d, g, s] = await Promise.all([
      loadDepts(),
      loadGroups(),
      loadServices(),
    ]);
    if (ownDeptId && ownDeptName && !d.has(ownDeptId)) d.set(ownDeptId, ownDeptName);
    setDepts(d);
    setGroups(g);
    setServices(s);
    setReady(true);
  }, [ownDeptId, ownDeptName]);

  const invalidate = useCallback(
    async (domain: Domain = "all") => {
      if (USE_MOCK_AUTH) return;
      if (domain === "all") return refreshAll();
      if (domain === "depts") {
        const d = await loadDepts();
        if (ownDeptId && ownDeptName && !d.has(ownDeptId)) d.set(ownDeptId, ownDeptName);
        setDepts(d);
      } else if (domain === "groups") setGroups(await loadGroups());
      else if (domain === "services") setServices(await loadServices());
      else if (domain === "servers") {
        // Перезаливаем серверы только если их кто-то уже запрашивал —
        // иначе нет смысла дёргать гейченный endpoint впустую.
        if (serversRequested.current) await refreshServers();
      }
    },
    [refreshAll, refreshServers, ownDeptId, ownDeptName],
  );

  useEffect(() => {
    if (USE_MOCK_AUTH) {
      setReady(true);
      return;
    }
    void refreshAll();
  }, [refreshAll]);

  // identity может прийти позже глобального refresh'а (или тот мог отвалиться
  // на 403) — досидим собственный отдел, как только он известен.
  useEffect(() => {
    if (!ownDeptId || !ownDeptName) return;
    setDepts((prev) => {
      if (prev.get(ownDeptId) === ownDeptName) return prev;
      const next = new Map(prev);
      next.set(ownDeptId, ownDeptName);
      return next;
    });
  }, [ownDeptId, ownDeptName]);

  const value = useMemo<LabelsState>(
    () => ({ depts, groups, services, servers, ready, invalidate, ensureServers }),
    [depts, groups, services, servers, ready, invalidate, ensureServers],
  );

  return (
    <LabelsContext.Provider value={value}>{children}</LabelsContext.Provider>
  );
}

/** Хук для invalidate из любого write-flow: после `createDepartment` etc.
 *  вызови `labels.invalidate('depts')` чтобы карта отразила новое имя. */
export function useLabelsInvalidate(): LabelsState["invalidate"] {
  return useContext(LabelsContext).invalidate;
}

function useLabels(): LabelsState {
  return useContext(LabelsContext);
}

/**
 * Returns human-readable department label. Falls back to id if не загружено,
 * к "—" если id пустое.
 */
export function useDeptLabel(deptId: string | null | undefined): string {
  const { depts } = useLabels();
  if (!deptId) return "—";
  return depts.get(deptId) ?? deptId;
}

/** Same but returns null on пустое id (для optional-cases в join'ах). */
export function useDeptLabelOpt(deptId: string | null | undefined): string | null {
  const { depts } = useLabels();
  if (!deptId) return null;
  return depts.get(deptId) ?? deptId;
}

export function useGroupLabel(groupId: string | null | undefined): string {
  const { groups } = useLabels();
  if (!groupId) return "—";
  return groups.get(groupId) ?? groupId;
}

export function useServiceLabel(serviceName: string | null | undefined): string {
  const { services } = useLabels();
  if (!serviceName) return "—";
  return services.get(serviceName) ?? serviceName;
}

/**
 * Человекочитаемое имя сервера (`display_name` или `hostname`) по `srv_*` id.
 *
 * Триггерит ленивую загрузку карты серверов при первом вызове. Фоллбэк на
 * сырой id, пока карта грузится / если сервер удалён / если у роли нет
 * доступа к списку серверов (платформенные admin'ы). `null`/пусто → "—".
 */
export function useServerLabel(serverId: string | null | undefined): string {
  const { servers, ensureServers } = useLabels();
  useEffect(() => {
    ensureServers();
  }, [ensureServers]);
  if (!serverId) return "—";
  return servers.get(serverId) ?? serverId;
}

/** Same but returns null on пустое id (для optional-join'ов). */
export function useServerLabelOpt(
  serverId: string | null | undefined,
): string | null {
  const { servers, ensureServers } = useLabels();
  useEffect(() => {
    ensureServers();
  }, [ensureServers]);
  if (!serverId) return null;
  return servers.get(serverId) ?? serverId;
}

/**
 * Returns the raw maps. Use when у компонента уже есть свой `useMemo` /
 * сортировка по нескольким id'шникам, и хук-в-цикле звать неудобно.
 */
export function useLabelMaps() {
  const { depts, groups, services } = useLabels();
  return useMemo(
    () => ({ depts, groups, services }),
    [depts, groups, services],
  );
}

/**
 * Карта серверов (`srv_*` → display_name/hostname) с ленивой загрузкой.
 * Для компонентов, которым нужен резолв нескольких id'шников в `useMemo`
 * (где хук-в-цикле звать нельзя).
 */
export function useServerMap() {
  const { servers, ensureServers } = useLabels();
  useEffect(() => {
    ensureServers();
  }, [ensureServers]);
  return servers;
}

/**
 * Ленивый резолвер «один id → имя» для доменов без дешёвого глобального
 * списка: пользователи (глобальный list гейтится, dep_admin ловит 403),
 * server_account'ы (list требует обязательный `server_id`, общей выдачи нет)
 * и credential'ы. В отличие от dept/group/service/server, грузим точечно по
 * запрошенному id и кэшируем результат.
 *
 * Кэш — module-level, общий на всё приложение и не зависит от
 * `LabelsProvider`: компоненты, которым нужен только id→name, не обязаны
 * висеть под провайдером. In-flight промисы дедуплицируются, чтобы один и
 * тот же id не дёргался параллельно из нескольких строк.
 */
type IdResolver = (id: string) => Promise<string>;

const resolveUserName: IdResolver = async (id) => {
  const u = await getUser(id);
  return u.username || id;
};

const resolveAccountName: IdResolver = async (id) => {
  const a = await getAccount(id);
  return a.login || id;
};

const resolveCredentialName: IdResolver = async (id) => {
  const c = await getCredential(id);
  return c.name || id;
};

interface LazyLabelDomain {
  cache: Map<string, string>;
  inflight: Map<string, Promise<string>>;
  resolve: IdResolver;
}

const USER_DOMAIN: LazyLabelDomain = {
  cache: new Map(),
  inflight: new Map(),
  resolve: resolveUserName,
};
const ACCOUNT_DOMAIN: LazyLabelDomain = {
  cache: new Map(),
  inflight: new Map(),
  resolve: resolveAccountName,
};
const CREDENTIAL_DOMAIN: LazyLabelDomain = {
  cache: new Map(),
  inflight: new Map(),
  resolve: resolveCredentialName,
};

function useLazyLabel(
  domain: LazyLabelDomain,
  id: string | null | undefined,
): string {
  const [, force] = useState(0);

  useEffect(() => {
    if (USE_MOCK_AUTH || !id || domain.cache.has(id)) return;
    let alive = true;
    let promise = domain.inflight.get(id);
    if (!promise) {
      promise = domain.resolve(id).catch(() => id);
      domain.inflight.set(id, promise);
    }
    void promise.then((name) => {
      domain.cache.set(id, name);
      domain.inflight.delete(id);
      if (alive) force((n) => n + 1);
    });
    return () => {
      alive = false;
    };
  }, [domain, id]);

  if (!id) return "—";
  return domain.cache.get(id) ?? id;
}

/**
 * `usr_*` → username. Фоллбэк на id, пока грузится / если пользователь удалён
 * или недоступен (403). `null`/пусто → "—".
 */
export function useUserLabel(userId: string | null | undefined): string {
  return useLazyLabel(USER_DOMAIN, userId);
}

/**
 * `acc_*` (server_account) → login. Фоллбэк на id, пока грузится / если
 * аккаунт удалён или недоступен. `null`/пусто → "—".
 */
export function useAccountLabel(accountId: string | null | undefined): string {
  return useLazyLabel(ACCOUNT_DOMAIN, accountId);
}

/**
 * `cred_*` → имя credential'а. Фоллбэк на id, пока грузится / если креда
 * удалена или недоступна. `null`/пусто → "—".
 */
export function useCredentialLabel(
  credId: string | null | undefined,
): string {
  return useLazyLabel(CREDENTIAL_DOMAIN, credId);
}
