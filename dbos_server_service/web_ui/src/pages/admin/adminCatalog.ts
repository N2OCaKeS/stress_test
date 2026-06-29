import { createElement, type ComponentType } from "react";
import { Navigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  Building2,
  Clock,
  Cog,
  Container,
  Database,
  EyeOff,
  FileText,
  HardDrive,
  KeyRound,
  Layers,
  LockOpen,
  RotateCw,
  ScanSearch,
  ServerIcon,
  ShieldCheck,
  Unplug,
  UserCog,
  Users,
  UsersRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { Persona } from "@/types/persona";
import type { Service } from "@/api/auth/types";

import { ClusterHealth } from "./cluster/ClusterHealth";
import { ClusterTLS } from "./cluster/ClusterTLS";
import { ClusterRotations } from "./cluster/ClusterRotations";
import { ClusterBackups } from "./cluster/ClusterBackups";
import { ClusterMigrations } from "./cluster/ClusterMigrations";
import { ClusterAuditOverview } from "./cluster/ClusterAuditOverview";
import { ClusterConfig } from "./cluster/ClusterConfig";

import { ServicesUsers } from "./services/ServicesUsers";
import { ServicesDepartments } from "./services/ServicesDepartments";
import { ServicesCatalog } from "./services/ServicesCatalog";
import { ServicesBots } from "./services/ServicesBots";
import { ServicesGroups } from "./services/ServicesGroups";
import { ServicesPlatformRoles } from "./services/ServicesPlatformRoles";
import { ServicesServerGroups } from "./services/ServicesServerGroups";
import { ServicesServerPermissions } from "./services/ServicesServerPermissions";
import { ServicesOsVersions } from "./services/ServicesOsVersions";
import { ServicesIgnoredLogins } from "./services/ServicesIgnoredLogins";
import { ServicesManagementUser } from "./services/ServicesManagementUser";
import { ServicesSecretAccess } from "./services/ServicesSecretAccess";
import { ServicesLogingRules } from "./services/ServicesLogingRules";
import { ServicesLogingRetention } from "./services/ServicesLogingRetention";
import { ServicesEncryptionRotation } from "./services/ServicesEncryptionRotation";
import { ServiceRolesCard } from "./services/ServiceRolesCard";

import { SecurityTokens } from "../security/SecurityTokens";
import { SecurityOAuth2 } from "../security/SecurityOAuth2";
import { SecurityDocker } from "../security/SecurityDocker";
import { SecurityLockout } from "../security/SecurityLockout";
import { SecurityIntrospect } from "../security/SecurityIntrospect";
import { SecurityServiceAccess } from "../security/SecurityServiceAccess";
import { SecurityServices } from "../security/SecurityServices";

export type AdminBlock = "cluster" | "services";

export interface AdminItem {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  block: AdminBlock;
  /** Sub-section header used by AdminMiddle to group items inside a block. */
  group?: string;
  content: ComponentType;
  /** Predicate: true → item is visible to this persona. */
  visibleFor: (p: Persona) => boolean;
}

/* RBAC helpers — derived from persona shape, not literal mock ids.
 * Real backend users get UUID-style ids (`usr_…`), so we read platform_role
 * and service_roles instead. Mock personas still resolve correctly because
 * identityToPersona / personas.ts set the same shape. */
const isAccountAdmin = (p: Persona) => p.platform_role === "account_admin";
const isDepAdmin = (p: Persona) => p.platform_role === "dep_admin";
const isLoggingAdmin = (p: Persona) => p.platform_role === "logging_admin";
/* Service-role checks — not platform roles, just facts about per-service
 * grants. UI uses them to surface service-scoped admin tools (e.g. secret
 * templates / policies) to whoever has admin on that service. */
const hasSecretServiceAdmin = (p: Persona) => p.service_roles?.secret === "admin";
const hasServerServiceAdmin = (p: Persona) => p.service_roles?.server === "admin";

/**
 * Static catalogue items — cluster + auth admin + per-service feature pages
 * that are specific to a single backend service (inventory, groups, secrets,
 * policies, templates, alert rules, retention, DLQ, cron). Service-role items
 * are NOT here — they are generated from `GET /services` in `buildAdminItems`.
 */
const STATIC_ITEMS: AdminItem[] = [
  // Cluster block — security / health / backup grouping
  {
    id: "cluster.health",
    label: "Здоровье кластера",
    hint: "поды, CPU, uptime",
    icon: Activity,
    block: "cluster",
    group: "Здоровье",
    content: ClusterHealth,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "cluster.tls",
    label: "TLS / сертификаты",
    icon: ShieldCheck,
    block: "cluster",
    group: "Безопасность",
    content: ClusterTLS,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "cluster.rotations",
    label: "Master keys / ротации",
    hint: "6 видов",
    icon: RotateCw,
    block: "cluster",
    group: "Безопасность",
    content: ClusterRotations,
    visibleFor: (p) => isAccountAdmin(p) || hasSecretServiceAdmin(p),
  },
  {
    id: "cluster.encryption_rotation",
    label: "Ротация ключей шифрования",
    hint: "server · secret keystore",
    icon: KeyRound,
    block: "cluster",
    group: "Безопасность",
    content: ServicesEncryptionRotation,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "cluster.backups",
    label: "Бэкапы / DR-drills",
    icon: Archive,
    block: "cluster",
    group: "Backup",
    content: ClusterBackups,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "cluster.migrations",
    label: "Миграции",
    hint: "lazy re-encrypt",
    icon: Database,
    block: "cluster",
    group: "Backup",
    content: ClusterMigrations,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "cluster.audit",
    label: "Аудит-обзор",
    hint: "events / 24h",
    icon: FileText,
    block: "cluster",
    group: "Аудит",
    content: ClusterAuditOverview,
    visibleFor: (p) =>
      isAccountAdmin(p) || isDepAdmin(p) || isLoggingAdmin(p),
  },
  {
    id: "cluster.config",
    label: "Глобальные настройки",
    hint: "env · NetworkPolicy",
    icon: Cog,
    block: "cluster",
    group: "Аудит",
    content: ClusterConfig,
    visibleFor: (p) => isAccountAdmin(p),
  },

  // Services block — auth
  {
    id: "services.users",
    label: "Пользователи",
    hint: "auth_service",
    icon: Users,
    block: "services",
    group: "auth",
    content: ServicesUsers,
    visibleFor: (p) => isAccountAdmin(p) || isDepAdmin(p),
  },
  {
    id: "services.departments",
    label: "Отделы",
    icon: Building2,
    block: "services",
    group: "auth",
    content: ServicesDepartments,
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "services.bots",
    label: "Боты",
    icon: Bot,
    block: "services",
    group: "auth",
    content: ServicesBots,
    visibleFor: (p) => isAccountAdmin(p) || isDepAdmin(p),
  },
  {
    id: "services.groups",
    label: "Группы",
    icon: UsersRound,
    block: "services",
    group: "auth",
    content: ServicesGroups,
    visibleFor: (p) => isAccountAdmin(p) || isDepAdmin(p),
  },
  {
    id: "services.platform_roles",
    label: "Платформенные роли",
    hint: "read-only",
    icon: ShieldCheck,
    block: "services",
    group: "auth",
    content: ServicesPlatformRoles,
    visibleFor: (p) => isAccountAdmin(p) || isLoggingAdmin(p),
  },
  {
    id: "services.services_catalog",
    label: "Сервисы",
    hint: "каталог сервисов платформы",
    icon: Layers,
    block: "services",
    group: "auth",
    content: ServicesCatalog,
    visibleFor: (p) => isAccountAdmin(p),
  },

  // Services block — server (service-specific pages; roles are dynamic).
  // Инвентарь живёт на /servers (новая страница Server.tsx); из админ-каталога
  // делаем редирект, чтобы единственная точка входа была одна.
  {
    id: "services.server.inventory",
    label: "Серверы",
    hint: "server_service",
    icon: ServerIcon,
    block: "services",
    group: "server",
    content: () => createElement(Navigate, { to: "/servers", replace: true }),
    // backend `server_service` блокирует platform-admins (account_admin /
    // loging_admin) — это business-data сервиса. Прячем от них пункт.
    visibleFor: (p) => isDepAdmin(p) || hasServerServiceAdmin(p),
  },
  {
    id: "services.server.groups",
    label: "Группы серверов",
    icon: Layers,
    block: "services",
    group: "server",
    content: ServicesServerGroups,
    // Backend для групп серверов ещё не реализован — страница пока заглушка.
    // Вернём пункт, когда появится соответствующий endpoint в server_service.
    visibleFor: () => false,
  },
  {
    id: "services.server.permissions",
    label: "Матрица разрешений",
    hint: "RBAC server_service",
    icon: ShieldCheck,
    block: "services",
    group: "server",
    content: ServicesServerPermissions,
    // backend: `(permission, *, view)` — department_admin своего отдела или
    // server.admin. account_admin / loging_admin режет middleware.
    visibleFor: (p) => isDepAdmin(p) || hasServerServiceAdmin(p),
  },
  {
    id: "services.server.os_versions",
    label: "OS-версии",
    hint: "глобальный каталог версий ОС",
    icon: HardDrive,
    block: "services",
    group: "server",
    content: ServicesOsVersions,
    // Каталог глобальный, но CRUD идёт под action-матрицей server_service —
    // create/update/delete несёт server.admin (и dep_admin в своём отделе).
    // Platform-роли (account_admin / loging_admin) backend режет на 403, им
    // пункт не показываем; кнопки управления гейтятся внутри страницы.
    visibleFor: (p) => isDepAdmin(p) || hasServerServiceAdmin(p),
  },
  {
    id: "services.server.ignored_logins",
    label: "Игнорируемые логины",
    hint: "OS-логины вне ревизии · отдел",
    icon: EyeOff,
    block: "services",
    group: "server",
    content: ServicesIgnoredLogins,
    // Гейтится action `manage_ignored_logins` — dep_admin своего отдела или
    // server.admin. Platform-роли (account_admin / loging_admin) backend режет
    // на 403; пункт им не показываем.
    visibleFor: (p) => isDepAdmin(p) || hasServerServiceAdmin(p),
  },
  {
    id: "services.server.management_user",
    label: "Системная учётка",
    hint: "управляющая учётка · login + режимы ОС",
    icon: UserCog,
    block: "services",
    group: "server",
    content: ServicesManagementUser,
    // Конфиг управляющей учётки гейтится account_admin; смена login запускает
    // cutover на всех подготовленных серверах. Остальным backend ответит 403.
    visibleFor: (p) => isAccountAdmin(p),
  },

  // Services block — secret. Per-credential RoleACL / DeptGrant / UserACL.
  // secret_service dept-scoped: account_admin / loging_* без отдела backend
  // режет 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT — пункт им не показываем.
  {
    id: "services.secret.access",
    label: "Доступ к секретам",
    hint: "Role-ACL / Dept-Grant / User-ACL per-credential",
    icon: ShieldCheck,
    block: "services",
    group: "secret",
    content: ServicesSecretAccess,
    visibleFor: (p) => isDepAdmin(p) || hasSecretServiceAdmin(p),
  },

  // Services block — loging (service-specific pages; roles are dynamic).
  // Администрирование логирования (правила severity/suppress, retention,
  // severity-overrides) — только платформенный loging_admin. account_admin и
  // dep_admin видят аудит-обзор, но не настраивают логирование; loging_reader
  // читает журнал. Backend (require_admin) всё равно ответит остальным 403.
  {
    id: "services.loging.rules",
    label: "Правила алёртов",
    icon: AlertTriangle,
    block: "services",
    group: "loging",
    content: ServicesLogingRules,
    visibleFor: (p) => isLoggingAdmin(p),
  },
  {
    id: "services.loging.retention",
    label: "Retention",
    icon: Clock,
    block: "services",
    group: "loging",
    content: ServicesLogingRetention,
    visibleFor: (p) => isLoggingAdmin(p),
  },

  // Services block — security (PAT / OAuth2 / Docker / Lockout / Introspect / S2S / catalog)
  {
    id: "services.security.tokens",
    label: "Personal Access Tokens",
    hint: "PAT для CLI и автоматизации",
    icon: KeyRound,
    block: "services",
    group: "Безопасность",
    content: SecurityTokens,
    visibleFor: (p) => p.has_admin,
  },
  {
    id: "services.security.oauth2",
    label: "OAuth2 клиенты",
    hint: "внешние интеграции",
    icon: Unplug,
    block: "services",
    group: "Безопасность",
    content: SecurityOAuth2,
    visibleFor: (p) => p.has_admin,
  },
  {
    id: "services.security.docker",
    label: "Docker registry",
    hint: "доступ к registry, JWKS",
    icon: Container,
    block: "services",
    group: "Безопасность",
    content: SecurityDocker,
    visibleFor: (p) => p.has_admin,
  },
  {
    id: "services.security.lockout",
    label: "Lockout reset",
    hint: "снять блокировку user/bot/oauth",
    icon: LockOpen,
    block: "services",
    group: "Безопасность",
    content: SecurityLockout,
    // Управление lockout (просмотр/unlock/ban/политика) backend пускает только
    // account_admin. dep_admin раньше видел пункт и упирался в отбойное
    // сообщение — прячем его, чтобы видел только тот, кому реально доступно.
    visibleFor: (p) => isAccountAdmin(p),
  },
  {
    id: "services.security.introspect",
    label: "Introspect token",
    hint: "разобрать JWT/PAT/bot-token",
    icon: ScanSearch,
    block: "services",
    group: "Безопасность",
    content: SecurityIntrospect,
    visibleFor: (p) => p.has_admin,
  },
  {
    id: "services.security.service_access",
    label: "Service-to-service",
    hint: "проверка прав между сервисами",
    icon: ShieldCheck,
    block: "services",
    group: "Безопасность",
    content: SecurityServiceAccess,
    // Платформенная проверка S2S-доступа: dep_admin'у не нужна и не положена —
    // он администрирует только свой отдел. Оставляем account_admin и
    // сервис-админам.
    visibleFor: (p) => p.has_admin && !isDepAdmin(p),
  },
  {
    // Платформенный каталог всех сервисов — account_admin / сервис-админы.
    // dep_admin сюда не попадает: для него ниже отдельный пункт «Доступные
    // сервисы отдела» (тот же компонент, dept-scoped режим).
    id: "services.security.services",
    label: "Каталог сервисов",
    hint: "список known services",
    icon: Wrench,
    block: "services",
    group: "Безопасность",
    content: SecurityServices,
    visibleFor: (p) => p.has_admin && !isDepAdmin(p),
  },
  {
    id: "services.security.dept_services",
    label: "Доступные сервисы отдела",
    hint: "что подключено вашему отделу",
    icon: Wrench,
    block: "services",
    group: "Безопасность",
    content: SecurityServices,
    visibleFor: (p) => isDepAdmin(p),
  },
];

/**
 * `auth_service` уже разложен на отдельные admin-страницы
 * (users / departments / bots / platform_roles). Своих "ролей сервиса" в UI
 * он не получает — поэтому исключаем его из динамического списка.
 */
const ROLE_ITEM_EXCLUDED_SERVICES = new Set<string>([
  "auth_service",
  // worker_service не несёт ролей и не управляется из UI — server_worker это
  // taskiq-процесс без HTTP-API (cron/DLQ заданы кодом, мониторинг через K8s).
  "worker_service",
]);

/**
 * Per-service visibility predicate for the generated «Роли · <service>» entry.
 * Каждый сервис разрешает CRUD ролей account_admin (platform-wide) и любому
 * dep_admin (он управляет ролями в рамках своего отдела). Сервисы, у которых
 * исторически был дополнительный «service admin» (server / secret / worker /
 * loging), пускают и его — чтобы поведение совпадало с прежним хардкодом.
 */
function roleItemVisibility(serviceName: string): (p: Persona) => boolean {
  switch (serviceName) {
    case "server_service":
      return (p) =>
        isAccountAdmin(p) || isDepAdmin(p) || hasServerServiceAdmin(p);
    case "secret_service":
      return (p) =>
        isAccountAdmin(p) || isDepAdmin(p) || hasSecretServiceAdmin(p);
    case "loging_service":
      return (p) =>
        isAccountAdmin(p) || isDepAdmin(p) || isLoggingAdmin(p);
    default:
      // Новый сервис → роли видят account_admin и любой dep_admin.
      return (p) => isAccountAdmin(p) || isDepAdmin(p);
  }
}

/** Sub-section header in AdminMiddle. Known services have short labels; for
 * everything else — fall back to the backend `service_name` slug. */
function roleItemGroup(svc: Service): string {
  switch (svc.service_name) {
    case "server_service":
      return "server";
    case "secret_service":
      return "secret";
    case "loging_service":
      return "loging";
    default:
      return svc.service_name;
  }
}

function roleItemLabel(svc: Service): string {
  return `Роли · ${svc.service_name}`;
}

/**
 * Build the full admin catalogue: static items + a dynamic «Роли · …» entry
 * for each registered service (minus the ones we explicitly skip). The id
 * of every dynamic item is `services.<backend_service_name>.roles`, matching
 * what `ServicesCatalog.tsx` links to via `BACKEND_TO_ADMIN_ITEM` (and what
 * `ServiceRolesCard` understands as `serviceName` prop).
 */
export function buildAdminItems(services: Service[]): AdminItem[] {
  const dynamicRoles: AdminItem[] = services
    .filter((svc) => !ROLE_ITEM_EXCLUDED_SERVICES.has(svc.service_name))
    .map((svc) => {
      const backendName = svc.service_name;
      const title = roleItemLabel(svc);
      const Content: ComponentType = () =>
        createElement(ServiceRolesCard, {
          serviceName: backendName,
          title,
        });
      // Дружелюбное имя для devtools.
      (Content as { displayName?: string }).displayName =
        `ServiceRolesCard(${backendName})`;
      return {
        id: `services.${backendName}.roles`,
        label: roleItemLabel(svc),
        hint: svc.service_name,
        icon: ShieldCheck,
        block: "services" as AdminBlock,
        group: roleItemGroup(svc),
        content: Content,
        visibleFor: roleItemVisibility(backendName),
      };
    });

  return [...STATIC_ITEMS, ...dynamicRoles];
}

export function visibleItems(items: AdminItem[], persona: Persona): AdminItem[] {
  return items.filter((it) => it.visibleFor(persona));
}

export function itemById(
  items: AdminItem[],
  id: string | undefined,
): AdminItem | undefined {
  if (!id) return undefined;
  return items.find((it) => it.id === id);
}
