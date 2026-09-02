import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
  createElement,
} from "react";
import type { ApiExample, CodeLang } from "./examples/types";

// ---------------------------------------------------------------------------
// Legacy-контекст: оставлен для обратной совместимости с CodeExample.tsx,
// который пока тянет {{BASE_URL}}/{{TOKEN}} через applySnippet. Фаза 2
// мигрирует CodeExample на composeSnippet + useWikiSettings.
// ---------------------------------------------------------------------------

export interface SnippetContextValue {
  baseUrl: string;
  token: string;
}

export const SnippetContext = createContext<SnippetContextValue>({
  baseUrl: "",
  token: "",
});

export function useSnippet(): SnippetContextValue {
  return useContext(SnippetContext);
}

// Подставляет {{BASE_URL}} и {{TOKEN}} в сниппет. Пустой Base URL остаётся как
// есть в плейсхолдере, пустой токен превращается в shell-переменную $TOKEN,
// чтобы скопированный curl работал без правок.
export function applySnippet(code: string, ctx: SnippetContextValue): string {
  const base = ctx.baseUrl.trim().replace(/\/+$/, "");
  const token = ctx.token.trim() || "$TOKEN";
  return code
    .replaceAll("{{BASE_URL}}", base || "{{BASE_URL}}")
    .replaceAll("{{TOKEN}}", token);
}

// ---------------------------------------------------------------------------
// WikiSettings — состояние панели примеров (только in-memory, без localStorage:
// пароль / PAT — секреты, не персистим).
// ---------------------------------------------------------------------------

export type AuthMode = "login" | "pat";

export interface WikiSettings {
  // transport / auth
  baseUrl: string;
  authMode: AuthMode;
  username: string;
  password: string;
  pat: string;
  // если задан напрямую — используется как готовый access-токен (без login-шага)
  token: string;
  // глобальный язык сниппетов
  lang: CodeLang;
  // entity-id для подстановки в пути / тела
  departmentId: string;
  serverId: string;
  accountId: string;
  credentialId: string;
  groupId: string;
}

export interface WikiSettingsValue extends WikiSettings {
  setBaseUrl: (v: string) => void;
  setAuthMode: (v: AuthMode) => void;
  setUsername: (v: string) => void;
  setPassword: (v: string) => void;
  setPat: (v: string) => void;
  setToken: (v: string) => void;
  setLang: (v: CodeLang) => void;
  setDepartmentId: (v: string) => void;
  setServerId: (v: string) => void;
  setAccountId: (v: string) => void;
  setCredentialId: (v: string) => void;
  setGroupId: (v: string) => void;
}

function defaultBaseUrl(): string {
  return typeof window !== "undefined" ? window.location.origin : "";
}

export const DEFAULT_WIKI_SETTINGS: WikiSettings = {
  baseUrl: defaultBaseUrl(),
  authMode: "login",
  username: "",
  password: "",
  pat: "",
  token: "",
  lang: "python",
  departmentId: "",
  serverId: "",
  accountId: "",
  credentialId: "",
  groupId: "",
};

const WikiSettingsContext = createContext<WikiSettingsValue | undefined>(
  undefined,
);

export function WikiSettingsProvider({ children }: { children: ReactNode }) {
  const [baseUrl, setBaseUrl] = useState(DEFAULT_WIKI_SETTINGS.baseUrl);
  const [authMode, setAuthMode] = useState<AuthMode>(
    DEFAULT_WIKI_SETTINGS.authMode,
  );
  const [username, setUsername] = useState(DEFAULT_WIKI_SETTINGS.username);
  const [password, setPassword] = useState(DEFAULT_WIKI_SETTINGS.password);
  const [pat, setPat] = useState(DEFAULT_WIKI_SETTINGS.pat);
  const [token, setToken] = useState(DEFAULT_WIKI_SETTINGS.token);
  const [lang, setLang] = useState<CodeLang>(DEFAULT_WIKI_SETTINGS.lang);
  const [departmentId, setDepartmentId] = useState(
    DEFAULT_WIKI_SETTINGS.departmentId,
  );
  const [serverId, setServerId] = useState(DEFAULT_WIKI_SETTINGS.serverId);
  const [accountId, setAccountId] = useState(DEFAULT_WIKI_SETTINGS.accountId);
  const [credentialId, setCredentialId] = useState(
    DEFAULT_WIKI_SETTINGS.credentialId,
  );
  const [groupId, setGroupId] = useState(DEFAULT_WIKI_SETTINGS.groupId);

  const value = useMemo<WikiSettingsValue>(
    () => ({
      baseUrl,
      authMode,
      username,
      password,
      pat,
      token,
      lang,
      departmentId,
      serverId,
      accountId,
      credentialId,
      groupId,
      setBaseUrl,
      setAuthMode,
      setUsername,
      setPassword,
      setPat,
      setToken,
      setLang,
      setDepartmentId,
      setServerId,
      setAccountId,
      setCredentialId,
      setGroupId,
    }),
    [
      baseUrl,
      authMode,
      username,
      password,
      pat,
      token,
      lang,
      departmentId,
      serverId,
      accountId,
      credentialId,
      groupId,
    ],
  );

  return createElement(WikiSettingsContext.Provider, { value }, children);
}

