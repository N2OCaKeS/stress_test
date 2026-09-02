/**
 * Mock инстанс-уровневого ACL (`resource_role_permissions`) для UI без backend.
 *
 * Держит in-memory набор грантов на ресурс + каталог инстанс-грантуемых
 * действий (подмножество `ENTITY_ACTIONS` за вычетом глобально-только). Toggle
 * и propagate мутируют локальный стор — сети нет. Используется компонентом
 * редактора прав в mock-режиме (`VITE_USE_MOCK_AUTH=true`).
 */

import type {
  ActionName,
  PermissionCatalogItem,
  PermissionListResponse,
  ResourceAclType,
  ResourcePermissionEffect,
  ResourcePermissionEntry,
  ResourcePropagateMode,
  ResourcePropagateResponse,
  RoleName,
} from "@/api/server/types";

interface MockAction {
  action: ActionName;
  description: string;
  sensitive: boolean;
}

const SERVER_ACTIONS: MockAction[] = [
  { action: "view", description: "Видеть карточку сервера", sensitive: false },
  { action: "update", description: "Редактировать сервер", sensitive: false },
  { action: "delete", description: "Удалить сервер", sensitive: true },
  { action: "busy_acquire", description: "Захватить сервер в работу", sensitive: false },
  { action: "busy_release", description: "Снять бронь", sensitive: false },
  { action: "os_sync", description: "Сменить версию ОС вручную", sensitive: false },
  { action: "power_on", description: "Включить питание (BMC)", sensitive: false },
  { action: "power_off", description: "Выключить питание (BMC)", sensitive: true },
  { action: "power_reboot", description: "Перезагрузить (BMC)", sensitive: true },
  { action: "power_status", description: "Опросить состояние питания", sensitive: false },
  { action: "inventory_trigger", description: "Запустить инвентаризацию", sensitive: false },
  { action: "console", description: "Интерактивная SSH-консоль", sensitive: true },
  { action: "view_drift", description: "Сводка drift по серверу", sensitive: false },
  { action: "manage_packages", description: "Массовое управление пакетами", sensitive: false },
];

const ACCOUNT_ACTIONS: MockAction[] = [
  { action: "view", description: "Видеть карточку учётки", sensitive: false },
  { action: "update", description: "Редактировать учётку", sensitive: false },
  { action: "delete", description: "Удалить учётку", sensitive: true },
  { action: "view_password", description: "Раскрыть пароль", sensitive: true },
  { action: "rotate_password", description: "Ротировать пароль", sensitive: true },
  { action: "grant_sudo", description: "Выдать sudo", sensitive: true },
  { action: "provision", description: "Завести OS-пользователя на боксе", sensitive: false },
  { action: "deprovision", description: "Снести OS-пользователя", sensitive: true },
  { action: "adopt_from_host", description: "Принять факт-состояние с хоста", sensitive: false },
  { action: "console", description: "Интерактивная консоль учётки", sensitive: true },
];

/** Каталог инстанс-грантуемых действий на каждый resource_type (mock). */
export const MOCK_INSTANCE_CATALOG: Record<ResourceAclType, PermissionCatalogItem> = {
  server: {
    entity_type: "server",
    description: "Сервер — инстанс-гранты на конкретную машину",
    actions: SERVER_ACTIONS.map((a) => ({ ...a, worker_only: false })),
  },
  server_account: {
    entity_type: "server_account",
    description: "Сервисная учётка — инстанс-гранты на конкретный аккаунт",
    actions: ACCOUNT_ACTIONS.map((a) => ({ ...a, worker_only: false })),
  },
};

// Базовая (тип-wide) матрица для mock-режима — что роль умеет «по умолчанию»
// на весь тип ресурса. По ней редактор проставляет чекбоксы; инстанс-гранты
// (store) накладываются поверх. admin везде считается полным доступом и в
// перечисление не входит.
const BASE_GRANTS: Record<ResourceAclType, Array<[RoleName, ActionName]>> = {
  server: [
    ["guest", "view"],
    ["reader", "view"],
    ["reader", "power_status"],
    ["operator", "view"],
    ["operator", "busy_acquire"],
    ["operator", "busy_release"],
    ["operator", "os_sync"],
    ["operator", "power_on"],
    ["operator", "power_off"],
    ["operator", "power_reboot"],
    ["operator", "power_status"],
    ["operator", "inventory_trigger"],
  ],
  server_account: [
    ["guest", "view"],
    ["reader", "view"],
    ["operator", "view"],
    ["operator", "rotate_password"],
    ["operator", "provision"],
  ],
};

