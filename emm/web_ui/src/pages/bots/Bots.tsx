import { usePersona } from "@/contexts/PersonaContext";
import { BotsAccountAdmin } from "./BotsAccountAdmin";
import { BotsDepAdmin } from "./BotsDepAdmin";

/**
 * Persona-aware Bots dispatcher.
 * account_admin → cluster-wide; everyone else → dep-scoped view.
 */
export function Bots() {
  const { persona } = usePersona();
  if (persona.platform_role === "account_admin") return <BotsAccountAdmin />;
  return <BotsDepAdmin />;
}