export function useWikiSettings(): WikiSettingsValue {
  const ctx = useContext(WikiSettingsContext);
  if (!ctx) {
    throw new Error(
      "useWikiSettings must be used inside <WikiSettingsProvider>",
    );
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// composeSnippet — собирает полный самодостаточный сниппет:
//   [A] auth-преамбула (login → token + department_id, либо PAT)
//   [B] основной вызов с подставленными плейсхолдерами
//   [C] poll-цикл для async-задач (если example.asyncTask)
// ---------------------------------------------------------------------------

function trimBase(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, "");
}

// Имя переменной с токеном внутри сниппета — на неё ссылается основной вызов,
// чтобы не светить литерал и переиспользовать результат login-преамбулы.
const TOKEN_VAR = "token";

function pythonAuthPreamble(s: WikiSettings, base: string): string[] {
  const lines: string[] = ["import requests", "import time", ""];
  lines.push(`base_url = ${JSON.stringify(base || "{{BASE_URL}}")}`);
  if (s.authMode === "pat") {
    const pat = s.pat.trim() || s.token.trim();
    lines.push(`${TOKEN_VAR} = ${JSON.stringify(pat || "<PAT>")}`);
    const dep = s.departmentId.trim();
    if (dep) {
      lines.push(`department_id = ${JSON.stringify(dep)}`);
    } else {
      lines.push(
        "# department_id узнать через GET /api/auth/v1/authorization/introspect или /me",
        'department_id = ""',
      );
    }
    lines.push("");
    return lines;
  }
  // login
  if (s.token.trim()) {
    // готовый токен задан напрямую — login-шаг не нужен
    lines.push(`${TOKEN_VAR} = ${JSON.stringify(s.token.trim())}`);
  } else {
    lines.push(
      `username = ${JSON.stringify(s.username.trim() || "admin")}`,
      `password = ${JSON.stringify(s.password || "1234")}`,
      "",
      "login = requests.post(",
      '    f"{base_url}/api/auth/v1/login",',
      '    json={"username": username, "password": password},',
      ").json()",
      `${TOKEN_VAR} = login["access_token"]`,
      "# refresh при необходимости:",
      '#   requests.post(f"{base_url}/api/auth/v1/refresh",',
      '#                 json={"refresh_token": login["refresh_token"]})',
    );
  }
  const dep = s.departmentId.trim();
  if (dep) {
    lines.push(`department_id = ${JSON.stringify(dep)}`);
  } else if (s.token.trim()) {
    lines.push(
      "# department_id из настроек пуст и нет login-ответа — задайте явно",
      'department_id = ""',
    );
  } else {
    // RBAC: юзер работает в своём отделе — берём его из identity логина
    lines.push('department_id = login["identity"]["department_id"]');
  }
  lines.push("");
  return lines;
}

function curlAuthPreamble(s: WikiSettings, base: string): string[] {
  const lines: string[] = [];
  lines.push(`base_url="${base || "{{BASE_URL}}"}"`);
  if (s.authMode === "pat") {
    const pat = s.pat.trim() || s.token.trim();
    lines.push(`token="${pat || "<PAT>"}"`);
    const dep = s.departmentId.trim();
    if (dep) {
      lines.push(`department_id="${dep}"`);
    } else {
      lines.push(
        "# department_id узнать через GET /api/auth/v1/authorization/introspect или /me",
        'department_id=""',
      );
    }
    lines.push("");
    return lines;
  }
  if (s.token.trim()) {
    lines.push(`token="${s.token.trim()}"`);
    const dep = s.departmentId.trim();
    lines.push(dep ? `department_id="${dep}"` : 'department_id=""');
    lines.push("");
    return lines;
  }
  lines.push(
    `username="${s.username.trim() || "admin"}"`,
    `password="${s.password || "1234"}"`,
    "",
    'login=$(curl -s -X POST "$base_url/api/auth/v1/login" \\',
    '  -H "Content-Type: application/json" \\',
    '  -d "{\\"username\\":\\"$username\\",\\"password\\":\\"$password\\"}")',
    'token=$(echo "$login" | jq -r .access_token)',
    "# refresh при необходимости:",
    '#   curl -s -X POST "$base_url/api/auth/v1/refresh" \\',
    '#     -H "Content-Type: application/json" \\',
    '#     -d "{\\"refresh_token\\":\\"$(echo "$login" | jq -r .refresh_token)\\"}"',
  );
  const dep = s.departmentId.trim();
  if (dep) {
    lines.push(`department_id="${dep}"`);
  } else {
    lines.push('department_id=$(echo "$login" | jq -r .identity.department_id)');
  }
  lines.push("");
  return lines;
}

// Подстановка плейсхолдеров в тело основного вызова. token и base_url ссылаются
// на переменные из преамбулы, поэтому BASE_URL/TOKEN → имена переменных.
function substitutePlaceholders(
  code: string,
  s: WikiSettings,
  lang: CodeLang,
): string {
  const baseRef = lang === "python" ? '{base_url}' : "$base_url";
  const tokenRef = lang === "python" ? `{${TOKEN_VAR}}` : `$${TOKEN_VAR}`;
  // entity-id: непустая настройка → литерал; пустая → ссылка на переменную
  // (department_id — из identity логина, прочие должны быть заданы в панели).
  // В python f-string переменная — {var}, в curl-строке — $var.
  const ent = (setting: string, name: string): string => {
    const v = setting.trim();
    if (v) return v;
    return lang === "python" ? `{${name}}` : `$${name}`;
  };
  return code
    .replaceAll("{{BASE_URL}}", baseRef)
    .replaceAll("{{TOKEN}}", tokenRef)
    .replaceAll("{{USERNAME}}", s.username.trim() || "admin")
    .replaceAll("{{PASSWORD}}", s.password || "1234")
    .replaceAll("{{PAT}}", s.pat.trim() || "<PAT>")
    .replaceAll("{{DEPARTMENT_ID}}", ent(s.departmentId, "department_id"))
    .replaceAll("{{SERVER_ID}}", ent(s.serverId, "server_id"))
    .replaceAll("{{ACCOUNT_ID}}", ent(s.accountId, "account_id"))
    .replaceAll("{{CREDENTIAL_ID}}", ent(s.credentialId, "credential_id"))
    .replaceAll("{{GROUP_ID}}", ent(s.groupId, "group_id"));
}

function pythonPollBlock(): string[] {
  return [
    "",
    "# async: ответ 202 несёт task_id — опрашиваем задачу до терминала",
    'task_id = r.json()["task_id"]',
    "while True:",
    "    t = requests.get(",
    '        f"{base_url}/api/server/v1/tasks/{task_id}",',
    `        headers={"Authorization": f"Bearer {${TOKEN_VAR}}"},`,
    "    ).json()",
    '    if t["status"] in ("succeeded", "failed", "cancelled"):',
    "        break",
    "    time.sleep(2)",
    'print(t["status"], t.get("result"), t.get("last_error"))',
  ];
}

function curlPollBlock(): string[] {
  return [
    "",
    "# async: ответ 202 несёт task_id — опрашиваем задачу до терминала",
    'task_id=$(echo "$r" | jq -r .task_id)',
    "while true; do",
    '  t=$(curl -s "$base_url/api/server/v1/tasks/$task_id" \\',
    '    -H "Authorization: Bearer $token")',
    '  status=$(echo "$t" | jq -r .status)',
    '  case "$status" in succeeded|failed|cancelled) break;; esac',
    "  sleep 2",
    "done",
    'echo "$t" | jq \'{status, result, last_error}\'',
  ];
}

// Для async-curl основной вызов нужно завернуть в r=$(...), чтобы из ответа
// достать task_id. Оборачиваем последнюю curl-команду в подстановку.
function wrapCurlForPolling(call: string): string {
  // call — текст example.curl после подстановки. Превращаем ведущий `curl ...`
  // в `r=$(curl ... )`, гасим вывод -s. Простой случай: один curl-блок.
  const trimmed = call.replace(/\s+$/, "");
  return `r=$(${trimmed})`;
}

export function composeSnippet(
  example: ApiExample,
  lang: CodeLang,
  s: WikiSettings,
): string {
  const base = trimBase(s.baseUrl);
  const raw = lang === "python" ? example.python : example.curl;
  const body = substitutePlaceholders(raw, s, lang);

  if (lang === "python") {
    const parts = [...pythonAuthPreamble(s, base), body];
    if (example.asyncTask) {
      parts.push(...pythonPollBlock());
    }
    return parts.join("\n");
  }

  // curl / bash
  const callBlock = example.asyncTask ? wrapCurlForPolling(body) : body;
  const parts = [...curlAuthPreamble(s, base), callBlock];
  if (example.asyncTask) {
    parts.push(...curlPollBlock());
  }
  return parts.join("\n");
}
