import type { Persona } from "@/types/persona";

export const PERSONAS: Persona[] = [
  {
    id: "bob",
    username: "bob",
    email: "bob@dbos.local",
    initials: "BO",
    display_name: "bob",
    dept_id: null,
    platform_role: "account_admin",
    service_roles: {},
    accessible_services: ["auth", "secret", "server", "worker", "logging", "config"],
    has_admin: true,
    tagline: "Глобальный аккаунт-админ — видит всё.",
  },
  {
    id: "alice",
    username: "alice",
    email: "alice@dbos.local",
    initials: "AL",
    display_name: "alice",
    dept_id: "core",
    platform_role: "dep_admin",
    service_roles: {},
    accessible_services: ["auth", "secret", "server", "worker", "logging", "config"],
    has_admin: true,
    tagline: "dep_admin одного отдела.",
  },
  {
    id: "carol",
    username: "carol",
    email: "carol@dbos.local",
    initials: "CA",
    display_name: "carol",
    dept_id: null,
    platform_role: "logging_admin",
    service_roles: {},
    accessible_services: ["logging", "config"],
    has_admin: true,
    tagline: "Полный доступ к аудиту, правилам и retention.",
  },
  {
    id: "dave",
    username: "dave",
    email: "dave@dbos.local",
    initials: "DA",
    display_name: "dave",
    dept_id: null,
    platform_role: "logging_reader",
    service_roles: {},
    accessible_services: ["logging"],
    has_admin: false,
    tagline: "Read-only доступ к аудит-событиям.",
  },
  {
    id: "erin",
    username: "erin",
    email: "erin@dbos.local",
    initials: "ER",
    display_name: "erin",
    dept_id: "core",
    platform_role: "logging_reader_dep",
    service_roles: {},
    accessible_services: ["logging"],
    has_admin: false,
    tagline: "Read-only аудит своего отдела (dept-scoped).",
  },
];

export const personaById = (id: string): Persona | undefined =>
  PERSONAS.find((p) => p.id === id);

export const DEFAULT_PERSONA_ID = "alice";
