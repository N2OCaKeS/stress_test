/* server_service mock: 90 servers. */

export type ServerStatus = "up" | "down" | "maintenance" | "unreachable";

export interface MockServer {
  id: string;
  hostname: string;
  ip: string;
  bmc_ip: string;
  dept_id: string;
  os: string;
  status: ServerStatus;
  last_seen: string;
  rack: string;
  power_state: "on" | "off" | "unknown";
}

const OSES = [
  "Astra Linux SE 1.7",
  "Astra Linux SE 1.8",
  "Astra Linux CE 2.12",
  "Ubuntu 22.04",
  "Debian 12",
];
const DEPTS = ["core", "dev", "infra", "ops"];

function genServers(): MockServer[] {
  const out: MockServer[] = [];
  for (let i = 0; i < 90; i++) {
    const num = String(i + 1).padStart(2, "0");
    const rack = `rack-${String.fromCharCode(65 + Math.floor(i / 10))}${(i % 10) + 1}`;
    let status: ServerStatus = "up";
    if (i % 23 === 0) status = "down";
    else if (i % 17 === 0) status = "maintenance";
    else if (i % 31 === 0) status = "unreachable";
    out.push({
      id: `srv-${num}`,
      hostname: `srv-node-${num}`,
      ip: `10.177.${100 + (i % 4)}.${20 + i}`,
      bmc_ip: `10.177.${110 + (i % 4)}.${20 + i}`,
      dept_id: DEPTS[i % DEPTS.length],
      os: OSES[i % OSES.length],
      status,
      last_seen: `2026-06-10T${String(i % 24).padStart(2, "0")}:${String((i * 7) % 60).padStart(2, "0")}:00Z`,
      rack,
      power_state: status === "up" || status === "maintenance" ? "on" : status === "down" ? "off" : "unknown",
    });
  }
  return out;
}

export const SERVERS: MockServer[] = genServers();
