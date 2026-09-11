/**
 * Раздел «Тесты» — каталог тестовых наборов `testing_service` (§2.2, §3.2
 * плана миграции). Живой каталог (`TestsWorkzone`) вайрен на реальный backend
 * (`test_definitions`/`test_command_args`/`global_variables`), включая
 * UI-конструктор команды теста.
 *
 * `TEST_CATALOG`/`CatalogTest`/`TestCategory`/`TestReadiness` ниже —
 * оставлены как есть (тот же демо-набор, что был в мокапе) исключительно ради
 * обратной совместимости: их всё ещё импортируют `runs.tsx`/`debug.tsx`
 * (другая волна той же миграции, редактируется параллельно). `TestsWorkzone`
 * их больше не использует. Как только оба файла перейдут на реальный
 * `listTestDefinitions`, этот блок можно удалить целиком.
 */
import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cog,
  Copy,
  Database,
  FileText,
  FlaskConical,
  FolderTree,
  KeyRound,
  Loader2,
  Network,
  Pencil,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  Variable,
  type LucideIcon,
} from "lucide-react";
import { Stat, TextStatusBadge, type BadgeKind } from "./_shared";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Dropdown } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import {
  createTestDefinition,
  deleteTestDefinition,
  listTestDefinitions,
  updateTestDefinition,
} from "@/api/testing/testDefinitions";
import {
  copyTestCommandArgs,
  createTestCommandArg,
  deleteTestCommandArg,
  listTestCommandArgs,
  updateTestCommandArg,
} from "@/api/testing/testCommandArgs";
import {
  createGlobalVariable,
  deleteGlobalVariable,
  getGlobalVariableChoices,
  listGlobalVariables,
  updateGlobalVariable,
} from "@/api/testing/global_variables";
import { listTestStands } from "@/api/testing/testStands";
import type {
  CommandArgKind,
  GlobalVariable,
  GlobalVariableCreateRequest,
  GlobalVariableSource,
  GlobalVariableValueType,
  TestCommandArg,
  TestDefinition,
  TestDefinitionCreateRequest,
  TestStand,
} from "@/api/testing/types";

// ── legacy demo-каталог, оставлен только для runs.tsx/debug.tsx (см. шапку) ─

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

// ── живой каталог ────────────────────────────────────────────────────────

const CATEGORY_VISUAL: Record<string, { label: string; icon: LucideIcon; badge: BadgeKind }> = {
  filesystem: { label: "Файловые системы", icon: FolderTree, badge: "accent" },
  database: { label: "СУБД", icon: Database, badge: "ok" },
  network: { label: "Сеть", icon: Network, badge: "warn" },
  security: { label: "Безопасность", icon: KeyRound, badge: "danger" },
  stress: { label: "Стресс/деградация", icon: FlaskConical, badge: "warn" },
  other: { label: "Прочее", icon: Cog, badge: "accent" },
};

/** `category` в `test_definitions` — имя git-ветки монорепо (её же клонирует starter.sh при запуске), свободная строка, не enum. Известные значения красим по каталогу выше, неизвестные — тем же нейтральным видом, что и "Прочее". */
function categoryVisual(category: string | null | undefined): { label: string; icon: LucideIcon; badge: BadgeKind } {
  const key = category ?? "other";
  return CATEGORY_VISUAL[key] ?? { label: key, icon: Cog, badge: "accent" };
}

const READINESS_OPTIONS = [
  { value: "ready", label: "ready" },
  { value: "draft", label: "draft" },
  { value: "blocked", label: "blocked" },
];

const SOURCE_OPTIONS: { value: GlobalVariableSource; label: string }[] = [
  { value: "launch_context", label: "launch_context — из контекста запуска" },
  { value: "static", label: "static — статическое значение" },
  { value: "per_test_override", label: "per_test_override — переопределяется тестом" },
  { value: "secret_service", label: "secret_service — из secret_service" },
];

const VALUE_TYPE_OPTIONS: { value: GlobalVariableValueType; label: string }[] = [
  { value: "string", label: "string" },
  { value: "integer", label: "integer" },
  { value: "boolean", label: "boolean" },
];

