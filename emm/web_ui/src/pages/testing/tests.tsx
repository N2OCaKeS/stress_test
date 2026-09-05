/**
 * Раздел «Тесты» — каталог тестовых наборов.
 *
 * В allta_app это `testname_columns`: плоский маппинг полного имени набора
 * на короткий код (использовался в отчётах и в СТП). Реальных наборов там
 * порядка полусотни — filesystem-варианты, PostgreSQL/Tantor, FreeIPA,
 * Apache, сеть, audit/syslog, ceph, docker, стресс-тесты на segfault/утечки
 * памяти и т.д. Здесь — представительная выборка по тем же категориям,
 * без претензии на побайтовое соответствие полному списку.
 */
import { useMemo, useState } from "react";
import {
  CheckCircle2,
  Cog,
  Database,
  FileText,
  FlaskConical,
  FolderTree,
  KeyRound,
  Network,
  Search,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { Stat, TextStatusBadge, type BadgeKind } from "./_shared";
import { Button } from "@/components/ui/Button";

export type TestCategory = "filesystem" | "database" | "network" | "security" | "stress" | "other";
export type TestReadiness = "ready" | "draft" | "blocked";

export interface CatalogTest {
  code: string;
  fullName: string;
  category: TestCategory;
  owner: string;
  params: string;
  readiness: TestReadiness;
}

const CATEGORY_META: Record<TestCategory, { label: string; icon: LucideIcon; badge: BadgeKind }> = {
  filesystem: { label: "Файловые системы", icon: FolderTree, badge: "accent" },
  database: { label: "СУБД", icon: Database, badge: "ok" },
  network: { label: "Сеть", icon: Network, badge: "warn" },
  security: { label: "Безопасность", icon: KeyRound, badge: "danger" },
  stress: { label: "Стресс/деградация", icon: FlaskConical, badge: "warn" },
  other: { label: "Прочее", icon: Cog, badge: "accent" },
};

export const TEST_CATALOG: CatalogTest[] = [
  { code: "FS-EXT4-FILL", fullName: "filesystem / ext4 fill+remove cycle", category: "filesystem", owner: "QA Infra", params: "device=/dev/nvme0n1, cycles=50", readiness: "ready" },
  { code: "FS-XFS-FILL", fullName: "filesystem / xfs fill+remove cycle", category: "filesystem", owner: "QA Infra", params: "device=/dev/sda1, cycles=50", readiness: "ready" },
  { code: "FS-BTRFS-SNAP", fullName: "filesystem / btrfs snapshot stress", category: "filesystem", owner: "QA Infra", params: "snapshots=200", readiness: "ready" },
  { code: "FS-FIO-RANDRW", fullName: "filesystem / fio randrw mixed", category: "filesystem", owner: "QA Infra", params: "blocksize=4k, jobs=8", readiness: "ready" },
  { code: "FS-FIO-SEQ", fullName: "filesystem / fio sequential read+write", category: "filesystem", owner: "QA Infra", params: "blocksize=1m, jobs=4", readiness: "ready" },
  { code: "FS-FSMARK", fullName: "filesystem / fs_mark many small files", category: "filesystem", owner: "QA Infra", params: "files=1000000", readiness: "ready" },
  { code: "FS-QUOTA", fullName: "filesystem / disk quota enforcement", category: "filesystem", owner: "QA Infra", params: "quota=10G", readiness: "draft" },
  { code: "DB-PG-TPCC", fullName: "database / PostgreSQL TPC-C", category: "database", owner: "Backend QA", params: "warehouses=50, duration=30m", readiness: "ready" },
  { code: "DB-PG-BASEBACKUP", fullName: "database / PostgreSQL base backup+restore", category: "database", owner: "Backend QA", params: "size=20G", readiness: "ready" },
  { code: "DB-TANTOR-REPL", fullName: "database / Tantor SE репликация", category: "database", owner: "Backend QA", params: "replicas=2", readiness: "ready" },
  { code: "DB-TANTOR-UPGRADE", fullName: "database / Tantor SE major upgrade", category: "database", owner: "Backend QA", params: "from=15, to=16", readiness: "blocked" },
  { code: "DB-SYSBENCH-OLTP", fullName: "database / sysbench oltp read-write", category: "database", owner: "Backend QA", params: "tables=10, threads=16", readiness: "ready" },
  { code: "NET-IPERF3", fullName: "network / iperf3 throughput", category: "network", owner: "Platform", params: "duration=60s, streams=4", readiness: "ready" },
  { code: "NET-APACHE-AB", fullName: "network / Apache ab benchmark", category: "network", owner: "Platform", params: "requests=100000, concurrency=50", readiness: "ready" },
  { code: "NET-DOCKER-REGISTRY", fullName: "network / docker registry push-pull", category: "network", owner: "Platform", params: "images=20", readiness: "ready" },
  { code: "NET-CEPH-RADOS", fullName: "network / ceph rados bench", category: "network", owner: "Platform", params: "pool=testbench, size=4G", readiness: "draft" },
  { code: "SEC-FREEIPA-JOIN", fullName: "security / FreeIPA domain join", category: "security", owner: "QA Infra", params: "realm=ASTRALINUX.RU", readiness: "ready" },
  { code: "SEC-FREEIPA-SUDO", fullName: "security / FreeIPA sudo rules", category: "security", owner: "QA Infra", params: "rules=12", readiness: "ready" },
  { code: "SEC-AUDIT-SYSLOG", fullName: "security / audit → syslog forwarding", category: "security", owner: "QA Infra", params: "rules=default", readiness: "ready" },
  { code: "SEC-IPTABLES", fullName: "security / iptables ruleset apply", category: "security", owner: "QA Infra", params: "ruleset=hardened", readiness: "ready" },
  { code: "SEC-OPENSSL-SPEED", fullName: "security / OpenSSL speed aes-256-gcm", category: "security", owner: "QA Infra", params: "algo=aes-256-gcm", readiness: "ready" },
  { code: "STR-SEGFAULT-FUZZ", fullName: "stress / segfault fuzz loop", category: "stress", owner: "Kernel QA", params: "iterations=100000", readiness: "ready" },
  { code: "STR-MEMLEAK-SOAK", fullName: "stress / memory leak soak 24h", category: "stress", owner: "Kernel QA", params: "duration=24h", readiness: "ready" },
  { code: "STR-OOM-KILLER", fullName: "stress / oom-killer behaviour", category: "stress", owner: "Kernel QA", params: "target_mem=90%", readiness: "draft" },
  { code: "STR-LINPACK", fullName: "stress / Linpack Xtreme matrix solve", category: "stress", owner: "Kernel QA", params: "size=20000", readiness: "ready" },
  { code: "STR-PARSEC", fullName: "stress / PARSEC 3.0 suite", category: "stress", owner: "Kernel QA", params: "workloads=blackscholes,canneal,streamcluster", readiness: "ready" },
  { code: "OTH-UNIXBENCH", fullName: "other / UnixBench 5.1.3 full", category: "other", owner: "QA Infra", params: "mode=full", readiness: "ready" },
  { code: "OTH-7ZIP", fullName: "other / 7-Zip 24.08 compression benchmark", category: "other", owner: "QA Infra", params: "dict=64m", readiness: "ready" },
  { code: "OTH-GUI-SMOKE", fullName: "other / GUI smoke (Workstation)", category: "other", owner: "QA Infra", params: "profile=default", readiness: "blocked" },
  { code: "OTH-VM-SNAPSHOT", fullName: "other / VM snapshot rollback", category: "other", owner: "Platform", params: "hub=vms-hub-1", readiness: "ready" },
];

export function TestsWorkzone() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<TestCategory | "all">("all");

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return TEST_CATALOG.filter((test) => {
      if (category !== "all" && test.category !== category) return false;
      if (!term) return true;
      return test.code.toLowerCase().includes(term) || test.fullName.toLowerCase().includes(term);
    });
  }, [search, category]);

  const totals = useMemo(
    () => ({
      total: TEST_CATALOG.length,
      ready: TEST_CATALOG.filter((t) => t.readiness === "ready").length,
      draft: TEST_CATALOG.filter((t) => t.readiness === "draft").length,
      blocked: TEST_CATALOG.filter((t) => t.readiness === "blocked").length,
    }),
    [],
  );

  const categoryCounts = useMemo(() => {
    const map = new Map<TestCategory, number>();
    for (const test of TEST_CATALOG) map.set(test.category, (map.get(test.category) ?? 0) + 1);
    return map;
  }, []);

  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Всего наборов" value={String(totals.total)} icon={FileText} />
        <Stat title="Готовы к запуску" value={String(totals.ready)} icon={CheckCircle2} kind="ok" />
        <Stat title="Требуют параметров" value={String(totals.draft)} icon={Cog} kind="warn" />
        <Stat title="Заблокированы" value={String(totals.blocked)} icon={ShieldCheck} kind="danger" />
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1 min-w-[220px]">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder="Поиск по коду или имени…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button
          type="button"
          size="sm"
          variant={category === "all" ? "primary" : "default"}
          onClick={() => setCategory("all")}
        >
          Все · {TEST_CATALOG.length}
        </Button>
        {(Object.keys(CATEGORY_META) as TestCategory[]).map((cat) => {
          const meta = CATEGORY_META[cat];
          const Icon = meta.icon;
          return (
            <Button
              key={cat}
              type="button"
              size="sm"
              variant={category === cat ? "primary" : "default"}
              className="inline-flex items-center gap-1.5"
              onClick={() => setCategory(cat)}
            >
              <Icon className="w-3.5 h-3.5" />
              {meta.label} · {categoryCounts.get(cat) ?? 0}
            </Button>
          );
        })}
      </div>

      <div className="surface border border-token rounded overflow-hidden">
        <div className="border-b border-token p-3 flex items-center gap-2">
          <FileText className="w-4 h-4 text-accent" />
          <div className="text-sm font-medium">Каталог тестов · {filtered.length}</div>
        </div>
        <div className="overflow-auto max-h-[560px]">
          <table className="mini">
            <thead>
              <tr>
                <th>Код</th>
                <th>Полное имя</th>
                <th>Категория</th>
                <th>Владелец-контур</th>
                <th>Параметры запуска</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((test) => {
                const meta = CATEGORY_META[test.category];
                return (
                  <tr key={test.code}>
                    <td className="mono">{test.code}</td>
                    <td className="truncate max-w-[280px]" title={test.fullName}>{test.fullName}</td>
                    <td>
                      <span className={`badge badge-${meta.badge}`}>{meta.label}</span>
                    </td>
                    <td>{test.owner}</td>
                    <td className="mono text-xs text-dim truncate max-w-[220px]" title={test.params}>{test.params}</td>
                    <td><TextStatusBadge value={test.readiness} /></td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center text-dim py-6">Нет тестов по выбранным фильтрам</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
