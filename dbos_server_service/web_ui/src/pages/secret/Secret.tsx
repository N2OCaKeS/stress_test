import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";
import { SecretAccountAdmin } from "./SecretAccountAdmin";
import { SecretDepAdmin } from "./SecretDepAdmin";

/**
 * Persona-aware Secret dispatcher.
 * account_admin gets the cluster-wide view; everyone else uses the dept-scoped one.
 * In live mode secret_service is not wired to UI yet — show a placeholder.
 */
export function Secret() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="secret_service / credentials"
        service="secret_service"
        endpoints={[
          "GET  /secret/v1/credentials",
          "POST /secret/v1/credentials",
          "POST /secret/v1/credentials/{id}/reveal",
          "POST /secret/v1/credentials/{id}/rotate",
        ]}
      />
    );
  }
  if (persona.platform_role === "account_admin") return <SecretAccountAdmin />;
  return <SecretDepAdmin />;
}
