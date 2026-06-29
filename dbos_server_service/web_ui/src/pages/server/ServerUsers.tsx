/**
 * Страница /server/users — сводный список всех server_account'ов отдела в
 * 3-панельной раскладке (Shell: left + middle-list + right-workzone).
 *
 * Per-server аккаунты живут на вкладке «Аккаунты» карточки сервера; здесь —
 * единый список «все пользователи разом» по всему отделу. Список тянется
 * dept-wide эндпоинтом (`listAccounts` без `server_id`) — одной выдачей по
 * department'у вызывающего, включая аккаунты без единой привязки (хранимые
 * кредены с 0 серверов), которые per-server листинг показать не мог. Список
 * серверов (`listServers`) грузится параллельно — он нужен для фильтра,
 * имён серверов и привязки новых.
 *
 * Средняя панель — список аккаунтов с поиском/фильтром по серверу и сортом;
 * выбор строки кладёт `?id=<accountId>` в URL. Правая рабочая зона — карточка
 * выбранного аккаунта с управлением: reveal пароля, edit (PATCH
 * has_sudo/unix_groups/shell), ротация пароля (только БД), удаление и секция
 * «Серверы аккаунта» — per-server provision (useradd) / update_on_host /
 * deprovision (userdel) / bind / unbind по каждому привязанному серверу.
 * Те же операции доступны и на вкладке «Аккаунты» карточки сервера.
 *
 * account_admin / logging_* отрезаны от server-зоны backend'ом
 * (`PLATFORM_ADMIN_BUSINESS_DATA_DENIED`) — для них BlockedPane вместо мёртвой
 * страницы. dep_admin видит аккаунты серверов своего отдела; server.*-роли —
 * по матрице.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Search,
  Users,
  AlertCircle,
  User,
  KeyRound,
  Eye,
  EyeOff,
  Copy,
  Edit3,
  Trash2,
  RotateCw,
  ShieldCheck,
  Server as ServerIcon,
  Power,
  Unlink,
  Link2,
  ScanSearch,
  Plus,
  X,
  KeySquare,
  Download,
  Upload,
  AlertTriangle,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { HelpTooltip } from "@/components/ui/HelpTooltip";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64, toBase64 } from "@/lib/base64";
import { formatMskShort } from "@/lib/datetime";
import { useServerMap, useUserLabel } from "@/lib/labels";
import { listServers } from "@/api/server/servers";
import * as accountsApi from "@/api/server/accounts";
import { usersInventory, listTasks } from "@/api/server/misc";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { RevisionDiffModal } from "@/pages/server/RevisionDiffModal";
import { OsUsersDiscoveryModal } from "@/pages/server/OsUsersDiscoveryModal";
import { RotateDispatchResult } from "@/pages/server/_rotateResult";
import { isDepAdmin, isServerZoneBlocked } from "@/lib/rbac";
import {
  type RevisionAccountDiff,
  type Server,
  type ServerAccount,
  type ServerAccountSource,
  type ServerAccountUpdateRequest,
  type SshKeyMode,
} from "@/api/server/types";

// Аккаунтов и серверов на отдел немного — одной страницы с запасом хватает,
// клиентский поиск/сорт идут по загруженному набору. Кап честно отражается в
// TruncationNotice.
const SERVER_LIMIT = 200;
const ACCOUNTS_LIMIT = 500;

// Зеркало `server_service/src/schemas/server_account.py`:
//   login — `^[A-Za-z0-9._\-]+$`, 1..128 символов.
//   unix_groups — POSIX group name `^[a-z_][a-z0-9_-]{0,31}$`.
const LOGIN_RE = /^[A-Za-z0-9._-]+$/;
const POSIX_GROUP_RE = /^[a-z_][a-z0-9_-]{0,31}$/;

function validateLogin(value: string): string | null {
  if (value.length > 128) return "login: максимум 128 символов";
  if (!LOGIN_RE.test(value)) {
    return "login: допустимы латиница, цифры и символы . _ -";
  }
  return null;
}

function validateUnixGroups(groups: string[]): string | null {
  for (const g of groups) {
    if (!POSIX_GROUP_RE.test(g)) {
      return `unix_groups: '${g}' не POSIX-имя (строчные, цифры, _ -, до 32 симв.)`;
    }
  }
  return null;
}

// Парольная политика backend'а: минимум 8 символов, обязательны и буквы, и
// цифры. Текст один — используем и для подсказки у поля, и для маппинга
// VALIDATION_ERROR по `password_b64`.
const PASSWORD_POLICY_TEXT =
  "Пароль не соответствует политике: минимум 8 символов, буквы и цифры.";

function validatePassword(value: string): string | null {
  if (value.length < 8) return PASSWORD_POLICY_TEXT;
  if (!/[A-Za-z]/.test(value) || !/[0-9]/.test(value)) {
    return PASSWORD_POLICY_TEXT;
  }
  return null;
}

/**
 * Маппит backend-VALIDATION_ERROR по полю `password_b64` в человекочитаемый
 * текст парольной политики. Не пароль — возвращает null, чтобы caller отдал
 * ошибку дальше своему обработчику.
 */
function passwordPolicyError(e: unknown): string | null {
  if (e instanceof ApiError && /password_b64/i.test(apiErrMsg(e, ""))) {
    return PASSWORD_POLICY_TEXT;
  }
  return null;
}

type SortMode = "login" | "server" | "rotated";

// Подсказки к бейджу происхождения аккаунта.
const SOURCE_HELP: Record<ServerAccountSource, string> = {
  managed:
    "Заведён через систему, пароль известен сервису — можно ротировать и раскатывать на серверы.",
  discovered:
    "Найден инвентаризацией на сервере, пароль сервису неизвестен — для применения нужен force/ручная ротация.",
};

/**
 * Бейдж происхождения аккаунта (managed/discovered) с подсказкой при
 * наведении. Подсказку держим в общем `HelpTooltip`, чтобы стиль совпадал с
 * остальными справками формы.
 */
function SourceBadge({ source }: { source: ServerAccountSource }) {
  const help = SOURCE_HELP[source];
  return (
    <span className="badge inline-flex items-center gap-1">
      {source}
      {help && <HelpTooltip text={help} label={`Что значит ${source}`} inline />}
    </span>
  );
}

/** Сохранить текст в файл через временный object-URL (download приватного ключа). */
function downloadText(filename: string, text: string) {
  if (typeof document === "undefined") return;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Освобождаем URL чуть позже — синхронный revoke ломает скачивание в части браузеров.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Прочитать текстовое содержимое выбранного файла (для загрузки SSH-ключей). */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(file);
  });
}

// Минимальная проверка приватного ключа на стороне формы — настоящую валидацию
// делает backend/worker. Здесь ловим только пустой ввод и явно не-ключи.
const SSH_PRIVATE_KEY_RE = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/;

function validateSshPrivateKey(value: string): string | null {
  if (!SSH_PRIVATE_KEY_RE.test(value)) {
    return "Приватный ключ не похож на PEM/OpenSSH (нет строки BEGIN … PRIVATE KEY).";
  }
  return null;
}

/**
 * Кнопка «загрузить из файла» с вшитым скрытым `<input type=file>`: читает
 * содержимое выбранного файла как текст и отдаёт его в `onText`. После выбора
 * сбрасывает value, чтобы повторный выбор того же файла снова сработал.
 */
function FileLoadButton({
  label,
  accept,
  onText,
  onError,
}: {
  label: string;
  accept?: string;
  onText: (text: string) => void;
  onError?: () => void;
}) {
  return (
    <label
      className="btn btn-sm flex items-center gap-1 cursor-pointer"
      title={label}
    >
      <Upload className="w-3.5 h-3.5" /> Загрузить файл
      <input
        type="file"
        accept={accept}
        aria-label={label}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          readFileText(file)
            .then(onText)
            .catch(() => onError?.());
        }}
      />
    </label>
  );
}

/** Режим SSH-ключа в формах create/edit. */
type SshKeyChoice = "none" | "generate" | "supply";

/**
 * Сводная выдача аккаунтов отдела: список + общее число по данным backend'а
 * (для TruncationNotice, если аккаунтов больше лимита одной страницы).
 */
interface AggregatedAccounts {
  accounts: ServerAccount[];
  /** Всего аккаунтов в отделе по данным backend (для TruncationNotice). */
  total: number;
}

/**
 * Тянет все аккаунты отдела одной dept-wide выдачей (`listAccounts` без
 * `server_id`). В отличие от прежнего fan-out по серверам, сюда попадают и
 * unbound-аккаунты (0 привязок). Пагинацию не разворачиваем: на отдел
 * аккаунтов немного, при переборе лимита показываем TruncationNotice.
 */
async function loadAllAccounts(): Promise<AggregatedAccounts> {
  const data = await accountsApi.listAccounts({ limit: ACCOUNTS_LIMIT });
  const total = "total" in data ? data.total : data.items.length;
  return { accounts: data.items, total };
}

