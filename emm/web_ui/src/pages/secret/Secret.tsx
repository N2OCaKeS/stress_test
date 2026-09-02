import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { SecretAccountAdmin } from "./SecretAccountAdmin";
import { SecretDepAdmin } from "./SecretDepAdmin";
import { SecretLive } from "./SecretLive";

/**
 * Persona-aware Secret dispatcher.
 *
 * Live-режим тянет secret_service напрямую (SecretLive — list/create/reveal/
 * delete/recover + read-only ACL/dept-grants). Mock-режим использует
 * статичные account_admin / dept-scoped макеты.
 */
export function Secret() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) return <SecretLive />;
  if (persona.platform_role === "account_admin") return <SecretAccountAdmin />;
  return <SecretDepAdmin />;
}
