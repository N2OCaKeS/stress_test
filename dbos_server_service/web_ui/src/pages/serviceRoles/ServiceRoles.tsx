import { usePersona } from "@/contexts/PersonaContext";
import { Shell } from "@/components/shell/Shell";
import { personaDeptId } from "@/lib/rbac";
import { ServiceRolesLivePanel } from "./ServiceRolesLivePanel";

/**
 * Persona-aware Service-roles screen. LivePanel talks to /service_roles
 * directly; account_admin scopes any department, everyone else is pinned to
 * their own.
 */
export function ServiceRoles() {
  const { persona } = usePersona();
  const deptId = personaDeptId(persona) ?? "";
  return (
    <Shell breadcrumb="auth_service / service-roles">
      <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-y-auto">
        <ServiceRolesLivePanel
          departmentId={deptId}
          canCreate={persona.platform_role === "account_admin"}
          scopeNote={
            persona.platform_role === "account_admin"
              ? "account_admin · scope любой отдел; переключите selector"
              : `scope: ${deptId || "—"}`
          }
        />
      </div>
    </Shell>
  );
}
