import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { Shell } from "@/components/shell/Shell";
import { personaDeptId } from "@/lib/rbac";
import { ServiceRolesAccountAdmin } from "./ServiceRolesAccountAdmin";
import { ServiceRolesDepAdmin } from "./ServiceRolesDepAdmin";
import { ServiceRolesLivePanel } from "./ServiceRolesLivePanel";

/**
 * Persona-aware Service-roles dispatcher.
 * account_admin → platform-wide view; everyone else → dep-scoped.
 *
 * In live mode the mockup ports show fabricated роли (devops, auditor, ДТКК
 * и т.д.) which don't exist in the backend. Render the LivePanel by itself —
 * it talks to /service_roles directly and shows what auth_service actually
 * has.
 */
export function ServiceRoles() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
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
  if (persona.platform_role === "account_admin") return <ServiceRolesAccountAdmin />;
  return <ServiceRolesDepAdmin />;
}
