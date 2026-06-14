import { usePersona } from "@/contexts/PersonaContext";
import { HomeAccountAdmin } from "./HomeAccountAdmin";
import { HomeDepAdmin } from "./HomeDepAdmin";
import { HomeLoggingAdmin } from "./HomeLoggingAdmin";
import { HomeLoggingReader } from "./HomeLoggingReader";

/**
 * Persona-aware Home dispatcher.
 * Each platform role owns a tailored Home variant.
 */
export function Home() {
  const { persona } = usePersona();
  switch (persona.platform_role) {
    case "account_admin":
      return <HomeAccountAdmin />;
    case "dep_admin":
      return <HomeDepAdmin />;
    case "logging_admin":
      return <HomeLoggingAdmin />;
    case "logging_reader":
      return <HomeLoggingReader />;
    default:
      return <HomeDepAdmin />;
  }
}
