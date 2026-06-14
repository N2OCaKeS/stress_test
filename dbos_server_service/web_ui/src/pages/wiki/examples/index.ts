import type { ApiSection, ApiFlow } from "./types";
import { BASICS } from "./basics";
import { AUTH_SESSION } from "./auth/01_auth_session";
import { USERS } from "./auth/02_users";
import { DEPTS_SERVICES_ROLES } from "./auth/03_departments_services_roles";
import { TOKENS_BOTS } from "./auth/04_tokens_bots";
import { GROUPS } from "./auth/05_groups";
import { AUTHZ_OAUTH_DOCKER } from "./auth/06_authz_oauth_docker";
import { AUTH_FLOWS as AUTH_FLOWS_DATA } from "./auth/flows";

export const AUTH_SECTIONS: ApiSection[] = [
  BASICS,
  AUTH_SESSION,
  USERS,
  DEPTS_SERVICES_ROLES,
  TOKENS_BOTS,
  GROUPS,
  AUTHZ_OAUTH_DOCKER,
];

export const AUTH_FLOWS: ApiFlow[] = AUTH_FLOWS_DATA;

export type { ApiSection, ApiFlow } from "./types";