export function ServerUsers() {
  const { persona } = usePersona();
  const toast = useToast();
  const zoneBlocked = isServerZoneBlocked(persona);
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");

  // server.* / dep_admin → reveal по матрице (фактический грант view_password
  // проверяет backend). guest/reader без view_password получат null/403 —
  // кнопку держим доступной для operator+ как в карточке аккаунта.
  // dep_admin берём из общего `lib/rbac` — он уже учитывает нормализацию
  // `department_admin → dep_admin`, поэтому локальная копия строки не нужна.
  const depAdmin = isDepAdmin(persona);
  const serverRole = persona.service_roles.server;
  const canOperate =
    depAdmin || serverRole === "admin" || serverRole === "operator";
  // create/edit/delete учётки — только admin/dep_admin (как на вкладке
  // «Аккаунты» карточки сервера). Ротация и reveal — operator+.
  const canManage = depAdmin || serverRole === "admin";
  const canReveal = canOperate;

  const serverMap = useServerMap();

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("login");
  const [serverFilter, setServerFilter] = useState<string>("all");
  const [creating, setCreating] = useState(false);
  const [discovering, setDiscovering] = useState(false);

  // Последняя инвентаризация (users.inventory), запущенная из этой точки или
  // подтянутая с backend'а — server_id, по которому она шла. Результат живёт в
  // самой задаче, поэтому индикатор переживает закрытие модалки и перезагрузку
  // страницы. Сам исход опрашивает useTaskOutcome.
  const [lastInventoryServer, setLastInventoryServer] = useState<string | null>(
    null,
  );
  const inventory = useTaskOutcome();
  const navigate = useNavigate();

  const serversQ = useQuery(
    () => listServers({ limit: SERVER_LIMIT }),
    [],
    { enabled: !zoneBlocked },
  );

  const servers = useMemo(() => serversQ.data?.items ?? [], [serversQ.data]);
  const serverTotal = serversQ.data?.total ?? servers.length;
  const serverIds = useMemo(() => servers.map((s) => s.id), [servers]);

  // Аккаунты тянем dept-wide — одной выдачей по отделу, независимо от серверов
  // (unbound-аккаунты должны быть видны даже когда серверов в отделе нет).
  const accountsQ = useQuery(
    () => loadAllAccounts(),
    [],
    { enabled: !zoneBlocked },
  );

  const allAccounts = useMemo(
    () => accountsQ.data?.accounts ?? [],
    [accountsQ.data],
  );

  const serverName = (id: string) => serverMap.get(id) ?? id;

  // Подтягиваем последнюю инвентаризацию отдела при заходе на страницу, чтобы
  // индикатор был и без свежего скана (после перезагрузки/возврата). Тянем один
  // раз, когда трека ещё нет и серверы загрузились; свой свежий dispatch
  // (handleScanStarted) имеет приоритет и перекрывает подтянутый.
  useEffect(() => {
    if (zoneBlocked || serverIds.length === 0 || inventory.tracked) return;
    let cancelled = false;
    listTasks({ kind: "users.inventory", limit: 1 })
      .then((page) => {
        if (cancelled) return;
        const task = page.items[0];
        if (!task || !task.server_id) return;
        setLastInventoryServer(task.server_id);
        inventory.track("discovery", task.id, task.status);
      })
      .catch(() => {
        // Нет доступа к /tasks или сеть — просто не показываем индикатор.
      });
    return () => {
      cancelled = true;
    };
    // inventory.track стабилен (useCallback); пересеиваем только когда сменился
    // набор серверов или зона разблокировалась.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneBlocked, serverIds.length]);

  function handleScanStarted(serverId: string, taskId: string) {
    setLastInventoryServer(serverId);
    inventory.track("discovery", taskId, "queued");
  }

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matched = allAccounts.filter((a) => {
      if (serverFilter !== "all" && !a.server_ids.includes(serverFilter)) {
        return false;
      }
      if (!term) return true;
      const haystack = [
        a.login,
        a.id,
        ...a.unix_groups,
        ...a.server_ids.map(serverName),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(term);
    });
    const sorted = [...matched].sort((a, b) => {
      if (sort === "server") {
        const sa = a.server_ids.map(serverName).sort()[0] ?? "";
        const sb = b.server_ids.map(serverName).sort()[0] ?? "";
        return sa.localeCompare(sb);
      }
      if (sort === "rotated") {
        return (b.password_rotated_at ?? "").localeCompare(
          a.password_rotated_at ?? "",
        );
      }
      return a.login.localeCompare(b.login);
    });
    return sorted;
    // serverMap влияет на serverName (имена для сорта/поиска) — пересчитываем
    // при его обновлении.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allAccounts, search, sort, serverFilter, serverMap]);

  const selectedAccount = useMemo(
    () => (selectedId ? allAccounts.find((a) => a.id === selectedId) ?? null : null),
    [selectedId, allAccounts],
  );

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    setParams(next, { replace: true });
  }

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / server users">
        <BlockedPane />
      </Shell>
    );
  }

  const loading = serversQ.loading || accountsQ.loading;
  const error = serversQ.error ?? accountsQ.error;
  const accountsTotal = accountsQ.data?.total ?? allAccounts.length;

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-2 flex-1 min-w-[140px]">
            <Search className="w-4 h-4 text-dim shrink-0" />
            <input
              className="bg-transparent outline-none flex-1 min-w-0 text-sm"
              placeholder={`Поиск по ${allAccounts.length} аккаунтам…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {canOperate && (
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1 shrink-0"
              onClick={() => setDiscovering(true)}
              title="Найти OS-юзеров на сервере (discovery)"
            >
              <ScanSearch className="w-3.5 h-3.5" /> Поиск на ОС
            </button>
          )}
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
          <span>Сервер:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
          >
            <option value="all">все серверы</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name || s.hostname}
              </option>
            ))}
          </select>
          <span>Сорт:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortMode)}
          >
            <option value="login">по login</option>
            <option value="server">по серверу</option>
            <option value="rotated">по ротации</option>
          </select>
        </div>
        {inventory.tracked && (
          <InventoryStatusBar
            tracked={inventory.tracked}
            serverName={
              lastInventoryServer ? serverName(lastInventoryServer) : ""
            }
            onOpen={() =>
              inventory.tracked && navigate(`/tasks/${inventory.tracked.taskId}`)
            }
            onDismiss={() => {
              inventory.reset();
              setLastInventoryServer(null);
            }}
          />
        )}
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {!loading && error != null && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(error, "Список пользователей не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => {
                  serversQ.refetch();
                  accountsQ.refetch();
                }}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!loading && error == null && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            {allAccounts.length > 0
              ? "Под текущий фильтр аккаунтов нет."
              : "В отделе нет server_account'ов."}
          </div>
        )}
        {!loading && error == null && filtered.length > 0 && (
          <div className="px-2 flex flex-col gap-0.5">
            {filtered.map((a) => (
              <AccountRow
                key={a.id}
                account={a}
                active={selectedId === a.id}
                serverName={serverName}
                onSelect={() => selectId(a.id)}
              />
            ))}
          </div>
        )}
        {!loading && error == null && (
          <>
            <TruncationNotice
              shown={servers.length}
              total={serverTotal}
              className="mx-3 mt-2"
            />
            <TruncationNotice
              shown={allAccounts.length}
              total={accountsTotal}
              className="mx-3 mt-1"
            />
          </>
        )}
      </div>

      {canManage && (
        <div className="border-t border-token p-3 shrink-0">
          <button
            type="button"
            className="btn btn-primary w-full flex items-center justify-center gap-2"
            onClick={() => setCreating(true)}
            title="Создать новый server_account"
          >
            <Plus className="w-4 h-4" /> Создать пользователя
          </button>
        </div>
      )}
    </aside>
  );

  return (
    <Shell breadcrumb="server_service / server users" middle={aside}>
      {selectedAccount ? (
        <AccountWorkzone
          account={selectedAccount}
          canReveal={canReveal}
          canOperate={canOperate}
          canManage={canManage}
          serverName={serverName}
          allServers={servers}
          onChanged={() => accountsQ.refetch()}
          onDeleted={() => {
            selectId(null);
            accountsQ.refetch();
          }}
        />
      ) : (
        <EmptyPane hasAny={allAccounts.length > 0} />
      )}

      {creating && (
        <AccountCreateModal
          servers={servers}
          onClose={() => setCreating(false)}
          onCreated={(a) => {
            setCreating(false);
            toast.success(`Аккаунт ${a.login} создан`);
            accountsQ.refetch();
            selectId(a.id);
          }}
        />
      )}

      {discovering && (
        <OsUsersDiscoveryModal
          servers={servers}
          serverName={serverName}
          onClose={() => setDiscovering(false)}
          onImported={() => accountsQ.refetch()}
          onIgnored={() => {}}
          onLinked={() => accountsQ.refetch()}
          onScanStarted={handleScanStarted}
        />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Создание аккаунта — модалка (флотовый список → удобнее оверлей, чем inline)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Форма заведения нового server_account во fleet-вьюхе. В отличие от вкладки
 * сервера, где сервер один и подставляется автоматически, тут можно сразу
 * привязать аккаунт к нескольким серверам отдела (чекбоксы, минимум один).
 * Создаёт только запись в БД — provision (useradd) на боксы делается отдельно
 * кнопкой в секции «Серверы аккаунта».
 */
function AccountCreateModal({
  servers,
  onClose,
  onCreated,
}: {
  servers: Server[];
  onClose: () => void;
  onCreated: (account: ServerAccount) => void;
}) {
  const [login, setLogin] = useState("");
  const [serverIds, setServerIds] = useState<string[]>([]);
  const [hasSudo, setHasSudo] = useState(false);
  const [groups, setGroups] = useState("");
  const [shell, setShell] = useState("");
  const [password, setPassword] = useState("");
  const [sshChoice, setSshChoice] = useState<SshKeyChoice>("generate");
  const [sshPublicKey, setSshPublicKey] = useState("");
  const [sshPrivateKey, setSshPrivateKey] = useState("");
  // Приватный ключ чувствительный — по умолчанию ввод скрыт, показываем по клику.
  const [showPrivate, setShowPrivate] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();

  function toggleServer(id: string) {
    setServerIds((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    const loginValue = login.trim();
    if (!loginValue) {
      setErr("Укажите login.");
      return;
    }
    const unixGroups = groups
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    const passwordValue = password.trim();
    const validationErr =
      validateLogin(loginValue) ??
      validateUnixGroups(unixGroups) ??
      (passwordValue ? validatePassword(passwordValue) : null);
    if (validationErr) {
      setErr(validationErr);
      return;
    }
    const pubKey = sshPublicKey.trim();
    // Приватный ключ кодируем как есть (PEM/OpenSSH чувствителен к завершающему
    // переводу строки); trim — только для проверки «задан/не задан» и формата.
    const hasPrivKey = sshPrivateKey.trim().length > 0;
    if (sshChoice === "supply") {
      if (!pubKey) {
        setErr("Вставьте публичный SSH-ключ или выберите другой режим.");
        return;
      }
      if (hasPrivKey) {
        const keyErr = validateSshPrivateKey(sshPrivateKey);
        if (keyErr) {
          setErr(keyErr);
          return;
        }
      }
    }
    setErr(null);
    setPending(true);
    try {
      const created = await accountsApi.createAccount({
        login: loginValue,
        server_ids: serverIds,
        has_sudo: hasSudo,
        unix_groups: unixGroups,
        shell: shell.trim() || null,
        password: password.trim() || null,
        ...(sshChoice !== "none"
          ? {
              ssh_mode: sshChoice,
              ssh_public_key: sshChoice === "supply" ? pubKey : null,
              ...(sshChoice === "supply" && hasPrivKey
                ? { ssh_private_key_b64: toBase64(sshPrivateKey) }
                : {}),
            }
          : {}),
      });
      // Приватный ключ на экран не выводим — он уже сохранён в хранилище.
      // Скачать его можно позже кнопкой «Скачать приватный ключ» в карточке
      // аккаунта (reveal через view_password).
      if (created.ssh_private_key) {
        toast.success(
          "SSH-ключ сохранён. Скачать приватный ключ можно позже кнопкой «Скачать приватный ключ» в карточке аккаунта.",
        );
      }
      onCreated(created);
    } catch (e) {
      setErr(handleCreateError(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Plus className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Создать пользователя
            </Dialog.Title>
          </div>

          <form onSubmit={submit}>
            <div className="modal-body flex flex-col gap-3">
              <Dialog.Description className="text-sm text-dim">
                Заводит server_account в БД и привязывает к выбранным серверам.
                Серверы можно не выбирать — тогда учётка заводится как хранимый
                креден и привязывается к серверам позже. OS-юзер на боксах не
                создаётся — для этого Provision в секции «Серверы аккаунта».
              </Dialog.Description>

              {err && <div className="alert-danger text-sm">{err}</div>}

              <label className="flex flex-col gap-1 text-sm">
                <span className="field-label">login *</span>
                <input
                  className="field-input mono"
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                  required
                  maxLength={128}
                  placeholder="dbos-svc"
                />
              </label>

              <div className="flex flex-col gap-1 text-sm">
                <span className="field-label flex items-center gap-1">
                  <ServerIcon className="w-3.5 h-3.5" /> Серверы
                  <span className="italic font-normal text-dim">
                    необязательно
                  </span>
                </span>
                {servers.length === 0 ? (
                  <div className="text-xs text-dim py-2">
                    Нет доступных серверов отдела.
                  </div>
                ) : (
                  <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto border border-token rounded p-2">
                    {servers.map((s) => (
                      <label
                        key={s.id}
                        className="inline-flex items-center gap-2 text-sm py-0.5"
                      >
                        <input
                          type="checkbox"
                          checked={serverIds.includes(s.id)}
                          onChange={() => toggleServer(s.id)}
                        />
                        <span className="mono truncate">
                          {s.display_name || s.hostname}
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <label className="inline-flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={hasSudo}
                  onChange={(e) => setHasSudo(e.target.checked)}
                />
                <span>выдать sudo (has_sudo)</span>
              </label>

              <label className="flex flex-col gap-1 text-sm">
                <span className="field-label">
                  unix_groups <span className="italic">csv: docker, wheel</span>
                </span>
                <input
                  className="field-input mono"
                  value={groups}
                  onChange={(e) => setGroups(e.target.value)}
                  placeholder="docker, wheel"
                />
              </label>

              <label className="flex flex-col gap-1 text-sm">
                <span className="field-label">shell</span>
                <input
                  className="field-input mono"
                  value={shell}
                  onChange={(e) => setShell(e.target.value)}
                  placeholder="/bin/bash"
                />
              </label>

              <label className="flex flex-col gap-1 text-sm">
                <span className="field-label">password</span>
                <input
                  className="field-input mono"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="пусто — backend сгенерирует сам"
                  autoComplete="new-password"
                />
                <span className="text-[11px] text-dim">
                  Если задаёте свой — минимум 8 символов, буквы и цифры. Пусто —
                  backend сгенерирует подходящий сам.
                </span>
              </label>

              <div className="flex flex-col gap-1 text-sm">
                <span className="field-label flex items-center gap-1">
                  <KeySquare className="w-3.5 h-3.5" /> SSH-ключ
                </span>
                <div className="flex flex-col gap-0.5 border border-token rounded p-2">
                  {(
                    [
                      ["none", "без ключа"],
                      ["generate", "сгенерировать (скачать приватный ключ потом из карточки)"],
                      ["supply", "вставить существующий ключ (публичный + опц. приватный)"],
                    ] as [SshKeyChoice, string][]
                  ).map(([value, label]) => (
                    <label
                      key={value}
                      className="inline-flex items-center gap-2 text-sm py-0.5"
                    >
                      <input
                        type="radio"
                        name="ssh-key-mode"
                        checked={sshChoice === value}
                        onChange={() => {
                          setSshChoice(value);
                          // Уходим из supply — не держим приватный ключ в памяти формы.
                          if (value !== "supply") {
                            setSshPrivateKey("");
                            setShowPrivate(false);
                          }
                        }}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
                {sshChoice === "supply" && (
                  <div className="flex flex-col gap-3 mt-1">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] text-dim">
                          Публичный ключ *
                        </span>
                        <FileLoadButton
                          label="Загрузить публичный из файла"
                          accept=".pub,text/plain"
                          onText={(text) => setSshPublicKey(text.trim())}
                          onError={() =>
                            setErr("Не удалось прочитать файл публичного ключа.")
                          }
                        />
                      </div>
                      <textarea
                        className="field-input mono text-xs h-20 resize-none"
                        value={sshPublicKey}
                        onChange={(e) => setSshPublicKey(e.target.value)}
                        placeholder="ssh-ed25519 AAAA… comment"
                        aria-label="Публичный SSH-ключ"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] text-dim">
                          Приватный ключ{" "}
                          <span className="italic">необязательно</span>
                        </span>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            className="btn btn-sm flex items-center gap-1"
                            onClick={() => setShowPrivate((v) => !v)}
                            title={
                              showPrivate
                                ? "Скрыть приватный ключ"
                                : "Показать/ввести приватный ключ"
                            }
                          >
                            {showPrivate ? (
                              <>
                                <EyeOff className="w-3.5 h-3.5" /> Скрыть
                              </>
                            ) : (
                              <>
                                <Eye className="w-3.5 h-3.5" /> Ввести вручную
                              </>
                            )}
                          </button>
                          <FileLoadButton
                            label="Загрузить приватный из файла"
                            accept=".pem,.key,text/plain"
                            onText={(text) => setSshPrivateKey(text)}
                            onError={() =>
                              setErr(
                                "Не удалось прочитать файл приватного ключа.",
                              )
                            }
                          />
                        </div>
                      </div>
                      {showPrivate ? (
                        <textarea
                          className="field-input mono text-xs h-24 resize-none"
                          value={sshPrivateKey}
                          onChange={(e) => setSshPrivateKey(e.target.value)}
                          placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                          aria-label="Приватный SSH-ключ"
                          autoComplete="off"
                          spellCheck={false}
                        />
                      ) : (
                        <div className="text-[11px] text-dim border border-token rounded px-2 py-1.5 flex items-center gap-2">
                          <KeyRound className="w-3.5 h-3.5 shrink-0" />
                          {sshPrivateKey.trim()
                            ? "Приватный ключ задан (скрыт). Загрузите файл или нажмите «Ввести вручную», чтобы изменить."
                            : "Приватный ключ не задан. Загрузите файл или нажмите «Ввести вручную»."}
                        </div>
                      )}
                      <span className="text-[11px] text-dim">
                        Приватный ключ нужен, чтобы платформа могла подключаться к
                        этому аккаунту через веб-консоль. Без него консоль для
                        аккаунта недоступна. Ключ хранится зашифрованным и наружу
                        больше не отдаётся.
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn flex items-center gap-1"
                onClick={onClose}
                disabled={pending}
              >
                <X className="w-4 h-4" /> Отмена
              </button>
              <button
                type="submit"
                className="btn btn-primary flex items-center gap-1"
                disabled={pending || !login.trim()}
              >
                <Plus className="w-4 h-4" />
                {pending ? "Создаём…" : "Создать"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * Сообщение по ошибке создания: 403 — нет прав, 409 — login уже занят на одном
 * из серверов, остальное — общий envelope (включая 400/422 валидации).
 */
function handleCreateError(e: unknown): string {
  const policy = passwordPolicyError(e);
  if (policy) return policy;
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав для создания аккаунта.";
    if (e.status === 409) {
      return apiErrMsg(e, "Конфликт: login уже занят на одном из серверов.");
    }
  }
  return apiErrMsg(e, "Создание не удалось");
}

// ───────────────────────────────────────────────────────────────────────────

function AccountRow({
  account,
  active,
  serverName,
  onSelect,
}: {
  account: ServerAccount;
  active: boolean;
  serverName: (id: string) => string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
      title="Открыть карточку аккаунта"
    >
      <div className="flex items-center gap-2">
        <User className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate mono">{account.login}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <ServerIcon className="w-3 h-3 shrink-0" />
            <span className="truncate">
              {account.server_ids.length > 0
                ? account.server_ids.map(serverName).join(", ")
                : "не привязан"}
            </span>
          </div>
        </div>
        {account.has_sudo && (
          <span className="badge badge-warn flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" /> sudo
          </span>
        )}
        <SourceBadge source={account.source} />
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Workzone — детальный просмотр + edit / rotate / delete (правая панель)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Рабочая зона одного аккаунта fleet-вида. Server-agnostic операции — правка
 * атрибутов (PATCH), ротация пароля в БД и hard-delete — плюс секция «Серверы
 * аккаунта»: per-server provision/update_on_host/deprovision/unbind и привязка
 * нового сервера. Финальные 403/404/409/503 приходят с backend'а; UI-гейты —
 * первичный визуальный слой.
 */
function AccountWorkzone({
  account,
  canReveal,
  canOperate,
  canManage,
  serverName,
  allServers,
  onChanged,
  onDeleted,
}: {
  account: ServerAccount;
  canReveal: boolean;
  canOperate: boolean;
  canManage: boolean;
  serverName: (id: string) => string;
  allServers: Server[];
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const { prompt, confirm } = useConfirm();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const linkedUserLabel = useUserLabel(account.linked_user_id);
  const createdByLabel = useUserLabel(account.created_by);

  // Ревизия атрибутов: триггерим users/inventory по серверу, поллим задачу.
  // result.diffs показываем в модалке. Если задача успела закрыться быстро —
  // открываем модалку сразу; если долго — висит кликабельное уведомление.
  const revision = useTaskOutcome();
  // server_id, по которому запущена текущая ревизия — нужен для adopt_from_host.
  const [revisionServer, setRevisionServer] = useState<string | null>(null);
  // Модалку открываем явно: либо сразу (быстрый таск), либо по клику на нотис.
  const [revisionModalOpen, setRevisionModalOpen] = useState(false);
  // Авто-открытие срабатывает один раз на задачу — чтобы повторный поллинг-tick
  // не переоткрывал закрытую вручную модалку.
  const [autoOpenedTask, setAutoOpenedTask] = useState<string | null>(null);

  // Смена выбранного аккаунта — сбрасываем локальное состояние зоны (edit-режим,
  // предыдущую ошибку и трек ревизии), чтобы не тащить их на другой аккаунт.
  useEffect(() => {
    setEditing(false);
    setErr(null);
    setRevisionServer(null);
    setRevisionModalOpen(false);
    setAutoOpenedTask(null);
    revision.reset();
    // revision.reset стабилен (useCallback) — в deps только смена аккаунта.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.id]);

  const revisionDiffs = useMemo<RevisionAccountDiff[]>(() => {
    const result = revision.tracked?.result;
    if (!result || typeof result !== "object") return [];
    const raw = (result as { diffs?: unknown }).diffs;
    return Array.isArray(raw) ? (raw as RevisionAccountDiff[]) : [];
  }, [revision.tracked?.result]);

  const revisionDone =
    revision.tracked != null &&
    !revision.tracked.polling &&
    revision.tracked.status === "succeeded";

  // Быстрая ревизия: как только задача закрылась успешно — открываем модалку
  // автоматически (одноразово). Долгая — пользователь сам кликнет нотис.
  useEffect(() => {
    if (!revisionDone || revision.tracked == null) return;
    if (autoOpenedTask === revision.tracked.taskId) return;
    setAutoOpenedTask(revision.tracked.taskId);
    setRevisionModalOpen(true);
  }, [revisionDone, revision.tracked, autoOpenedTask]);

  async function handleRevision(serverId: string) {
    if (!canOperate) return;
    revision.reset();
    setRevisionModalOpen(false);
    setAutoOpenedTask(null);
    setRevisionServer(serverId);
    try {
      const res = await usersInventory(serverId);
      revision.track("revision", res.task_id, res.status);
      toast.info(`Ревизия запущена (${serverName(serverId)})`);
    } catch (e) {
      const msg = handleDispatchError(e);
      setRevisionServer(null);
      toast.error(msg);
    }
  }

  async function handleRotate() {
    if (pending || !canOperate) return;
    if (
      !(await confirm({
        title: "Ротация пароля",
        message: `Сгенерировать новый пароль для ${account.login} в БД? На серверы изменение не уезжает — для apply используйте worker-rotate на вкладке «Аккаунты» сервера.`,
        confirmLabel: "Ротировать",
      }))
    )
      return;
    setErr(null);
    setPending(true);
    try {
      const res = await accountsApi.rotateAccountUserInitiated(account.id);
      toast.success(
        `Пароль ${res.login} ротирован в БД (${formatMskShort(res.rotated_at)})`,
      );
      onChanged();
    } catch (e) {
      const msg = handleActionError(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    if (pending || !canManage) return;
    const { ok } = await prompt({
      title: "Удалить аккаунт",
      message: `Удалить аккаунт ${account.login}? Hard-delete во всех ${account.server_ids.length} серверах. OS-аккаунт на боксах не сносится — для этого Deprovision на вкладке сервера. Операция необратима.`,
      reason: true,
      reasonLabel: "Причина удаления",
      reasonRequired: true,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setErr(null);
    setPending(true);
    try {
      await accountsApi.deleteAccount(account.id);
      toast.success(`Аккаунт ${account.login} удалён`);
      onDeleted();
    } catch (e) {
      const msg = handleActionError(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <div className="border-b border-token px-5 py-4 shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <User className="w-5 h-5 text-accent shrink-0" />
          <div className="flex-1 min-w-0">
            <h1 className="text-base font-semibold mono truncate">
              {account.login}
            </h1>
            <div className="text-[11px] text-dim truncate">
              {account.server_ids.length > 0
                ? account.server_ids.map(serverName).join(", ")
                : "не привязан"}
            </div>
          </div>
          {account.has_sudo && (
            <span className="badge badge-warn flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" /> sudo
            </span>
          )}
          {!account.is_active && (
            <span className="badge badge-warn">inactive</span>
          )}
          <SourceBadge source={account.source} />
        </div>

        {/* Управляющие кнопки — наверху, чтобы не скроллить за ними. */}
        {!editing && (
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <button
              type="button"
              className="btn btn-sm btn-primary flex items-center gap-1"
              disabled={pending || !canManage}
              title={canManage ? undefined : "Нет прав на редактирование"}
              onClick={() => setEditing(true)}
            >
              <Edit3 className="w-4 h-4" /> Редактировать
            </button>
            <button
              className="btn btn-sm flex items-center gap-1"
              disabled={pending || !canOperate}
              title={canOperate ? "Ротировать пароль в БД" : "Нет прав"}
              onClick={handleRotate}
              type="button"
            >
              <RotateCw className="w-4 h-4" /> Ротировать (БД)
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger flex items-center gap-1"
              disabled={pending || !canManage}
              title={canManage ? undefined : "Нет прав на удаление"}
              onClick={handleDelete}
            >
              <Trash2 className="w-4 h-4" /> Удалить
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-auto p-5 flex flex-col gap-4 max-w-3xl">
        {err && <div className="alert-danger text-sm">{err}</div>}

        {/* Долгая ревизия: уведомление со статусом; по клику (когда готово) —
            открыть модалку diff. Быстрая открывает модалку сама. */}
        {revision.tracked && (
          <RevisionNotice
            tracked={revision.tracked}
            diffCount={revisionDiffs.length}
            serverName={revisionServer ? serverName(revisionServer) : ""}
            onOpen={() => setRevisionModalOpen(true)}
            onDismiss={() => {
              revision.reset();
              setRevisionServer(null);
              setAutoOpenedTask(null);
            }}
          />
        )}

        {editing ? (
          <AccountEditForm
            account={account}
            canRecreate={canManage}
            onCancel={() => setEditing(false)}
            onSaved={() => {
              setEditing(false);
              toast.success("Аккаунт обновлён");
              onChanged();
            }}
            onError={(e) => {
              const msg = handleActionError(e);
              setErr(msg);
              toast.error(msg);
            }}
          />
        ) : (
          <div>
            <div className="text-xs uppercase text-dim mb-2">Профиль</div>
              <StatRow
                k="account_id"
                v={<span className="mono">{account.id}</span>}
              />
              <StatRow
                k="login"
                v={<span className="mono">{account.login}</span>}
              />
              <StatRow
                k="серверы"
                v={
                  <span className="break-words">
                    {account.server_ids.length > 0
                      ? account.server_ids.map(serverName).join(", ")
                      : "не привязан"}
                  </span>
                }
              />
              <StatRow k="source" v={account.source} />
              <StatRow k="has_sudo" v={account.has_sudo ? "да" : "нет"} />
              <StatRow
                k="unix_groups"
                v={
                  account.unix_groups.length === 0 ? (
                    <span className="text-dim italic">—</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {account.unix_groups.map((g) => (
                        <span key={g} className="badge mono">
                          {g}
                        </span>
                      ))}
                    </div>
                  )
                }
              />
              <StatRow
                k="shell"
                v={
                  account.shell ? (
                    <span className="mono">{account.shell}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="home_dir"
                v={
                  account.home_dir ? (
                    <span className="mono">{account.home_dir}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="linked_user"
                v={
                  account.linked_user_id ? (
                    <span title={account.linked_user_id}>{linkedUserLabel}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="is_active"
                v={account.is_active ? "активен" : "неактивен"}
              />
              <StatRow
                k="created_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.created_at)}
                  </span>
                }
              />
              <StatRow
                k="updated_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.updated_at)}
                  </span>
                }
              />
              <StatRow
                k="password_rotated_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.password_rotated_at)}
                  </span>
                }
              />
              <StatRow
                k="created_by"
                v={
                  account.created_by ? (
                    <span title={account.created_by}>{createdByLabel}</span>
                  ) : (
                    <span className="text-dim italic">system</span>
                  )
                }
              />
          </div>
        )}

        {/* Пароль и SSH-ключ — управление кредами не должно пропадать при
            переключении в edit-режим: держим их вне ветки `editing`. */}
        <PasswordRevealCard
          key={account.id}
          account={account}
          canReveal={canReveal}
          canOperate={canOperate}
          serverName={serverName}
          onChanged={onChanged}
        />

        <SshKeySection
          account={account}
          canOperate={canOperate}
          canReveal={canReveal}
          onChanged={onChanged}
        />

        {!editing && (
          <ServersSection
            account={account}
            canOperate={canOperate}
            serverName={serverName}
            allServers={allServers}
            onChanged={onChanged}
            onRevision={handleRevision}
            revisionBusyServer={
              revision.tracked?.polling ? revisionServer : null
            }
          />
        )}
      </div>

      {revisionServer && (
        <RevisionDiffModal
          open={revisionModalOpen}
          serverId={revisionServer}
          serverName={serverName(revisionServer)}
          diffs={revisionDiffs}
          onClose={() => setRevisionModalOpen(false)}
          onApplied={onChanged}
        />
      )}
    </section>
  );
}

/**
 * Уведомление о ходе ревизии в рабочей зоне. Пока задача поллится — «запущена»,
 * по завершении — кликабельный итог (открыть модалку diff) либо ошибка.
 */
function RevisionNotice({
  tracked,
  diffCount,
  serverName,
  onOpen,
  onDismiss,
}: {
  tracked: NonNullable<ReturnType<typeof useTaskOutcome>["tracked"]>;
  diffCount: number;
  serverName: string;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const where = serverName ? ` (${serverName})` : "";
  if (tracked.polling) {
    return (
      <div className="alert text-sm flex items-center gap-2" role="status">
        <RotateCw className="w-4 h-4 animate-spin shrink-0" />
        <span className="flex-1">Ревизия запущена{where} — ждём результат…</span>
      </div>
    );
  }
  if (tracked.error || tracked.status === "failed") {
    return (
      <div className="alert-danger text-sm flex items-start gap-2">
        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
        <span className="flex-1">
          Ревизия{where} не удалась: {tracked.error ?? "задача завершилась ошибкой"}
        </span>
        <button type="button" className="btn btn-sm" onClick={onDismiss}>
          Скрыть
        </button>
      </div>
    );
  }
  return (
    <div className="alert text-sm flex items-center gap-2 flex-wrap" role="status">
      <ScanSearch className="w-4 h-4 shrink-0 text-accent" />
      <span className="flex-1">
        Ревизия{where} готова:{" "}
        {diffCount > 0
          ? `расхождений — ${diffCount}`
          : "расхождений нет"}
      </span>
      <button
        type="button"
        className="btn btn-sm btn-primary"
        onClick={onOpen}
      >
        Открыть
      </button>
      <button type="button" className="btn btn-sm" onClick={onDismiss}>
        Скрыть
      </button>
    </div>
  );
}

/**
 * Индикатор последней инвентаризации OS-юзеров в тулбаре fleet-списка. Скан
 * асинхронный (dispatch → task_id → polling), и его результат раньше пропадал
 * из точки запуска, если модалку закрывали до конца долгой задачи. Здесь статус
 * (идёт/готово/ошибка) и кнопка «Открыть результат» ведут на `/tasks/{id}`, где
 * результат живёт независимо от модалки.
 */
function InventoryStatusBar({
  tracked,
  serverName,
  onOpen,
  onDismiss,
}: {
  tracked: NonNullable<ReturnType<typeof useTaskOutcome>["tracked"]>;
  serverName: string;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const where = serverName ? ` (${serverName})` : "";
  if (tracked.polling) {
    return (
      <div
        className="mt-2 alert text-[11px] flex items-center gap-2"
        role="status"
      >
        <RotateCw className="w-3.5 h-3.5 animate-spin shrink-0" />
        <span className="flex-1">Поиск на ОС{where} идёт…</span>
        <button type="button" className="btn btn-sm" onClick={onOpen}>
          Открыть результат
        </button>
      </div>
    );
  }
  if (tracked.error || tracked.status === "failed") {
    return (
      <div
        className="mt-2 alert-danger text-[11px] flex items-start gap-2"
        role="status"
      >
        <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span className="flex-1">
          Поиск на ОС{where} не удался: {tracked.error ?? "задача завершилась ошибкой"}
        </span>
        <button type="button" className="btn btn-sm" onClick={onDismiss}>
          Скрыть
        </button>
      </div>
    );
  }
  return (
    <div
      className="mt-2 alert text-[11px] flex items-center gap-2 flex-wrap"
      role="status"
    >
      <ScanSearch className="w-3.5 h-3.5 shrink-0 text-accent" />
      <span className="flex-1">Поиск на ОС{where} готов.</span>
      <button
        type="button"
        className="btn btn-sm btn-primary"
        onClick={onOpen}
      >
        Открыть результат
      </button>
      <button type="button" className="btn btn-sm" onClick={onDismiss}>
        Скрыть
      </button>
    </div>
  );
}

/**
 * Дружелюбное сообщение по ошибке мутации. 403 — нет прав в server-зоне;
 * остальное — обычный envelope.
 */
function handleActionError(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "Недостаточно прав для этой операции.";
  }
  if (e instanceof ApiError && e.status === 404) {
    return "Аккаунт не найден (возможно, уже удалён).";
  }
  return apiErrMsg(e, "Операция не удалась");
}

/**
 * Дружелюбное сообщение по ошибке per-server dispatch'а. Сверх общих 403/404
 * раскрывает 409 (конфликт — login занят / нет пароля у discovered / нечего
 * отвязывать) и 503 (worker недоступен).
 */
function handleDispatchError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав для этой операции.";
    if (e.status === 404) return "Аккаунт или сервер не найдены.";
    if (e.status === 409) {
      return apiErrMsg(
        e,
        "Конфликт: login занят, нет пароля у discovered-аккаунта или нечего отвязывать.",
      );
    }
    if (e.status === 503) {
      return "Worker недоступен — задача не поставлена. Повторите позже.";
    }
  }
  return apiErrMsg(e, "Операция не удалась");
}

/**
 * Presence-бэйдж сервера в секции «Серверы аккаунта». В fleet-вьюхе нет
 * отдельного флага «present_on_host», поэтому presence выводим из тех же
 * признаков, что и `provisionBadge` на вкладке сервера: привязка
 * (`server_ids`) + managed/discovered-without-password.
 */
function presenceBadge(
  a: ServerAccount,
  serverId: string,
): { label: string; kind: "ok" | "warn" | "danger" | "" } {
  const linked = a.server_ids.includes(serverId);
  if (!linked) return { label: "не привязан", kind: "danger" };
  if (a.source === "discovered" && !a.password_rotated_at) {
    return { label: "discovered", kind: "warn" };
  }
  return { label: "привязан", kind: "ok" };
}

// ───────────────────────────────────────────────────────────────────────────
// Серверы аккаунта — per-server provision/update/deprovision/unbind + bind
// ───────────────────────────────────────────────────────────────────────────

/**
 * Секция управления присутствием аккаунта на конкретных боксах. Edit-форма выше
 * меняет атрибуты в БД с авто-fan-out'ом `update_on_host`; здесь — точечный
 * lifecycle OS-юзера на каждом сервере (`useradd`/`usermod`/`userdel`), отвязка
 * связки и привязка нового сервера отдела.
 */
function ServersSection({
  account,
  canOperate,
  serverName,
  allServers,
  onChanged,
  onRevision,
  revisionBusyServer,
}: {
  account: ServerAccount;
  canOperate: boolean;
  serverName: (id: string) => string;
  allServers: Server[];
  onChanged: () => void;
  /** Запуск ревизии (users/inventory) по конкретному серверу. */
  onRevision: (serverId: string) => void;
  /** Сервер, по которому ревизия сейчас крутится (или null). */
  revisionBusyServer: string | null;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  // Pending держим по конкретному серверу — чтобы не гасить кнопки всех строк
  // на время одной операции.
  const [busyServer, setBusyServer] = useState<string | null>(null);
  const [bindTarget, setBindTarget] = useState("");
  const [binding, setBinding] = useState(false);

  // Discovered-аккаунт без сохранённого пароля backend отбивает на provision
  // (ACCOUNT_HAS_NO_PASSWORD) — provision уйдёт только с force_password.
  const discoveredNoPassword =
    account.source === "discovered" && !account.password_rotated_at;

  // Серверы отдела, к которым аккаунт ещё не привязан — кандидаты на bind.
  const bindCandidates = useMemo(
    () => allServers.filter((s) => !account.server_ids.includes(s.id)),
    [allServers, account.server_ids],
  );

  async function runOn(
    serverId: string,
    fn: () => Promise<unknown>,
    ok: string,
  ) {
    if (busyServer) return;
    setBusyServer(serverId);
    try {
      await fn();
      toast.success(ok);
      onChanged();
    } catch (e) {
      toast.error(handleDispatchError(e));
    } finally {
      setBusyServer(null);
    }
  }

  async function handleProvision(serverId: string) {
    if (!canOperate) return;
    let force = false;
    if (discoveredNoPassword) {
      // Без пароля useradd не уедет — спрашиваем подтверждение на генерацию.
      if (
        !(await confirm({
          title: "Provision discovered-аккаунта",
          message: `У ${account.login} нет сохранённого пароля (discovered). Сгенерировать новый и применить на боксе через chpasswd (force_password)?`,
          confirmLabel: "Provision + force_password",
        }))
      )
        return;
      force = true;
    }
    void runOn(
      serverId,
      () =>
        accountsApi.provisionOnHost(serverId, account.id, {
          force_password: force,
        }),
      `Provision-task поставлен в очередь (${serverName(serverId)})`,
    );
  }

  async function handleDeprovision(serverId: string) {
    if (!canOperate) return;
    const { ok, removeHome } = await confirmDeprovision(serverId);
    if (!ok) return;
    void runOn(
      serverId,
      () =>
        accountsApi.deprovisionOnHost(serverId, account.id, {
          remove_home: removeHome,
        }),
      `Deprovision-task поставлен в очередь (${serverName(serverId)})`,
    );
  }

  // remove_home выбирается без отдельного чекбокса: ConfirmDialog не несёт
  // boolean-опции, поэтому разводим на два варианта подтверждения.
  async function confirmDeprovision(
    serverId: string,
  ): Promise<{ ok: boolean; removeHome: boolean }> {
    const removeHome = await confirm({
      title: "Deprovision",
      message: `Удалить OS-пользователя ${account.login} с сервера ${serverName(serverId)}?\n\nУдалить и home-каталог (userdel --remove)? «Отмена» снесёт только учётку, дальше будет ещё одно подтверждение.`,
      confirmLabel: "Удалить вместе с home",
      cancelLabel: "Только учётку",
      danger: true,
    });
    if (removeHome) return { ok: true, removeHome: true };
    // Пользователь выбрал «только учётку» — подтверждаем сам факт удаления.
    const justUser = await confirm({
      title: "Deprovision",
      message: `Удалить OS-пользователя ${account.login} с сервера ${serverName(serverId)} без удаления home?`,
      confirmLabel: "Deprovision",
      danger: true,
    });
    return { ok: justUser, removeHome: false };
  }

  async function handleUnbind(serverId: string) {
    if (!canOperate) return;
    if (
      !(await confirm({
        title: "Отвязать сервер",
        message: `Отвязать аккаунт ${account.login} от ${serverName(serverId)}? Связка снимется, OS-юзер на боксе будет удалён (userdel), если он там стоял.`,
        confirmLabel: "Отвязать",
        danger: true,
      }))
    )
      return;
    void runOn(
      serverId,
      () => accountsApi.unbindAccountServer(account.id, serverId),
      `Аккаунт отвязан от ${serverName(serverId)}`,
    );
  }

  async function handleBind() {
    if (!canOperate || !bindTarget || binding) return;
    setBinding(true);
    try {
      await accountsApi.bindAccountServers(account.id, {
        server_ids: [bindTarget],
      });
      toast.success(`Аккаунт привязан к ${serverName(bindTarget)}`);
      setBindTarget("");
      onChanged();
    } catch (e) {
      toast.error(handleDispatchError(e));
    } finally {
      setBinding(false);
    }
  }

  return (
    <div className="card">
      <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
        <ServerIcon className="w-3 h-3" /> Серверы аккаунта
      </div>
      <div className="text-xs text-dim mb-3">
        Per-server lifecycle OS-юзера: Provision (`useradd`), Update on host
        (`usermod`), Deprovision (`userdel`), Unbind (снять связку без удаления
        на боксе). Атрибуты (sudo/группы/shell) меняются через «Редактировать» с
        авто-рассылкой на привязанные серверы.
      </div>

      {account.server_ids.length === 0 ? (
        <div className="text-xs text-dim italic mb-3">
          Аккаунт ни к одному серверу не привязан.
        </div>
      ) : (
        <div className="flex flex-col gap-2 mb-3">
          {account.server_ids.map((sid) => {
            const presence = presenceBadge(account, sid);
            const busy = busyServer === sid;
            const disabled = busy || !!busyServer || !canOperate;
            const noPrivReason = canOperate ? undefined : "Нет прав";
            return (
              <div
                key={sid}
                className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap"
              >
                <ServerIcon className="w-4 h-4 text-dim shrink-0" />
                <span className="text-sm mono flex-1 min-w-[140px] truncate">
                  {serverName(sid)}
                </span>
                <span
                  className={`badge${presence.kind ? ` badge-${presence.kind}` : ""}`}
                >
                  {presence.label}
                </span>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  disabled={disabled || revisionBusyServer === sid}
                  title={
                    noPrivReason ??
                    "Сверить атрибуты OS-юзера с БД (users/inventory)"
                  }
                  onClick={() => onRevision(sid)}
                  type="button"
                >
                  <ScanSearch
                    className={`w-3.5 h-3.5 ${revisionBusyServer === sid ? "animate-spin" : ""}`}
                  />{" "}
                  Ревизия
                </button>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  disabled={disabled}
                  title={
                    noPrivReason ??
                    (discoveredNoPassword
                      ? "useradd (запросит force_password)"
                      : "useradd на боксе")
                  }
                  onClick={() => handleProvision(sid)}
                  type="button"
                >
                  <Power className="w-3.5 h-3.5" /> Provision
                </button>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  disabled={disabled}
                  title={noPrivReason ?? "usermod синхронизирует атрибуты"}
                  onClick={() =>
                    runOn(
                      sid,
                      () => accountsApi.updateOnHost(sid, account.id),
                      `Update-on-host-task поставлен в очередь (${serverName(sid)})`,
                    )
                  }
                  type="button"
                >
                  <RotateCw className="w-3.5 h-3.5" /> Update on host
                </button>
                <button
                  className="btn btn-sm btn-danger flex items-center gap-1"
                  disabled={disabled}
                  title={noPrivReason ?? "userdel на боксе"}
                  onClick={() => handleDeprovision(sid)}
                  type="button"
                >
                  <Trash2 className="w-3.5 h-3.5" /> Deprovision
                </button>
                <button
                  className="btn btn-sm btn-danger flex items-center gap-1"
                  disabled={disabled}
                  title={noPrivReason ?? "Снять связку + userdel на боксе"}
                  onClick={() => handleUnbind(sid)}
                  type="button"
                >
                  <Unlink className="w-3.5 h-3.5" /> Unbind
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="border-t border-token pt-3 flex items-center gap-2 flex-wrap">
        <span className="text-xs text-dim">Привязать сервер:</span>
        <select
          className="surface-2 border border-token rounded px-2 py-1 text-sm flex-1 min-w-[160px]"
          value={bindTarget}
          disabled={!canOperate || binding || bindCandidates.length === 0}
          onChange={(e) => setBindTarget(e.target.value)}
        >
          <option value="">
            {bindCandidates.length === 0
              ? "— нет доступных серверов —"
              : "— выберите сервер —"}
          </option>
          {bindCandidates.map((s) => (
            <option key={s.id} value={s.id}>
              {s.display_name || s.hostname}
            </option>
          ))}
        </select>
        <button
          className="btn btn-sm btn-primary flex items-center gap-1"
          disabled={!canOperate || binding || !bindTarget}
          title={canOperate ? "Привязать аккаунт к серверу" : "Нет прав"}
          onClick={handleBind}
          type="button"
        >
          <Link2 className="w-3.5 h-3.5" /> {binding ? "Привязываем…" : "Привязать"}
        </button>
      </div>
    </div>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1 text-sm">
      <span className="text-xs text-dim w-44 shrink-0">{k}</span>
      <span className="flex-1 min-w-0 break-words">{v}</span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Edit form (PATCH has_sudo / unix_groups / shell)
// ───────────────────────────────────────────────────────────────────────────

function AccountEditForm({
  account,
  canRecreate,
  onCancel,
  onSaved,
  onError,
}: {
  account: ServerAccount;
  /** dep_admin / server.admin — доступно пересоздание залоченного login. */
  canRecreate: boolean;
  onCancel: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [login, setLoginValue] = useState(account.login);
  const [hasSudo, setHasSudo] = useState(account.has_sudo);
  const [groups, setGroups] = useState(account.unix_groups.join(", "));
  const [shell, setShell] = useState(account.shell ?? "");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 409 LOGIN_LOCKED при rename present-аккаунта — поднимаем кнопку пересоздания.
  const [loginLocked, setLoginLocked] = useState(false);

  const loginChanged = login.trim() !== account.login;

  // Сменить пароль ТОЛЬКО в БД. На серверы новый пароль не уезжает —
  // раскатка делается отдельной кнопкой «Применить на серверы» в карточке
  // (worker-rotate). Так старый пароль остаётся валидным для ещё не
  // обновлённых серверов до явного apply.
  async function applyPasswordChange(plain: string) {
    await accountsApi.rotateAccountUserInitiated(account.id, {
      password: plain,
    });
    toast.success(
      "Пароль сменён в БД. Примените его на серверы кнопкой «Применить на серверы».",
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    const loginValue = login.trim();
    const unixGroups = groups
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    const newPassword = password.trim();
    const validationErr =
      (loginChanged ? validateLogin(loginValue) : null) ??
      validateUnixGroups(unixGroups) ??
      (newPassword ? validatePassword(newPassword) : null);
    if (validationErr) {
      setErr(validationErr);
      return;
    }
    setErr(null);
    setLoginLocked(false);
    setPending(true);
    try {
      const body: ServerAccountUpdateRequest = {
        has_sudo: hasSudo,
        unix_groups: unixGroups,
        shell: shell.trim() || null,
      };
      if (loginChanged) body.login = loginValue;
      await accountsApi.updateAccount(account.id, body);
      const plain = password.trim();
      if (plain) await applyPasswordChange(plain);
      onSaved();
    } catch (e) {
      const policy = passwordPolicyError(e);
      // present-аккаунт нельзя переименовать обычным PATCH — backend отдаёт 409
      // LOGIN_LOCKED; предлагаем destructive-пересоздание отдельной кнопкой.
      if (policy) {
        setErr(policy);
      } else if (
        loginChanged &&
        e instanceof ApiError &&
        e.status === 409 &&
        (e.errorCode === "LOGIN_LOCKED" ||
          /LOGIN_LOCKED/i.test(apiErrMsg(e, "")))
      ) {
        setLoginLocked(true);
        setErr(
          "Логин занят OS-аккаунтом на сервере — обычное переименование невозможно. Доступно пересоздание (destructive).",
        );
      } else if (
        loginChanged &&
        e instanceof ApiError &&
        e.status === 409 &&
        (e.errorCode === "ACCOUNT_DUPLICATE" ||
          /ACCOUNT_DUPLICATE/i.test(apiErrMsg(e, "")))
      ) {
        // Другой аккаунт уже держит этот login на одном из серверов —
        // пересоздание не поможет, нужен другой login.
        setErr(
          "Логин уже занят другим аккаунтом на одном из серверов — выберите другой login.",
        );
      } else {
        setErr(handleActionError(e));
        onError(e);
      }
    } finally {
      setPending(false);
    }
  }

  async function handleRecreate() {
    if (pending) return;
    const loginValue = login.trim();
    if (!canRecreate) {
      setErr("Недостаточно прав на пересоздание (нужен dep_admin/server.admin).");
      return;
    }
    if (
      !(await confirm({
        title: "Пересоздать OS-аккаунт",
        message: `OS-аккаунт ${account.login} будет УДАЛЁН со всех ${account.server_ids.length} серверов вместе с домашним каталогом $HOME и всеми данными, затем создан заново под логином ${loginValue}. Данные не восстановить.`,
        confirmLabel: "Пересоздать (удалить $HOME)",
        danger: true,
      }))
    )
      return;
    setErr(null);
    setPending(true);
    try {
      // Ответ — сводка диспатча, а не сама карточка: считаем задачи и
      // перезапрашиваем аккаунт через onSaved (родитель дёргает refetch).
      const dispatch = await accountsApi.recreateLogin(account.id, {
        login: loginValue,
      });
      const dispatched =
        dispatch.deprovision.length + dispatch.provision.length;
      const skipped = dispatch.skipped.length;
      const plain = password.trim();
      if (plain) await applyPasswordChange(plain);
      if (skipped > 0) {
        toast.error(
          `OS-аккаунт пересоздаётся под логином ${dispatch.new_login}: задач — ${dispatched}, пропущено — ${skipped}.`,
        );
      } else {
        toast.success(
          `OS-аккаунт пересоздаётся под логином ${dispatch.new_login}: задач — ${dispatched}.`,
        );
      }
      onSaved();
    } catch (e) {
      const policy = passwordPolicyError(e);
      if (policy) {
        setErr(policy);
      } else if (e instanceof ApiError && e.status === 403) {
        setErr("Недостаточно прав на пересоздание (нужен dep_admin/server.admin).");
      } else {
        setErr(handleActionError(e));
        onError(e);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <div className="text-xs uppercase text-dim flex items-center gap-2">
        <Edit3 className="w-3 h-3" /> Редактирование
      </div>
      <div className="text-xs text-dim">
        При изменении атрибутов backend сам разошлёт `update_on_host` на
        привязанные серверы. Смена пароля пишется ТОЛЬКО в БД — на серверы он
        уезжает отдельно, кнопкой «Применить на серверы» в карточке пароля.
      </div>
      {err && <div className="alert-danger text-sm">{err}</div>}

      <FormRow label="login">
        <input
          className="input mono"
          value={login}
          onChange={(e) => {
            setLoginValue(e.target.value);
            setLoginLocked(false);
          }}
          maxLength={128}
          placeholder="dbos-svc"
        />
      </FormRow>
      {loginLocked && (
        <div className="alert-warn text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>
              Аккаунт present на серверах — переименование без пересоздания
              невозможно. Пересоздание удалит OS-аккаунт со всеми данными `$HOME`
              на всех серверах и создаст заново.
            </div>
            <button
              type="button"
              className="btn btn-sm btn-danger mt-2 flex items-center gap-1"
              disabled={pending || !canRecreate}
              title={
                canRecreate
                  ? undefined
                  : "Нужны права dep_admin / server.admin"
              }
              onClick={handleRecreate}
            >
              <AlertTriangle className="w-3.5 h-3.5" /> Пересоздать под новым
              логином
            </button>
          </div>
        </div>
      )}
      <FormRow label="sudo">
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={hasSudo}
            onChange={(e) => setHasSudo(e.target.checked)}
          />
          <span>has_sudo</span>
        </label>
      </FormRow>
      <FormRow label="unix_groups" hint="csv: docker, wheel">
        <input
          className="input mono"
          value={groups}
          onChange={(e) => setGroups(e.target.value)}
          placeholder="docker, wheel"
        />
      </FormRow>
      <FormRow label="shell">
        <input
          className="input mono"
          value={shell}
          onChange={(e) => setShell(e.target.value)}
          placeholder="/bin/bash"
        />
      </FormRow>
      <FormRow
        label="новый пароль"
        hint="пусто — не меняется; иначе минимум 8 символов, буквы и цифры (только в БД)"
      >
        <input
          className="input mono"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="оставьте пустым, чтобы не менять"
          autoComplete="new-password"
        />
      </FormRow>
      <div className="mt-2 flex gap-2 justify-end">
        <button type="button" className="btn" onClick={onCancel}>
          Отмена
        </button>
        <button type="submit" className="btn btn-primary" disabled={pending}>
          {pending ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </form>
  );
}

function FormRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-dim text-xs">
        {label}
        {hint && <span className="ml-2 italic">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Password reveal card (в правой рабочей зоне)
// ───────────────────────────────────────────────────────────────────────────

/** Декод base64 → plaintext с fallback'ом на исходную строку. */
function decodePassword(b64: string): string {
  try {
    return fromBase64(b64);
  } catch {
    return b64;
  }
}

/**
 * Reveal-блок пароля в рабочей зоне. По клику «показать» дёргает карточку
 * аккаунта (`getAccount`), декодит `password_b64` → plaintext. `null` →
 * понятная причина (нет view_password либо нет сохранённого пароля), 429 гасит
 * кнопку на retry-окно, 403 — отдельная причина. Сбрасывается при смене
 * аккаунта (key по account.id монтирует блок заново).
 *
 * Пока аккаунт в переходном состоянии `credentials_pending_apply` (новый
 * пароль сменён в БД, но раскатан не на все серверы) — backend отдаёт ещё и
 * `previous_password_b64`; показываем ОБА: новый (для обновлённых боксов) и
 * старый (для ещё не обновлённых). Apply на серверы — кнопками ниже
 * (worker-rotate на все / на конкретный сервер).
 */
function PasswordRevealCard({
  account,
  canReveal,
  canOperate,
  serverName,
  onChanged,
}: {
  account: ServerAccount;
  canReveal: boolean;
  canOperate: boolean;
  serverName: (id: string) => string;
  onChanged: () => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const { confirm } = useConfirm();
  const [plain, setPlain] = useState<string | null>(null);
  const [prevPlain, setPrevPlain] = useState<string | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [throttleUntil, setThrottleUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  // Сервер, на который сейчас идёт точечный apply (или "all").
  const [applyingServer, setApplyingServer] = useState<string | null>(null);
  // Разбивка последнего worker-rotate (dispatched / failed / partial_failure).
  const [dispatchResult, setDispatchResult] =
    useState<accountsApi.AccountRotateDispatchResponse | null>(null);

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);

  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));
  const shown = plain !== null;
  const pendingApply = account.credentials_pending_apply === true;

  // Discovered-аккаунт без ротации обычно не имеет ciphertext'а в БД — backend
  // вернёт null даже держателю view_password. Подсказка до запроса.
  const likelyNoPassword =
    account.source === "discovered" && !account.password_rotated_at;

  async function handleReveal() {
    if (revealing || throttleLeft > 0) return;
    setRevealing(true);
    setReason(null);
    try {
      const fresh = await accountsApi.getAccount(account.id);
      if (fresh.password_b64 === null) {
        setReason(
          likelyNoPassword
            ? "У аккаунта нет сохранённого пароля (discovered, без ротации)."
            : "Пароль скрыт: нет права view_password или пароль отсутствует.",
        );
        return;
      }
      setPlain(decodePassword(fresh.password_b64));
      setPrevPlain(
        fresh.previous_password_b64
          ? decodePassword(fresh.previous_password_b64)
          : null,
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 60;
        setThrottleUntil(Date.now() + secs * 1000);
        setNow(Date.now());
        toast.error(`Reveal-лимит: повторите через ${secs} сек`);
      } else if (e instanceof ApiError && e.status === 403) {
        setReason("Недостаточно прав: нужен view_password.");
      } else {
        toast.error(apiErrMsg(e, "Не удалось получить пароль"));
      }
    } finally {
      setRevealing(false);
    }
  }

  function hide() {
    setPlain(null);
    setPrevPlain(null);
  }

  async function copy(value: string) {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Пароль скопирован");
    } catch {
      toast.error("Буфер обмена недоступен");
    }
  }

  // Раскатка пароля на серверы. server === null → массово на все привязанные.
  async function applyTo(server: string | null) {
    if (applyingServer || !canOperate) return;
    if (server === null) {
      if (
        !(await confirm({
          title: "Применить пароль на серверы",
          message: `Раскатать текущий пароль ${account.login} на все ${account.server_ids.length} привязанных серверов (worker chpasswd)?`,
          confirmLabel: "Применить на все",
        }))
      )
        return;
    }
    setApplyingServer(server ?? "all");
    try {
      const dispatch = await accountsApi.rotateAccountWorker(
        account.id,
        server ? { server_id: server } : {},
      );
      setDispatchResult(dispatch);
      const queued = dispatch.dispatched.length || dispatch.tasks.length;
      const skipped = dispatch.failed.length || dispatch.skipped.length;
      const where = server ? serverName(server) : "все серверы";
      if (dispatch.partial_failure || skipped > 0) {
        toast.error(
          `Apply (${where}) частичный: задач — ${queued}, пропущено — ${skipped}.`,
        );
      } else {
        toast.success(`Apply поставлен в очередь (${where}): задач — ${queued}.`);
      }
      onChanged();
    } catch (e) {
      toast.error(handleDispatchError(e));
    } finally {
      setApplyingServer(null);
    }
  }

  return (
    <div className="card">
      <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
        <KeyRound className="w-3 h-3" /> Пароль
      </div>
      <div className="text-xs text-dim mb-3">
        Хранится зашифрованным (AES-256-GCM). Показ требует права view_password,
        пишет CRITICAL audit и режется reveal-rate-limit'ом. Смена пароля пишется
        только в БД — на серверы раскатывается кнопками ниже.
      </div>

      {pendingApply && (
        <div className="alert-warn text-xs flex items-start gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Новый пароль ещё не раскатан на все серверы. До apply на части
            боксов действует прежний пароль — ниже показаны оба.
          </span>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px] text-dim w-28 shrink-0">
            {pendingApply ? "новый (БД)" : "текущий"}
          </span>
          <div
            className={`mono text-sm flex-1 min-w-[200px] break-all ${shown ? "" : "text-dim"}`}
          >
            {shown ? plain : "••••••••••••"}
          </div>
          {shown ? (
            <>
              <button
                className="btn btn-sm flex items-center gap-1"
                onClick={() => plain && copy(plain)}
                type="button"
              >
                <Copy className="w-4 h-4" /> Копировать
              </button>
              <button
                className="btn btn-sm flex items-center gap-1"
                onClick={hide}
                type="button"
              >
                <EyeOff className="w-4 h-4" /> Скрыть
              </button>
            </>
          ) : (
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={handleReveal}
              disabled={!canReveal || revealing || throttleLeft > 0}
              title={
                !canReveal
                  ? "Нужна роль server.operator+ (и грант view_password)"
                  : "Раскрыть пароль (CRITICAL audit)"
              }
              type="button"
            >
              <Eye className="w-4 h-4" />
              {revealing
                ? "Запрашиваем…"
                : throttleLeft > 0
                  ? `Подождите ${throttleLeft}с`
                  : "Показать"}
            </button>
          )}
        </div>

        {shown && prevPlain !== null && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] text-dim w-28 shrink-0">
              прежний (ещё не обновлённые серверы)
            </span>
            <div className="mono text-sm flex-1 min-w-[200px] break-all">
              {prevPlain}
            </div>
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={() => copy(prevPlain)}
              type="button"
            >
              <Copy className="w-4 h-4" /> Копировать
            </button>
          </div>
        )}
      </div>

      {reason && <div className="text-xs text-dim mt-3">{reason}</div>}

      {/* Apply пароля на серверы: массово на все или точечно на конкретный. */}
      <div className="border-t border-token mt-3 pt-3 flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-dim flex-1 min-w-[140px]">
            Применить пароль на серверы (worker chpasswd):
          </span>
          <button
            type="button"
            className="btn btn-sm btn-primary flex items-center gap-1"
            disabled={
              !canOperate ||
              applyingServer !== null ||
              account.server_ids.length === 0
            }
            title={
              canOperate
                ? "Раскатать на все привязанные серверы"
                : "Нужна роль server.operator+"
            }
            onClick={() => applyTo(null)}
          >
            <RotateCw
              className={`w-3.5 h-3.5 ${applyingServer === "all" ? "animate-spin" : ""}`}
            />{" "}
            Применить на все серверы
          </button>
        </div>
        {account.server_ids.length > 0 && (
          <div className="flex flex-col gap-1">
            {account.server_ids.map((sid) => (
              <div
                key={sid}
                className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
              >
                <ServerIcon className="w-3.5 h-3.5 text-dim shrink-0" />
                <span className="mono flex-1 min-w-0 truncate">
                  {serverName(sid)}
                </span>
                <button
                  type="button"
                  className="btn btn-sm flex items-center gap-1"
                  disabled={!canOperate || applyingServer !== null}
                  title={
                    canOperate
                      ? "Раскатать пароль только на этот сервер"
                      : "Нужна роль server.operator+"
                  }
                  onClick={() => applyTo(sid)}
                >
                  <RotateCw
                    className={`w-3.5 h-3.5 ${applyingServer === sid ? "animate-spin" : ""}`}
                  />{" "}
                  Применить
                </button>
              </div>
            ))}
          </div>
        )}

        {dispatchResult && (
          <div className="border-t border-token mt-1 pt-3">
            <div className="text-[11px] uppercase text-dim mb-2">
              Результат последнего apply
            </div>
            <RotateDispatchResult
              result={dispatchResult}
              serverName={serverName}
              onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// SSH-ключ аккаунта — добавить/заменить + ротация (в правой рабочей зоне)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Управление SSH-ключом аккаунта. Добавить/заменить (`generate`|`supply`) и
 * ротация при компрометации (`rotate_ssh_key`). Раскатку на привязанные
 * серверы делает backend. Сам приватный ключ на экран не выводим: после
 * генерации сервис уже хранит его, и оператор скачивает PEM кнопкой «Скачать
 * приватный ключ» (reveal-эндпоинт) — сразу или позже.
 */
function SshKeySection({
  account,
  canOperate,
  canReveal,
  onChanged,
}: {
  account: ServerAccount;
  canOperate: boolean;
  /** view_password — держатель может скачать сохранённый приватный ключ. */
  canReveal: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  // null — форма закрыта; "add" — добавить/заменить; "rotate" — ротация.
  const [mode, setMode] = useState<"add" | "rotate" | null>(null);
  const [choice, setChoice] = useState<SshKeyMode>("generate");
  const [pubKey, setPubKey] = useState("");
  const [pending, setPending] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const hasKey = !!account.ssh_key_fingerprint || !!account.ssh_public_key;

  // Скачать сохранённый приватный ключ (для сгенерированных сервером пар).
  // Если ключа в хранилище нет (supply / нет ключа) — backend отдаёт 404,
  // показываем понятную причину.
  async function handleDownloadPrivate() {
    if (downloading || !canReveal) return;
    setDownloading(true);
    try {
      const res = await accountsApi.revealAccountSshPrivateKey(account.id);
      downloadText(`${account.login}_id_ed25519.pem`, res.ssh_private_key);
      toast.success("Приватный ключ скачан");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        toast.error(
          "Приватный ключ недоступен: ключ был передан как публичный (supply) или не выдавался.",
        );
      } else if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 60;
        toast.error(`Reveal-лимит: повторите через ${secs} сек`);
      } else if (e instanceof ApiError && e.status === 403) {
        toast.error("Недостаточно прав: нужен view_password.");
      } else {
        toast.error(apiErrMsg(e, "Не удалось скачать приватный ключ"));
      }
    } finally {
      setDownloading(false);
    }
  }

  function reset() {
    setMode(null);
    setChoice("generate");
    setPubKey("");
  }

  async function submit() {
    if (pending || !canOperate || !mode) return;
    const pub = pubKey.trim();
    if (choice === "supply" && !pub) {
      toast.error("Вставьте публичный SSH-ключ.");
      return;
    }
    if (mode === "rotate") {
      const okConfirm = await confirm({
        title: "Ротация SSH-ключа",
        message: `Сгенерировать новый SSH-ключ для ${account.login}? После раскатки на серверы старый ключ перестанет работать.`,
        confirmLabel: "Ротировать",
        danger: true,
      });
      if (!okConfirm) return;
    }
    setPending(true);
    try {
      // Ротация тела не принимает (всегда новая пара); add/replace шлёт режим
      // и, при supply, публичный ключ.
      const res =
        mode === "rotate"
          ? await accountsApi.rotateAccountSshKey(account.id)
          : await accountsApi.setAccountSshKey(account.id, {
              ssh_mode: choice,
              ssh_public_key: choice === "supply" ? pub : null,
            });
      reset();
      // При generate (add) и при ротации сервер возвращает приватный ключ, но
      // на экран его не выводим. Ключ уже сохранён в хранилище — скачать его
      // можно сейчас или позже кнопкой «Скачать приватный ключ» (reveal).
      if (res.ssh_private_key) {
        toast.success(
          "SSH-ключ сохранён. Скачать приватный ключ можно сейчас или позже кнопкой «Скачать приватный ключ».",
        );
      } else {
        toast.success("SSH-ключ сохранён, раскатка на серверы запущена.");
      }
      onChanged();
    } catch (e) {
      toast.error(handleActionError(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
        <KeySquare className="w-3 h-3" /> SSH-ключ
      </div>
      <div className="text-xs text-dim mb-3">
        Раскатка ключа на привязанные серверы — автоматически на стороне сервиса.
        Сам приватный ключ на экран не выводится: после генерации его можно
        скачать сейчас или позже кнопкой «Скачать приватный ключ».
      </div>

      <div className="flex items-center gap-2 flex-wrap mb-3">
        <span className="text-sm flex-1 min-w-[160px] break-all">
          {account.ssh_key_fingerprint ? (
            <span className="mono text-xs">{account.ssh_key_fingerprint}</span>
          ) : (
            <span className="text-dim italic">ключ не выдан</span>
          )}
        </span>
        {mode === null && (
          <>
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              disabled={!canOperate}
              title={canOperate ? undefined : "Нет прав"}
              onClick={() => setMode("add")}
            >
              <KeySquare className="w-3.5 h-3.5" />{" "}
              {hasKey ? "Заменить ключ" : "Добавить ключ"}
            </button>
            {hasKey && canReveal && (
              <button
                type="button"
                className="btn btn-sm flex items-center gap-1"
                disabled={downloading}
                title="Скачать сохранённый приватный ключ (.pem). Требует view_password."
                onClick={handleDownloadPrivate}
              >
                <Download className="w-3.5 h-3.5" />{" "}
                {downloading ? "Скачиваем…" : "Скачать приватный ключ"}
              </button>
            )}
            {hasKey && (
              <button
                type="button"
                className="btn btn-sm btn-danger flex items-center gap-1"
                disabled={!canOperate}
                title={canOperate ? "Кейс компрометации" : "Нет прав"}
                onClick={() => {
                  setChoice("generate");
                  setMode("rotate");
                }}
              >
                <RotateCw className="w-3.5 h-3.5" /> Ротировать
              </button>
            )}
          </>
        )}
      </div>

      {mode !== null && (
        <div className="border-t border-token pt-3 flex flex-col gap-2">
          <div className="flex flex-col gap-0.5">
            <label className="inline-flex items-center gap-2 text-sm py-0.5">
              <input
                type="radio"
                name="ssh-section-mode"
                checked={choice === "generate"}
                onChange={() => setChoice("generate")}
              />
              <span>сгенерировать (приватный ключ — кнопкой «Скачать»)</span>
            </label>
            <label className="inline-flex items-center gap-2 text-sm py-0.5">
              <input
                type="radio"
                name="ssh-section-mode"
                checked={choice === "supply"}
                onChange={() => setChoice("supply")}
              />
              <span>вставить существующий публичный ключ</span>
            </label>
          </div>
          {choice === "supply" && (
            <textarea
              className="field-input mono text-xs h-20 resize-none"
              value={pubKey}
              onChange={(e) => setPubKey(e.target.value)}
              placeholder="ssh-ed25519 AAAA… comment"
            />
          )}
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              className="btn btn-sm"
              onClick={reset}
              disabled={pending}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={submit}
              disabled={pending}
            >
              {pending
                ? "Применяем…"
                : mode === "rotate"
                  ? "Ротировать"
                  : "Сохранить"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function EmptyPane({ hasAny }: { hasAny: boolean }) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <Users className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim">
          {hasAny
            ? "Выберите аккаунт слева для просмотра и управления."
            : "В отделе нет server_account'ов. Аккаунты заводятся на вкладке «Аккаунты» карточки сервера."}
        </div>
      </div>
    </section>
  );
}

function BlockedPane() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
        <div className="text-sm font-medium mb-2">
          Раздел недоступен для платформенного администратора
        </div>
        <div className="text-xs text-dim">
          server_service отделяет управление платформой от бизнес-данных
          серверов. Учётка <b>account_admin</b> / <b>logging_admin</b> не имеет
          доступа к аккаунтам серверов — работайте под департаментной ролью
          (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}
