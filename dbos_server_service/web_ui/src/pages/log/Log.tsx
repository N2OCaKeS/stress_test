import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { LogLoggingAdmin } from "./LogLoggingAdmin";
import { LogLoggingReader } from "./LogLoggingReader";
import { LogEventsLive } from "./LogEventsLive";

/**
 * Persona-aware Log dispatcher.
 *
 * Mock-режим показывает дизайн-порты (carol/dave). Live-режим тянет журнал
 * аудита из loging_service через `LogEventsLive` (read для loging_admin /
 * loging_reader).
 */
export function Log() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  if (!mockMode) {
    return <LogEventsLive />;
  }
  if (persona.platform_role === "logging_reader") return <LogLoggingReader />;
  return <LogLoggingAdmin />;
}
