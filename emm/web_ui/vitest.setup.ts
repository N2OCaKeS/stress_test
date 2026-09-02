import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// Tests render pages that use PersonaContext without wrapping in AuthProvider.
// Force mock-auth mode so PersonaContext returns the mock persona fallback
// (alice by default) instead of EMPTY_PERSONA.
import.meta.env.VITE_USE_MOCK_AUTH = "true";

configure({ asyncUtilTimeout: 5_000 });