export function TestsWorkzone() {
  const toast = useToast();
  const { confirm } = useConfirm();

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | "all">("all");
  const [formTarget, setFormTarget] = useState<"create" | TestDefinition | null>(null);
  const [cloneSource, setCloneSource] = useState<TestDefinition | null>(null);
  const [commandTest, setCommandTest] = useState<TestDefinition | null>(null);
  const [variablesModalOpen, setVariablesModalOpen] = useState(false);

  const testsQ = useQuery(async () => (await listTestDefinitions({ limit: 500 })).items, []);
  const tests = testsQ.data ?? [];

  const standsQ = useQuery(async () => (await listTestStands({ limit: 500 })).items, []);

  // Общий источник каталога переменных — им пользуется и панель управления
  // (эта переменная), и конструктор команды (`CommandConstructorModal`,
  // получает `variables`/`variablesLoading` пропсами). Один `useQuery` на
  // страницу означает, что после создания/правки/удаления переменной в
  // панели управления достаточно вызвать `variablesQ.refetch()` — и
  // выпадающий список слотов в уже открытом конструкторе увидит новое
  // значение без перезагрузки страницы.
  const variablesQ = useQuery(async () => (await listGlobalVariables({ limit: 200 })).items, []);
  const variables = variablesQ.data ?? [];

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return tests.filter((test) => {
      const key = test.category ?? "other";
      if (category !== "all" && key !== category) return false;
      if (!term) return true;
      return test.code.toLowerCase().includes(term) || test.full_name.toLowerCase().includes(term);
    });
  }, [tests, search, category]);

  const totals = useMemo(
    () => ({
      total: tests.length,
      ready: tests.filter((t) => t.readiness === "ready").length,
      draft: tests.filter((t) => t.readiness === "draft").length,
      blocked: tests.filter((t) => t.readiness === "blocked").length,
    }),
    [tests],
  );

  const categoryCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const test of tests) {
      const key = test.category ?? "other";
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [tests]);

  const categories = useMemo(() => Array.from(categoryCounts.keys()).sort(), [categoryCounts]);

  async function handleCreate(body: TestDefinitionCreateRequest) {
    try {
      const created = await createTestDefinition(body);
      toast.success(`Тест «${body.code}» создан`);
      // Клон "из шаблона" — переносим слоты команды исходного теста один в
      // один (тот же variable_id валиден и у нового теста: global_variables
      // платформенные, не per-test). Порядок не переставляем — create-эндпоинт
      // без явной position сам добавляет слот в конец, поэтому обходим
      // исходные слоты по возрастанию position.
      if (cloneSource) {
        try {
          const sourceArgs = await listTestCommandArgs(cloneSource.id);
          const ordered = [...sourceArgs].sort((a, b) => a.position - b.position);
          for (const arg of ordered) {
            await createTestCommandArg(created.id, {
              kind: arg.kind as CommandArgKind,
              literal_value: arg.literal_value,
              variable_id: arg.variable_id,
              override_value: arg.override_value,
            });
          }
          if (ordered.length > 0) {
            toast.success(`Скопировано слотов команды: ${ordered.length}`);
          }
        } catch (e) {
          toast.error(apiErrMsg(e, "Тест создан, но не удалось скопировать слоты команды исходного теста"));
        }
      }
      setFormTarget(null);
      setCloneSource(null);
      testsQ.refetch();
      // Сразу открываем конструктор команды — иначе созданный тест без единого
      // слота команды выглядит как "потерянный", а кнопку "Конструктор" в
      // таблице ещё нужно найти среди остальных тестов.
      setCommandTest(created);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось создать тест"));
    }
  }

  async function handleUpdate(test: TestDefinition, body: TestDefinitionCreateRequest) {
    try {
      await updateTestDefinition(test.id, body);
      toast.success(`Тест «${test.code}» обновлён`);
      setFormTarget(null);
      testsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось сохранить тест"));
    }
  }

  async function handleDelete(test: TestDefinition) {
    const ok = await confirm({
      title: "Удалить тест из каталога",
      message: `Удалить «${test.code} · ${test.full_name}»? Слоты команды теста будут удалены вместе с ним.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteTestDefinition(test.id);
      toast.success(`Тест «${test.code}» удалён`);
      testsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить тест"));
    }
  }

  return (
    <div className="flex flex-1 min-h-0 flex-col gap-4">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 shrink-0">
        <Stat title="Всего наборов" value={String(totals.total)} icon={FileText} />
        <Stat title="Готовы к запуску" value={String(totals.ready)} icon={CheckCircle2} kind="ok" />
        <Stat title="Требуют параметров" value={String(totals.draft)} icon={Cog} kind="warn" />
        <Stat title="Заблокированы" value={String(totals.blocked)} icon={ShieldCheck} kind="danger" />
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-3 flex-wrap shrink-0">
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
          Все · {tests.length}
        </Button>
        {categories.map((cat) => {
          const meta = categoryVisual(cat);
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
        <Button
          type="button"
          size="sm"
          className="inline-flex items-center gap-1.5 ml-auto"
          onClick={() => setVariablesModalOpen(true)}
        >
          <Variable className="w-3.5 h-3.5" /> Переменные · {variables.length}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="primary"
          className="inline-flex items-center gap-1.5"
          onClick={() => {
            setCloneSource(null);
            setFormTarget("create");
          }}
        >
          <Plus className="w-3.5 h-3.5" /> Добавить тест
        </Button>
      </div>

      <div className="surface border border-token rounded overflow-hidden flex flex-1 min-h-0 flex-col">
        <div className="border-b border-token p-3 flex items-center gap-2 shrink-0">
          <FileText className="w-4 h-4 text-accent" />
          <div className="text-sm font-medium">Каталог тестов · {filtered.length}</div>
        </div>
        {testsQ.loading ? (
          <div className="p-6 text-center text-xs text-dim flex items-center justify-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
          </div>
        ) : testsQ.error && tests.length === 0 ? (
          <div className="p-4">
            <div className="alert alert-danger flex items-start gap-2 text-xs">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <div className="flex-1">
                <div>{apiErrMsg(testsQ.error, "Каталог тестов не загрузился")}</div>
                <Button size="sm" className="mt-2" onClick={() => testsQ.refetch()}>
                  Повторить
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="overflow-auto flex-1 min-h-0">
            <table className="mini">
              <thead>
                <tr>
                  <th>Код</th>
                  <th>Полное имя</th>
                  <th>Ветка git</th>
                  <th>Команда</th>
                  <th>Статус</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((test) => {
                  const meta = categoryVisual(test.category);
                  return (
                    <tr key={test.id}>
                      <td className="mono">{test.code}</td>
                      <td className="truncate max-w-[280px]" title={test.full_name}>{test.full_name}</td>
                      <td>
                        <span className={`badge badge-${meta.badge}`}>{meta.label}</span>
                      </td>
                      <td>
                        <Button
                          size="sm"
                          className="inline-flex items-center gap-1.5"
                          onClick={() => setCommandTest(test)}
                        >
                          <Settings2 className="w-3.5 h-3.5" /> Конструктор
                        </Button>
                      </td>
                      <td><TextStatusBadge value={test.readiness ?? "draft"} /></td>
                      <td>
                        <div className="flex items-center gap-1 justify-end">
                          <Button
                            size="sm"
                            aria-label="Клонировать тест"
                            title="Создать новый тест на основе этого — с той же командой"
                            onClick={() => {
                              setCloneSource(test);
                              setFormTarget("create");
                            }}
                          >
                            <Copy className="w-3.5 h-3.5" />
                          </Button>
                          <Button size="sm" aria-label="Изменить тест" onClick={() => setFormTarget(test)}>
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button size="sm" variant="danger" aria-label="Удалить тест" onClick={() => handleDelete(test)}>
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </td>
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
        )}
      </div>

      {formTarget && (
        <TestFormModal
          mode={formTarget === "create" ? "create" : "edit"}
          initial={formTarget === "create" ? undefined : formTarget}
          template={formTarget === "create" ? cloneSource ?? undefined : undefined}
          stands={standsQ.data ?? []}
          onClose={() => {
            setFormTarget(null);
            setCloneSource(null);
          }}
          onSubmit={(body) =>
            formTarget === "create" ? handleCreate(body) : handleUpdate(formTarget, body)
          }
        />
      )}

      {commandTest && (
        <CommandConstructorModal
          test={commandTest}
          allTests={tests}
          variables={variables}
          variablesLoading={variablesQ.loading}
          onClose={() => setCommandTest(null)}
        />
      )}

      {variablesModalOpen && (
        <GlobalVariablesModal
          variables={variables}
          loading={variablesQ.loading}
          error={variablesQ.error}
          onRefetch={variablesQ.refetch}
          onClose={() => setVariablesModalOpen(false)}
        />
      )}
    </div>
  );
}

// ── модалка карточки теста (создание/редактирование) ────────────────────

function TestFormModal({
  mode,
  initial,
  template,
  stands,
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  initial?: TestDefinition;
  /** Только для mode="create": исходный тест, из которого предзаполняются поля
   * и (в TestsWorkzone.handleCreate) копируются слоты команды — "клонирование". */
  template?: TestDefinition;
  stands: TestStand[];
  onClose: () => void;
  onSubmit: (body: TestDefinitionCreateRequest) => void | Promise<void>;
}) {
  const [code, setCode] = useState(initial?.code ?? (template ? `${template.code}.copy` : ""));
  const [fullName, setFullName] = useState(initial?.full_name ?? template?.full_name ?? "");
  const [category, setCategory] = useState(initial?.category ?? template?.category ?? "");
  const [readiness, setReadiness] = useState(initial?.readiness ?? template?.readiness ?? "draft");
  const [pinnedStandId, setPinnedStandId] = useState(
    initial?.pinned_stand_id ?? template?.pinned_stand_id ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  const valid = code.trim() !== "" && fullName.trim() !== "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit({
        code: code.trim(),
        full_name: fullName.trim(),
        category: category.trim() || null,
        readiness: readiness || null,
        pinned_stand_id: pinnedStandId || null,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title={mode === "create" ? "Новый тест каталога" : `Изменить тест · ${initial?.code}`}
      subtitle={template ? `На основе «${template.code}» — команда будет скопирована` : undefined}
      icon={<FlaskConical className="w-5 h-5 text-accent" />}
      width="md"
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Код *</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="FS-EXT4-FILL"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Полное имя *</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 text-sm"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="filesystem / ext4 fill+remove cycle"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Ветка git</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 text-sm"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="postgresql"
            list="tests-category-suggestions"
          />
        </label>
        <datalist id="tests-category-suggestions">
          {Object.keys(CATEGORY_VISUAL).map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Готовность</span>
          <Dropdown mode="single" options={READINESS_OPTIONS} value={readiness ?? ""} onChange={setReadiness} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Привязанный стенд</span>
          <Dropdown
            mode="single"
            searchable
            placeholder="— не привязан —"
            options={[
              { value: "", label: "— не привязан —" },
              ...stands.map((s) => ({ value: s.id, label: s.server_id })),
            ]}
            value={pinnedStandId ?? ""}
            onChange={setPinnedStandId}
          />
        </label>
        <div className="modal-footer -mx-5 -mb-5 mt-2">
          <Button type="button" onClick={onClose}>Отмена</Button>
          <Button variant="primary" type="submit" disabled={!valid || submitting}>
            {submitting ? "Сохранение…" : mode === "create" ? "Создать" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// ── конструктор команды (test_command_args) ──────────────────────────────

function CommandConstructorModal({
  test,
  allTests,
  variables,
  variablesLoading,
  onClose,
}: {
  test: TestDefinition;
  allTests: TestDefinition[];
  variables: GlobalVariable[];
  variablesLoading: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();

  const slotsQ = useQuery(() => listTestCommandArgs(test.id), [test.id]);
  const variableById = useMemo(() => new Map(variables.map((v) => [v.id, v])), [variables]);

  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [copyOpen, setCopyOpen] = useState(false);
  const [copySourceId, setCopySourceId] = useState("");
  const [copyError, setCopyError] = useState<string | null>(null);
  const copySources = allTests.filter((candidate) => candidate.id !== test.id);

  const slots = useMemo(
    () => [...(slotsQ.data ?? [])].sort((a, b) => a.position - b.position),
    [slotsQ.data],
  );

  async function handleCopy() {
    if (!copySourceId || busy) return;
    setBusy(true);
    setCopyError(null);
    try {
      const copied = await copyTestCommandArgs(test.id, copySourceId);
      setCopyOpen(false);
      setCopySourceId("");
      setEditId(null);
      setAddOpen(false);
      slotsQ.refetch();
      toast.success(`Параметры скопированы: ${copied.length}. Теперь их можно изменить.`);
    } catch (e) {
      const code = (e as { errorCode?: string })?.errorCode;
      setCopyError(code === "COMMAND_COPY_SOURCE_EMPTY"
        ? "В выбранном тесте нет параметров. Выберите другой тест."
        : apiErrMsg(e, "Не удалось скопировать параметры"));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreate(body: SlotBody) {
    setBusy(true);
    try {
      await createTestCommandArg(test.id, body);
      toast.success("Слот добавлен");
      setAddOpen(false);
      slotsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось добавить слот"));
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(argId: string, body: SlotBody) {
    setBusy(true);
    try {
      await updateTestCommandArg(test.id, argId, body);
      toast.success("Слот обновлён");
      setEditId(null);
      slotsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось сохранить слот"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(slot: TestCommandArg) {
    const ok = await confirm({
      title: "Удалить слот команды",
      message: `Удалить слот №${slot.position + 1} из команды теста «${test.code}»?`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteTestCommandArg(test.id, slot.id);
      toast.success("Слот удалён");
      slotsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить слот"));
    } finally {
      setBusy(false);
    }
  }

  // Готовой drag&drop-библиотеки в проекте нет (проверено — ни @dnd-kit/*, ни
  // аналогов в package.json), заводить новую зависимость ради одного списка
  // из нескольких десятков строк избыточно — переставляем кнопками ↑/↓,
  // меняя местами `position` двух соседних слотов.
  async function moveSlot(index: number, dir: -1 | 1) {
    const current = slots[index];
    const other = slots[index + dir];
    if (!current || !other) return;
    setBusy(true);
    try {
      await Promise.all([
        updateTestCommandArg(test.id, current.id, { position: other.position }),
        updateTestCommandArg(test.id, other.id, { position: current.position }),
      ]);
      slotsQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить порядок слотов"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => !next && !busy && onClose()}
      title={`Конструктор команды · ${test.code}`}
      subtitle={test.full_name}
      icon={<Settings2 className="w-5 h-5 text-accent" />}
      width="lg"
    >
      <div className="flex flex-col gap-3">
        <div className="text-xs text-dim">
          Упорядоченный список аргументов команды теста — литерал или ссылка на
          глобальную переменную. Порядок задаётся кнопками ↑/↓.
        </div>

        {copyOpen ? (
          <div className="surface-2 border border-token rounded p-3 flex flex-col gap-3">
            <Dropdown
              mode="single"
              searchable
              placeholder="Выберите тест для копирования"
              options={copySources.map((source) => ({
                value: source.id,
                label: `${source.code} · ${source.full_name}`,
              }))}
              value={copySourceId}
              onChange={(value) => { setCopySourceId(value); setCopyError(null); }}
              disabled={busy}
            />
            <div className="text-xs text-dim">
              Параметры выбранного теста заменят текущие. После копирования их можно
              редактировать; исходный тест не изменится.
            </div>
            {copyError && <div role="alert" className="alert alert-danger text-xs">{copyError}</div>}
            <div className="flex justify-end gap-2">
              <Button size="sm" disabled={busy} onClick={() => setCopyOpen(false)}>Отмена</Button>
              <Button size="sm" variant="primary" disabled={!copySourceId || busy} onClick={handleCopy}>
                {busy ? "Копирование…" : "ОК"}
              </Button>
            </div>
          </div>
        ) : (
          <Button
            size="sm"
            className="inline-flex items-center gap-1.5 self-start"
            disabled={busy || slotsQ.isFetching || !!slotsQ.error || addOpen || !!editId || copySources.length === 0}
            onClick={() => { setCopyOpen(true); setCopySourceId(""); setCopyError(null); }}
          >
            <Copy className="w-3.5 h-3.5" /> Скопировать из
          </Button>
        )}

        {slotsQ.loading || variablesLoading ? (
          <div className="text-xs text-dim flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
          </div>
        ) : slotsQ.error ? (
          <div className="alert alert-danger flex items-start gap-2 text-xs">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div className="flex-1">{apiErrMsg(slotsQ.error, "Список слотов не загрузился")}</div>
            <Button size="sm" onClick={() => slotsQ.refetch()}>Повторить</Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {slots.length === 0 && !addOpen && (
              <div className="text-xs text-dim">Команда пока пустая — добавьте первый слот.</div>
            )}
            {slots.map((slot, index) =>
              editId === slot.id ? (
                <SlotEditorForm
                  key={slot.id}
                  variables={variables}
                  initialKind={(slot.kind === "variable" ? "variable" : "literal") as CommandArgKind}
                  initialLiteral={slot.literal_value ?? ""}
                  initialVariableId={slot.variable_id ?? ""}
                  initialOverride={slot.override_value ?? ""}
                  saving={busy}
                  onCancel={() => setEditId(null)}
                  onSave={(body) => handleUpdate(slot.id, body)}
                />
              ) : (
                <SlotRow
                  key={slot.id}
                  index={index}
                  slot={slot}
                  variable={slot.variable_id ? variableById.get(slot.variable_id) : undefined}
                  canMoveUp={index > 0}
                  canMoveDown={index < slots.length - 1}
                  disabled={busy || copyOpen || slotsQ.isFetching}
                  onMoveUp={() => moveSlot(index, -1)}
                  onMoveDown={() => moveSlot(index, 1)}
                  onEdit={() => setEditId(slot.id)}
                  onDelete={() => handleDelete(slot)}
                />
              ),
            )}
          </div>
        )}

        {addOpen ? (
          <SlotEditorForm
            variables={variables}
            saving={busy}
            onCancel={() => setAddOpen(false)}
            onSave={handleCreate}
          />
        ) : (
          <Button
            type="button"
            size="sm"
            className="inline-flex items-center gap-1.5 self-start"
            onClick={() => setAddOpen(true)}
            disabled={busy || copyOpen || slotsQ.isFetching || !!slotsQ.error || variablesLoading}
          >
            <Plus className="w-3.5 h-3.5" /> Добавить слот
          </Button>
        )}
      </div>
    </Modal>
  );
}

interface SlotBody {
  kind: CommandArgKind;
  literal_value: string | null;
  variable_id: string | null;
  override_value: string | null;
}

function SlotRow({
  index,
  slot,
  variable,
  canMoveUp,
  canMoveDown,
  disabled,
  onMoveUp,
  onMoveDown,
  onEdit,
  onDelete,
}: {
  index: number;
  slot: TestCommandArg;
  variable: GlobalVariable | undefined;
  canMoveUp: boolean;
  canMoveDown: boolean;
  disabled: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="surface-2 border border-token rounded p-2 flex items-center gap-2">
      <div className="mono text-xs text-dim w-5 text-center shrink-0">{index + 1}</div>
      <div className="flex flex-col shrink-0">
        <button
          type="button"
          className="btn btn-ghost btn-sm !p-0.5"
          disabled={!canMoveUp || disabled}
          onClick={onMoveUp}
          aria-label="Переместить слот выше"
        >
          <ChevronUp className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm !p-0.5"
          disabled={!canMoveDown || disabled}
          onClick={onMoveDown}
          aria-label="Переместить слот ниже"
        >
          <ChevronDown className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="flex-1 min-w-0 flex items-center gap-1.5 flex-wrap text-sm">
        {slot.kind === "variable" ? (
          <>
            <Badge kind="accent">переменная</Badge>
            <span className="mono">{variable?.code ?? slot.variable_id}</span>
            {variable?.is_sensitive && <Badge kind="warn">чувствительно</Badge>}
            {slot.override_value && (
              <span className="text-dim text-xs">
                override: <span className="mono">{slot.override_value}</span>
              </span>
            )}
          </>
        ) : (
          <>
            <Badge kind="ok">литерал</Badge>
            <span className="mono truncate">{slot.literal_value}</span>
          </>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button size="sm" onClick={onEdit} disabled={disabled} aria-label="Изменить слот">
          <Pencil className="w-3.5 h-3.5" />
        </Button>
        <Button size="sm" variant="danger" onClick={onDelete} disabled={disabled} aria-label="Удалить слот">
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

function SlotEditorForm({
  variables,
  initialKind = "literal",
  initialLiteral = "",
  initialVariableId = "",
  initialOverride = "",
  saving,
  onCancel,
  onSave,
}: {
  variables: GlobalVariable[];
  initialKind?: CommandArgKind;
  initialLiteral?: string;
  initialVariableId?: string;
  initialOverride?: string;
  saving?: boolean;
  onCancel: () => void;
  onSave: (body: SlotBody) => void | Promise<void>;
}) {
  const [kind, setKind] = useState<CommandArgKind>(initialKind);
  const [literalValue, setLiteralValue] = useState(initialLiteral);
  const [variableId, setVariableId] = useState(initialVariableId);
  const [overrideValue, setOverrideValue] = useState(initialOverride);

  const selectedVariable = variables.find((v) => v.id === variableId);

  // Резолв choices — только удобство при заполнении override_value (если у
  // переменной есть choices_source без обязательных параметров). Резолверам,
  // которым нужен контекст (например dynamic:kernels с os_version_id), тут
  // взяться неоткуда — при ошибке просто откатываемся на свободный текст.
  const choicesQ = useQuery(
    async () => {
      if (kind !== "variable" || !selectedVariable?.choices_source) return null;
      try {
        return await getGlobalVariableChoices(selectedVariable.id);
      } catch {
        return null;
      }
    },
    [kind, selectedVariable?.id, selectedVariable?.choices_source],
  );
  const choices = choicesQ.data?.items ?? [];

  const valid = kind === "literal" ? literalValue.trim() !== "" : variableId !== "";

  async function submit() {
    if (!valid) return;
    await onSave({
      kind,
      literal_value: kind === "literal" ? literalValue.trim() : null,
      variable_id: kind === "variable" ? variableId : null,
      override_value: kind === "variable" && overrideValue.trim() !== "" ? overrideValue.trim() : null,
    });
  }

  return (
    <div className="surface border border-token rounded p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" variant={kind === "literal" ? "primary" : "default"} onClick={() => setKind("literal")}>
          Литерал
        </Button>
        <Button type="button" size="sm" variant={kind === "variable" ? "primary" : "default"} onClick={() => setKind("variable")}>
          Переменная
        </Button>
      </div>
      {kind === "literal" ? (
        <input
          className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
          placeholder="значение аргумента"
          value={literalValue}
          onChange={(e) => setLiteralValue(e.target.value)}
        />
      ) : (
        <>
          <Dropdown
            mode="single"
            searchable
            placeholder="— выберите переменную —"
            options={variables.map((v) => ({ value: v.id, label: `${v.code} · ${v.label}` }))}
            value={variableId}
            onChange={setVariableId}
          />
          {selectedVariable?.is_sensitive && (
            <div className="text-[11px] text-warn">
              Значение переменной чувствительное — в логах команды будет заменено на ***.
            </div>
          )}
          {choices.length > 0 ? (
            <Dropdown
              mode="single"
              searchable
              placeholder="override_value — необязательно"
              options={[{ value: "", label: "— значение по умолчанию —" }, ...choices]}
              value={overrideValue}
              onChange={setOverrideValue}
            />
          ) : (
            <input
              className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
              placeholder="override_value (необязательно)"
              value={overrideValue}
              onChange={(e) => setOverrideValue(e.target.value)}
            />
          )}
        </>
      )}
      <div className="flex items-center gap-2 justify-end">
        <Button type="button" size="sm" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" variant="primary" disabled={!valid || saving} onClick={submit}>
          {saving ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  );
}

// ── управление глобальными переменными (global_variables) ────────────────

function GlobalVariablesModal({
  variables,
  loading,
  error,
  onRefetch,
  onClose,
}: {
  variables: GlobalVariable[];
  loading: boolean;
  error: unknown;
  onRefetch: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();

  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleCreate(body: GlobalVariableCreateRequest) {
    setBusy(true);
    try {
      await createGlobalVariable(body);
      toast.success(`Переменная «${body.code}» создана`);
      setAddOpen(false);
      onRefetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось создать переменную"));
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(variable: GlobalVariable, body: GlobalVariableCreateRequest) {
    setBusy(true);
    try {
      await updateGlobalVariable(variable.id, body);
      toast.success(`Переменная «${variable.code}» обновлена`);
      setEditId(null);
      onRefetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось сохранить переменную"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(variable: GlobalVariable) {
    const ok = await confirm({
      title: "Удалить глобальную переменную",
      message: `Удалить «${variable.code} · ${variable.label}»? Если переменную использует хотя бы один слот команды теста, удаление будет отклонено.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteGlobalVariable(variable.id);
      toast.success(`Переменная «${variable.code}» удалена`);
      onRefetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось удалить переменную"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title="Глобальные переменные"
      subtitle="Каталог переменных конструктора команд"
      icon={<Variable className="w-5 h-5 text-accent" />}
      width="lg"
    >
      <div className="flex flex-col gap-3">
        <div className="text-xs text-dim">
          Переменные, доступные слотам команды любого теста каталога — источник значения,
          тип и (опционально) резолвер вариантов выбора (<span className="mono">choices_source</span>).
        </div>

        {loading ? (
          <div className="text-xs text-dim flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
          </div>
        ) : error ? (
          <div className="alert alert-danger flex items-start gap-2 text-xs">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div className="flex-1">{apiErrMsg(error, "Список переменных не загрузился")}</div>
            <Button size="sm" onClick={() => onRefetch()}>Повторить</Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {variables.length === 0 && !addOpen && (
              <div className="text-xs text-dim">Переменных пока нет — добавьте первую.</div>
            )}
            {variables.map((variable) =>
              editId === variable.id ? (
                <GlobalVariableEditorForm
                  key={variable.id}
                  initial={variable}
                  saving={busy}
                  onCancel={() => setEditId(null)}
                  onSave={(body) => handleUpdate(variable, body)}
                />
              ) : (
                <GlobalVariableRow
                  key={variable.id}
                  variable={variable}
                  disabled={busy}
                  onEdit={() => setEditId(variable.id)}
                  onDelete={() => handleDelete(variable)}
                />
              ),
            )}
          </div>
        )}

        {addOpen ? (
          <GlobalVariableEditorForm saving={busy} onCancel={() => setAddOpen(false)} onSave={handleCreate} />
        ) : (
          <Button
            type="button"
            size="sm"
            className="inline-flex items-center gap-1.5 self-start"
            onClick={() => setAddOpen(true)}
            disabled={loading}
          >
            <Plus className="w-3.5 h-3.5" /> Добавить переменную
          </Button>
        )}
      </div>
    </Modal>
  );
}

function GlobalVariableRow({
  variable,
  disabled,
  onEdit,
  onDelete,
}: {
  variable: GlobalVariable;
  disabled: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="surface-2 border border-token rounded p-2 flex items-center gap-2">
      <div className="flex-1 min-w-0 flex items-center gap-1.5 flex-wrap text-sm">
        <span className="mono font-medium">{variable.code}</span>
        <span className="text-dim text-xs">{variable.label}</span>
        <Badge kind="accent">{variable.source}</Badge>
        <Badge kind="ok">{variable.value_type}</Badge>
        {variable.choices_source && (
          <span className="mono text-[11px] text-dim">choices: {variable.choices_source}</span>
        )}
        {variable.is_sensitive && <Badge kind="warn">чувствительно</Badge>}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button size="sm" onClick={onEdit} disabled={disabled} aria-label="Изменить переменную">
          <Pencil className="w-3.5 h-3.5" />
        </Button>
        <Button size="sm" variant="danger" onClick={onDelete} disabled={disabled} aria-label="Удалить переменную">
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

function GlobalVariableEditorForm({
  initial,
  saving,
  onCancel,
  onSave,
}: {
  initial?: GlobalVariable;
  saving?: boolean;
  onCancel: () => void;
  onSave: (body: GlobalVariableCreateRequest) => void | Promise<void>;
}) {
  const [code, setCode] = useState(initial?.code ?? "");
  const [label, setLabel] = useState(initial?.label ?? "");
  const [source, setSource] = useState<GlobalVariableSource>(
    (initial?.source as GlobalVariableSource) ?? "launch_context",
  );
  const [valueType, setValueType] = useState<GlobalVariableValueType>(
    (initial?.value_type as GlobalVariableValueType) ?? "string",
  );
  const [choicesSource, setChoicesSource] = useState(initial?.choices_source ?? "");
  const [isSensitive, setIsSensitive] = useState(initial?.is_sensitive ?? false);
  const [description, setDescription] = useState(initial?.description ?? "");

  const valid = code.trim() !== "" && label.trim() !== "";

  async function submit() {
    if (!valid) return;
    await onSave({
      code: code.trim(),
      label: label.trim(),
      source,
      value_type: valueType,
      choices_source: choicesSource.trim() || null,
      is_sensitive: isSensitive,
      description: description.trim() || null,
    });
  }

  return (
    <div className="surface border border-token rounded p-3 flex flex-col gap-2">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <input
          className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
          placeholder="код, например RC"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <input
          className="surface-2 border border-token rounded px-2 py-1 text-sm"
          placeholder="метка"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Dropdown
          mode="single"
          options={SOURCE_OPTIONS}
          value={source}
          onChange={(v) => setSource(v as GlobalVariableSource)}
        />
        <Dropdown
          mode="single"
          options={VALUE_TYPE_OPTIONS}
          value={valueType}
          onChange={(v) => setValueType(v as GlobalVariableValueType)}
        />
      </div>
      <input
        className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
        placeholder="choices_source (необязательно, например dynamic:kernels)"
        value={choicesSource}
        onChange={(e) => setChoicesSource(e.target.value)}
      />
      <input
        className="surface-2 border border-token rounded px-2 py-1 text-sm"
        placeholder="описание (необязательно)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <Checkbox checked={isSensitive} onChange={(e) => setIsSensitive(e.target.checked)} />
        Чувствительное значение (маскируется в логах команды)
      </label>
      <div className="flex items-center gap-2 justify-end">
        <Button type="button" size="sm" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" variant="primary" disabled={!valid || saving} onClick={submit}>
          {saving ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  );
}
