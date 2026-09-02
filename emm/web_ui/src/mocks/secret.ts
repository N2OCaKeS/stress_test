/* secret_service mock: 70 credentials. */

export type CredentialKind = "password" | "token" | "ssh_key" | "cert";

export interface MockCredential {
  id: string;
  name: string;
  kind: CredentialKind;
  dept_id: string;
  owner: string;
  created_at: string;
  expires_at: string | null;
  last_revealed_at: string | null;
  reveal_count_24h: number;
  status: "active" | "expiring" | "expired" | "rotating";
}

const NAMES = [
  "prod-postgres-master", "prod-postgres-replica", "stage-redis-cluster",
  "grafana-admin", "kibana-svc", "k8s-sa-deploy", "vault-root", "ipmi-rack-a01",
  "ipmi-rack-a02", "ssh-bastion-eu", "ssh-bastion-us", "dockerhub-pull",
  "gitea-bot", "ldap-bind", "smtp-relay", "ssl-wildcard-dbos", "nginx-cache",
  "minio-admin", "rabbitmq-svc", "mysql-replica", "argocd-svc", "consul-acl",
  "etcd-root", "prom-scrape", "alertmanager-svc", "loki-svc", "tempo-svc",
];
const KINDS: CredentialKind[] = ["password", "token", "ssh_key", "cert"];
const DEPTS = ["core", "dev", "infra", "ops"];

function genCredentials(): MockCredential[] {
  const out: MockCredential[] = [];
  for (let i = 0; i < 70; i++) {
    const name = i < NAMES.length ? NAMES[i] : `cred-${String(i + 1).padStart(3, "0")}`;
    const kind = KINDS[i % KINDS.length];
    const dept = DEPTS[i % DEPTS.length];
    const expiringIn = (i % 9) - 2;
    let status: MockCredential["status"] = "active";
    if (expiringIn < 0) status = "expired";
    else if (expiringIn < 2) status = "expiring";
    else if (i % 17 === 0) status = "rotating";
    out.push({
      id: `cred-${String(i + 1).padStart(3, "0")}`,
      name,
      kind,
      dept_id: dept,
      owner: i % 3 === 0 ? "alice" : i % 3 === 1 ? "igor" : "pavel",
      created_at: `2025-${String(((i % 12) + 1)).padStart(2, "0")}-15T12:00:00Z`,
      expires_at: kind === "ssh_key" ? null : `2026-${String(((i % 12) + 6) % 12 + 1).padStart(2, "0")}-15T12:00:00Z`,
      last_revealed_at: i % 5 === 0 ? null : `2026-06-${String(1 + (i % 9)).padStart(2, "0")}T10:00:00Z`,
      reveal_count_24h: i % 6,
      status,
    });
  }
  return out;
}

export const CREDENTIALS: MockCredential[] = genCredentials();
