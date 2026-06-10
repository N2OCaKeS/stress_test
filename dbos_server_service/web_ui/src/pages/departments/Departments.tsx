import { Navigate } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin } from "@/lib/rbac";
import { DepartmentsAccountAdmin } from "./DepartmentsAccountAdmin";

/**
 * Departments — visible only to account_admin. Other personas are
 * redirected to /home.
 */
export function Departments() {
  const { persona } = usePersona();
  if (isPlatformWideAdmin(persona)) return <DepartmentsAccountAdmin />;
  return <Navigate to="/home" replace />;
}
