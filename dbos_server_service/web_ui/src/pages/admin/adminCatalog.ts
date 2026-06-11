import { createElement, type ComponentType } from "react";
import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  Building2,
  Calendar,
  Clock,
  Cog,
  Container,
  Database,
  FileText,
  Inbox,
  KeyRound,
  Layers,
  LayoutTemplate,
  LockOpen,
  RotateCw,
  ScanSearch,
  ServerIcon,
  ShieldCheck,
  Unplug,
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
import { ServicesServerInventory } from "./services/ServicesServerInventory";
import { ServicesServerGroups } from "./services/ServicesServerGroups";
import { ServicesSecretSecrets } from "./services/ServicesSecretSecrets";
import { ServicesSecretPolicies } from "./services/ServicesSecretPolicies";
import { ServicesSecretTemplates } from "./services/ServicesSecretTemplates";
import { ServicesLogingRules } from "./services/ServicesLogingRules";
import { ServicesLogingRetention } from "./services/ServicesLogingRetention";
import { ServicesWorkerInventory } from "./services/ServicesWorkerInventory";
import { ServicesWorkerDlq } from "./services/ServicesWorkerDlq";
import { ServicesWorkerCron } from "./services/ServicesWorkerCron";
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
const isLoggingReader = (p: Persona) => p.platform_role === "logging_reader";
/* Service-role checks — not platform roles, just facts about per-service
 * grants. UI uses them to surface service-scoped admin tools (e.g. secret
 * templates / policies) to whoever has admin on that service. */
const hasSecretServiceAdmin = (p: Persona) => p.service_roles?.secret === "admin";
const hasServerServiceAdmin = (p: Persona) => p.service_roles?.server === "admin";
const hasWorkerServiceAdmin = (p: Persona) => p.service_roles?.worker === "admin";

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
      isAccountAdmin(p) ||
      isDepAdmin(p) ||
      isLoggingAdmin(p) ||
      isLoggingReader(p),
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
    visibleFor: (p) =>
      isAccountAdmin(p) || isDepAdmin(p),
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

  // Services block — server (service-specific pages; roles are dynamic)
  {
    id: "services.server.inventory",
    label: "Серверы",
    hint: "server_service",
    icon: ServerIcon,
    block: "services",
    group: "server",
    content: ServicesServerInventory,
    visibleFor: (p) =>
      isAccountAdmin(p) ||
      isDepAdmin(p) ||
      hasServerServiceAdmin(p),
  },
  {
    id: "services.server.groups",
    label: "Группы серверов",
    icon: Layers,
    block: "services",
    group: "server",
    content: ServicesServerGroups,
    visibleFor: (p) => isAccountAdmin(p) || hasServerServiceAdmin(p),
  },

  // Services block — secret (service-specific pages; roles are dynamic)
  {
    id: "services.secret.secrets",
    label: "Секреты",
    hint: "secret_service",
    icon: KeyRound,
    block: "services",
    group: "secret",
    content: ServicesSecretSecrets,
    visibleFor: (p) =>
      isAccountAdmin(p) ||
      isDepAdmin(p) ||
      hasSecretServiceAdmin(p),
  },
  {
    id: "services.secret.policies",
    label: "Политики ротации",
    icon: RotateCw,
    block: "services",
    group: "secret",
    content: ServicesSecretPolicies,
    visibleFor: (p) => isAccountAdmin(p) || hasSecretServiceAdmin(p),
  },
  {
    id: "services.secret.templates",
    label: "Шаблоны секретов",
    icon: LayoutTemplate,
    block: "services",
    group: "secret",
    content: ServicesSecretTemplates,
    visibleFor: (p) => isAccountAdmin(p) || hasSecretServiceAdmin(p),
  },

  // Services block — loging (service-specific pages; roles are dynamic)
  {
    id: "services.loging.rules",
    label: "Правила алёртов",
    icon: AlertTriangle,
    block: "services",
    group: "loging",
    content: ServicesLogingRules,
    visibleFor: (p) =>
      isAccountAdmin(p) ||
      isLoggingAdmin(p) ||
      isLoggingReader(p),
  },
  {
    id: "services.loging.retention",
    label: "Retention",
    icon: Clock,
    block: "services",
    group: "loging",
    content: ServicesLogingRetention,
    visibleFor: (p) => isAccountAdmin(p) || isLoggingAdmin(p),
  },

  // Services block — worker (service-specific pages; roles are dynamic)
  {
    id: "services.worker.inventory",
    label: "Воркеры",
    hint: "server_worker pods",
    icon: Cog,
    block: "services",
    group: "worker",
    content: ServicesWorkerInventory,
    visibleFor: (p) => isAccountAdmin(p) || hasWorkerServiceAdmin(p),
  },
  {
    id: "services.worker.dlq",
    label: "DLQ-политики",
    icon: Inbox,
    block: "services",
    group: "worker",
    content: ServicesWorkerDlq,
    visibleFor: (p) => isAccountAdmin(p) || hasWorkerServiceAdmin(p),
  },
  {
    id: "services.worker.cron",
    label: "Cron-задачи",
    icon: Calendar,
    block: "services",
    group: "worker",
    content: ServicesWorkerCron,
    visibleFor: (p) => isAccountAdmin(p) || hasWorkerServiceAdmin(p),
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
    visibleFor: (p) => p.has_admin,
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
    visibleFor: (p) => p.has_admin,
  },
  {
    id: "services.security.services",
    label: "Каталог сервисов",
    hint: "список known services",
    icon: Wrench,
    block: "services",
    group: "Безопасность",
    content: SecurityServices,
    visibleFor: (p) => p.has_admin,
  },
];

/**
 * `auth_service` уже разложен на отдельные admin-страницы
 * (users / departments / bots / platform_roles). Своих "ролей сервиса" в UI
 * он не получает — поэтому исключаем его из динамического списка.
 */
const ROLE_ITEM_EXCLUDED_SERVICES = new Set<string>(["auth_service"]);

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
    case "worker_service":
    case "server_worker":
      return (p) =>
        isAccountAdmin(p) || isDepAdmin(p) || hasWorkerServiceAdmin(p);
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
    case "worker_service":
    case "server_worker":
      return "worker";
    case "loging_service":
      return "loging";
    default:
      return svc.service_name;
  }
}

function roleItemLabel(svc: Service): string {
  const display = svc.display_name?.trim();
  return `Роли · ${display && display.length > 0 ? display : svc.service_name}`;
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