/** Тип-wide матрица entity_permissions для mock-режима. */
export function mockBaseMatrix(type: ResourceAclType): PermissionListResponse {
  const now = "2026-06-29T00:00:00Z";
  const items = BASE_GRANTS[type].map(([role, action], i) => ({
    id: `perm_base_${type}_${i}`,
    entity_type: type,
    role,
    action,
    department_id: "core",
    granted_by: null,
    created_at: now,
    updated_at: now,
  }));
  return { items, total: items.length, described: false };
}

// key `${type}:${id}` → map `${role}::${action}` → effect
const store = new Map<string, Map<string, ResourcePermissionEffect>>();
let seq = 1;

function keyFor(type: ResourceAclType, id: string): string {
  return `${type}:${id}`;
}

function ensure(
  type: ResourceAclType,
  id: string,
): Map<string, ResourcePermissionEffect> {
  const k = keyFor(type, id);
  let map = store.get(k);
  if (!map) {
    map = new Map<string, ResourcePermissionEffect>();
    store.set(k, map);
  }
  return map;
}

// Демо-сид: пара override'ов поверх базовой матрицы — один deny (снимает
// базово-разрешённое действие) и один allow (выдаёт сверх базы), чтобы было
// видно отличие чекбокса от базы.
function seedIfEmpty(type: ResourceAclType, id: string): void {
  const k = keyFor(type, id);
  if (store.has(k)) return;
  const map = ensure(type, id);
  if (type === "server") {
    map.set("operator::power_off", "deny");
    map.set("reader::power_reboot", "allow");
  } else {
    map.set("operator::view_password", "allow");
    map.set("reader::rotate_password", "allow");
  }
}

export function mockListResourcePermissions(
  type: ResourceAclType,
  id: string,
): ResourcePermissionEntry[] {
  seedIfEmpty(type, id);
  const map = ensure(type, id);
  const now = "2026-06-29T00:00:00Z";
  return Array.from(map.entries()).map(([entry, effect]) => {
    const [role, action] = entry.split("::");
    return {
      id: `rrp_mock_${seq++}`,
      resource_type: type,
      resource_id: id,
      role: role as RoleName,
      action: action as ActionName,
      effect,
      department_id: "core",
      granted_by: "u-mock",
      created_at: now,
      updated_at: now,
    };
  });
}

export function mockGrant(
  type: ResourceAclType,
  id: string,
  role: RoleName,
  action: ActionName,
  effect: ResourcePermissionEffect = "allow",
): void {
  ensure(type, id).set(`${role}::${action}`, effect);
}

export function mockRevoke(
  type: ResourceAclType,
  id: string,
  role: RoleName,
  action: ActionName,
): void {
  ensure(type, id).delete(`${role}::${action}`);
}

export function mockPropagate(
  type: ResourceAclType,
  sourceId: string,
  targetIds: string[],
  mode: ResourcePropagateMode,
): ResourcePropagateResponse {
  const source = ensure(type, sourceId);
  const targets = targetIds.map((tid) => {
    const target = ensure(type, tid);
    let added = 0;
    let removed = 0;
    // merge/mirror переносят и effect: новый ключ или иной effect = added.
    for (const [g, effect] of source) {
      if (target.get(g) !== effect) {
        target.set(g, effect);
        added++;
      }
    }
    if (mode === "mirror") {
      for (const g of Array.from(target.keys())) {
        if (!source.has(g)) {
          target.delete(g);
          removed++;
        }
      }
    }
    return { resource_id: tid, added, removed };
  });
  return {
    source_resource_id: sourceId,
    resource_type: type,
    mode,
    source_grant_count: source.size,
    targets,
  };
}
