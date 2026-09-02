import { AdminHub } from "./AdminHub";

/**
 * Thin dispatcher: the new admin is a single Shell + AdminMiddle + Workzone.
 * RBAC is enforced inside AdminHub via the central catalog.
 */
export function Admin() {
  return <AdminHub />;
}
