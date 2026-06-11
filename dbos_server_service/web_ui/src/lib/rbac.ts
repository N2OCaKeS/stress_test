import type { Persona } from "@/types/persona";

/**
 * Returns the dept id as-is (or a platform-wide marker for empty id).
 * Human-readable dept name живёт в LabelsProvider; используйте `useDeptLabel`
 * там, где доступен React-контекст. Эта функция нужна только для мест,
 * где hook позвать нельзя.
 */
export function deptDisplayName(deptId?: string | null): string {
  if (!deptId) return "— (платформенный)";
  return deptId;
}

export function personaDeptId(persona: Persona): string | null {
  return persona.dept_id ?? null;
}

/** True if persona is a platform-wide admin (no dept scope). */
export function isPlatformWideAdmin(persona: Persona): boolean {
  return persona.platform_role === "account_admin";
}

/** True if persona is a dep_admin (single dept). */
export function isDepAdmin(persona: Persona): boolean {
  return persona.platform_role === "dep_admin";
}

/** True if persona has any logging-only role (view-only on aux services). */
export function isLoggingOnly(persona: Persona): boolean {
  return (
    persona.platform_role === "logging_admin" ||
    persona.platform_role === "logging_reader"
  );
}

/**
 * True if persona only gets read-only view of cluster admin items
 * (Health / TLS / Rotations / Backups / Migrations / Config / Audit overview).
 *
 * logging_reader — read-only по всему /admin блоку, mutation-кнопки
 * («Trigger», «Запустить backup», «Renew TLS», «Force migration») скрываются
 * сверху лежит .readonly-bar. Все остальные персоны, у которых cluster items
 * вообще видны (фильтрация — через adminCatalog visibility), получают
 * write-mode.
 */
export function isReadOnlyForCluster(persona: Persona): boolean {
  return persona.platform_role === "logging_reader";
}

/**
 * True if persona is a service-level admin spanning >1 service. This is
 * a fact about service_roles (≥ 2 admin grants), not a platform role.
 */
export function isMultiAdmin(persona: Persona): boolean {
  const svcRoles = persona.service_roles ?? {};
  return (
    persona.platform_role === null &&
    Object.values(svcRoles).filter((r) => r === "admin").length >= 2
  );
}

/**
 * True if persona has service_roles.secret = admin. Это факт по сервис-
 * ролям, а не платформенная роль.
 */
export function isSecretAdmin(persona: Persona): boolean {
  return persona.service_roles?.secret === "admin";
}

export interface UserMutationCaps {
  /** Edit profile (email, status), reset password, force-MFA-reset. */
  edit: boolean;
  /** Block/unblock, revoke sessions. */
  disable: boolean;
  /** Hard delete. */
  delete: boolean;
  /** Edit role assignments / direct grants / group membership. */
  manageRoles: boolean;
  reason: string;
}

/**
 * RBAC for /users/<id> detail mutations.
 *
 * - account_admin — full.
 * - dep_admin    — full within own dept; platform users => none.
 * - logging_* — view only.
 * - service-only admin (secret.admin alone) — view only on users; user
 *   mutation требует dep_admin или account_admin.
 */
export function userMutationCaps(
  persona: Persona,
  targetDeptId: string | null,
): UserMutationCaps {
  if (isPlatformWideAdmin(persona)) {
    return { edit: true, disable: true, delete: true, manageRoles: true, reason: "" };
  }
  if (isLoggingOnly(persona)) {
    return {
      edit: false,
      disable: false,
      delete: false,
      manageRoles: false,
      reason: "logging-роль — только просмотр",
    };
  }
  if (isSecretAdmin(persona) && !isMultiAdmin(persona) && !isDepAdmin(persona)) {
    return {
      edit: false,
      disable: false,
      delete: false,
      manageRoles: false,
      reason: "service-роль secret.admin не даёт прав на mutation пользователей",
    };
  }
  // dep_admin / multi-service-admin: dept-scoped.
  const myDept = personaDeptId(persona);
  if (myDept && targetDeptId && myDept === targetDeptId) {
    return { edit: true, disable: true, delete: true, manageRoles: true, reason: "" };
  }
  return {
    edit: false,
    disable: false,
    delete: false,
    manageRoles: false,
    reason: myDept
      ? "пользователь вне вашего dept-scope"
      : "нет dept — нет прав на mutation",
  };
}

export interface GroupMutationCaps {
  edit: boolean;
  delete: boolean;
  manageMembers: boolean;
  reason: string;
}

export function groupMutationCaps(
  persona: Persona,
  ownerDept: string | null,
): GroupMutationCaps {
  if (isPlatformWideAdmin(persona)) {
    return { edit: true, delete: true, manageMembers: true, reason: "" };
  }
  if (isLoggingOnly(persona)) {
    return { edit: false, delete: false, manageMembers: false, reason: "logging-роль — только просмотр" };
  }
  if (isSecretAdmin(persona) && !isMultiAdmin(persona) && !isDepAdmin(persona)) {
    return { edit: false, delete: false, manageMembers: false, reason: "service-роль secret.admin не управляет группами" };
  }
  const myDept = personaDeptId(persona);
  if (myDept && ownerDept && myDept === ownerDept) {
    return { edit: true, delete: true, manageMembers: true, reason: "" };
  }
  // cross-dept groups (ownerDept === null) — account_admin only, already handled above.
  return {
    edit: false,
    delete: false,
    manageMembers: false,
    reason: ownerDept === null
      ? "cross-dept группа — только account_admin"
      : "группа вне вашего dept-scope",
  };
}

export interface BotMutationCaps {
  rotateToken: boolean;
  revokeToken: boolean;
  manageRoles: boolean;
  delete: boolean;
  reason: string;
}

/**
 * RBAC for /users/bot/<id> detail mutations.
 *
 * - account_admin — full.
 * - dep_admin — full if bot.owner_dept matches.
 * - service-only admin (secret.admin etc) — manages bots of own dept
 *   (token + roles).
 * - logging_* — view only.
 */
export function botMutationCaps(
  persona: Persona,
  ownerDept: string | null,
): BotMutationCaps {
  if (isPlatformWideAdmin(persona)) {
    return { rotateToken: true, revokeToken: true, manageRoles: true, delete: true, reason: "" };
  }
  if (isLoggingOnly(persona)) {
    return {
      rotateToken: false,
      revokeToken: false,
      manageRoles: false,
      delete: false,
      reason: "logging-роль — только просмотр",
    };
  }
  const myDept = personaDeptId(persona);
  if (myDept && ownerDept && myDept === ownerDept) {
    return { rotateToken: true, revokeToken: true, manageRoles: true, delete: true, reason: "" };
  }
  return {
    rotateToken: false,
    revokeToken: false,
    manageRoles: false,
    delete: false,
    reason: ownerDept === null
      ? "cross-dept бот — только account_admin"
      : "бот вне вашего dept-scope",
  };
}
