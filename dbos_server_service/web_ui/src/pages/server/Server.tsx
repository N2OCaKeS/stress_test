import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";
import { ServerAccountAdmin } from "./ServerAccountAdmin";
import { ServerDepAdmin } from "./ServerDepAdmin";

/**
 * Persona-aware Server dispatcher.
 * account_admin gets the cluster-wide view; everyone else uses the dept-scoped one.
 * In live mode server_service is not wired to UI yet — show a placeholder.
 */
export function Server() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="server_service / servers"
        service="server_service"
        endpoints={[
          "GET  /server/v1/servers",
          "GET  /server/v1/servers/{id}",
          "POST /server/v1/servers/{id}/power",
        ]}
      />
    );
  }
  if (persona.platform_role === "account_admin") return <ServerAccountAdmin />;
  return <ServerDepAdmin />;
}
