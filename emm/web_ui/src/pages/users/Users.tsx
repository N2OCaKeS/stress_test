import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin } from "@/lib/rbac";
import { UsersAccountAdmin } from "./UsersAccountAdmin";
import { UsersDepAdmin } from "./UsersDepAdmin";

/**
 * Persona-aware Users dispatcher.
 * account_admin → cluster-wide variant; everyone else → dept-scoped.
 */
export function Users() {
  const { persona } = usePersona();
  if (isPlatformWideAdmin(persona)) return <UsersAccountAdmin />;
  return <UsersDepAdmin />;
}
