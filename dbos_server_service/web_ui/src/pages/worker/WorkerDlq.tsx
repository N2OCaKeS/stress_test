import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";
import { WorkerDlqAccountAdmin } from "./WorkerDlqAccountAdmin";
import { WorkerDlqDepAdmin } from "./WorkerDlqDepAdmin";

/**
 * Persona-aware Worker DLQ dispatcher.
 * In live mode server_worker is not wired to UI yet — show a placeholder.
 */
export function WorkerDlq() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="server_worker / dlq"
        service="server_worker (DLQ)"
        endpoints={[
          "GET  /worker/v1/dlq",
          "POST /worker/v1/dlq/{id}/requeue",
          "POST /worker/v1/dlq/{id}/discard",
        ]}
      />
    );
  }
  if (persona.platform_role === "account_admin") return <WorkerDlqAccountAdmin />;
  return <WorkerDlqDepAdmin />;
}
