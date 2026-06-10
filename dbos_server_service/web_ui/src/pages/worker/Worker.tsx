import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { NotWiredPlaceholder } from "@/pages/Placeholder";
import { WorkerAccountAdmin } from "./WorkerAccountAdmin";
import { WorkerDepAdmin } from "./WorkerDepAdmin";

/**
 * Persona-aware Worker (tasks) dispatcher.
 * account_admin → cluster-wide, by status
 * dep_admin / default → scope my/my_dep/cross_dep
 * In live mode server_worker is not wired to UI yet — show a placeholder.
 */
export function Worker() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return (
      <NotWiredPlaceholder
        breadcrumb="server_worker / tasks"
        service="server_worker"
        endpoints={[
          "GET  /worker/v1/tasks",
          "GET  /worker/v1/tasks/{id}",
          "POST /worker/v1/tasks/{id}/retry",
          "GET  /worker/v1/dlq",
        ]}
      />
    );
  }
  if (persona.platform_role === "account_admin") return <WorkerAccountAdmin />;
  return <WorkerDepAdmin />;
}
