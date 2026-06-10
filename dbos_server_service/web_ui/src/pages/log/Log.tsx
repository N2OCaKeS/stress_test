import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";
import { LogLoggingAdmin } from "./LogLoggingAdmin";
import { LogLoggingReader } from "./LogLoggingReader";

/**
 * Persona-aware Log dispatcher.
 * In live mode loging_service browse UI is not wired yet — show a placeholder.
 */
export function Log() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="loging_service / events"
        service="loging_service (browse)"
        endpoints={[
          "GET  /loging/v1/events",
          "GET  /loging/v1/events/{id}",
          "GET  /loging/v1/facets",
        ]}
      />
    );
  }
  if (persona.platform_role === "logging_reader") return <LogLoggingReader />;
  return <LogLoggingAdmin />;
}
