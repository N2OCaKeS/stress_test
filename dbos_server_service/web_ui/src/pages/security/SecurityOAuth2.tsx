import { useCallback, useEffect, useState } from "react";
import { Copy, Plus, Trash2, Unplug } from "lucide-react";
import { ApiError } from "@/api/client";
import {
  buildAuthorizeUrl,
  createClient,
  deleteClient,
  exchangeToken,
  listClients,
  type OAuthTokenResponse,
} from "@/api/auth/oauth2";
import type {
  OAuth2Client,
  OAuth2ClientCreatedResponse,
} from "@/api/auth/types";
import { useToast } from "@/contexts/ToastContext";
import { useDeptLabel } from "@/lib/labels";

const KNOWN_GRANTS = ["authorization_code", "client_credentials"];

export function SecurityOAuth2() {
  const [items, setItems] = useState<OAuth2Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<OAuth2ClientCreatedResponse | null>(null);
  const [tab, setTab] = useState<"clients" | "authorize" | "token">("clients");
  const toast = useToast();

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listClients());
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Не удалось загрузить клиентов");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-1 border-b border-token">
        {(["clients", "authorize", "token"] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
              tab === t
                ? "border-accent text-accent"
                : "border-transparent text-dim"
            }`}
            onClick={() => setTab(t)}
          >
            {t === "clients"
              ? "Клиенты"
              : t === "authorize"
                ? "Authorize-flow"
                : "Token exchange"}
          </button>
        ))}
      </div>

      {tab === "clients" && (
        <>
          <div className="flex items-center justify-between">
            <div className="text-sm text-dim">
              `POST /oauth2/clients` создаёт клиента, секрет показывается один раз.
              `DELETE` — soft-delete (`is_active=false`).
            </div>
            <button
              className="btn btn-primary flex items-center gap-1"
              onClick={() => setCreating(true)}
            >
              <Plus className="w-4 h-4" /> Создать клиента
            </button>
          </div>

          {secret && (
            <SecretPanel resp={secret} onClose={() => setSecret(null)} />
          )}
          {creating && (
            <ClientForm
              onCancel={() => setCreating(false)}
              onCreated={(r) => {
                setCreating(false);
                setSecret(r);
                void reload();
              }}
            />
          )}

          <div className="card">
            <h3 className="font-semibold flex items-center gap-2 mb-3">
              <Unplug className="w-4 h-4 text-accent" /> OAuth2-клиенты
            </h3>
            {loading ? (
              <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
            ) : items.length === 0 ? (
              <div className="text-xs text-dim py-4 text-center">
                Клиентов нет.
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {items.map((c) => (
                  <ClientRow key={c.id} c={c} onChange={reload} />
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {tab === "authorize" && <AuthorizeTester />}
      {tab === "token" && <TokenTester />}
    </div>
  );
}

function ClientRow({
  c,
  onChange,
}: {
  c: OAuth2Client;
  onChange: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const toast = useToast();
  const onDelete = async () => {
    if (!window.confirm(`Деактивировать клиента «${c.name}»?`)) return;
    setPending(true);
    try {
      // backend ждёт внутренний `id` (`cli_<32hex>`), не публичный `client_id`
      await deleteClient(c.id);
      toast.success("Клиент деактивирован");
      await onChange();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Не удалось удалить клиента");
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="cred-row flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">
          {c.name}{" "}
          {!c.is_active && <span className="badge">inactive</span>}
        </div>
        <div className="text-[11px] text-dim mono truncate">{c.client_id}</div>
        <div className="text-[11px] text-dim truncate">
          dept: <OAuth2DeptLabel deptId={c.department_id} /> · grants:{" "}
          {c.grant_types.join(", ")} · uris: {c.redirect_uris.length}
        </div>
      </div>
      <button
        className="btn btn-danger flex items-center gap-1"
        onClick={onDelete}
        disabled={pending}
      >
        <Trash2 className="w-4 h-4" /> Delete
      </button>
    </div>
  );
}

function ClientForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (r: OAuth2ClientCreatedResponse) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [redirects, setRedirects] = useState("https://app.example.com/callback");
  const [scopes, setScopes] = useState("openid profile");
  const [grants, setGrants] = useState<string[]>(["authorization_code"]);
  const [pending, setPending] = useState(false);
  const toast = useToast();

  const toggleGrant = (g: string) =>
    setGrants((prev) =>
      prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g],
    );

  const submit = async () => {
    if (!name.trim() || !departmentId.trim()) {
      toast.warn("Имя и department_id обязательны");
      return;
    }
    setPending(true);
    try {
      const r = await createClient({
        name: name.trim(),
        description: description.trim() || undefined,
        department_id: departmentId.trim(),
        redirect_uris: redirects
          .split(/[\n,]/)
          .map((s) => s.trim())
          .filter(Boolean),
        allowed_scopes: scopes.split(/\s+/).filter(Boolean),
        grant_types: grants,
      });
      onCreated(r);
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Не удалось создать клиента");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold mb-3">Новый OAuth2 клиент</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">name</span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">department_id</span>
          <input
            className="input mono"
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm md:col-span-2">
          <span className="text-dim text-xs">description</span>
          <input
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm md:col-span-2">
          <span className="text-dim text-xs">
            redirect_uris (по одному на строку или через запятую; только https
            или http://localhost)
          </span>
          <textarea
            className="input mono"
            rows={3}
            value={redirects}
            onChange={(e) => setRedirects(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">allowed_scopes</span>
          <input
            className="input mono"
            value={scopes}
            onChange={(e) => setScopes(e.target.value)}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">grant_types</span>
          <div className="flex gap-1.5 flex-wrap">
            {KNOWN_GRANTS.map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => toggleGrant(g)}
                className={`badge ${grants.includes(g) ? "active" : ""}`}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={pending}>
          Создать
        </button>
      </div>
    </div>
  );
}

function SecretPanel({
  resp,
  onClose,
}: {
  resp: OAuth2ClientCreatedResponse;
  onClose: () => void;
}) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(resp.client_secret);
      toast.success("Секрет скопирован");
    } catch {
      toast.warn("Не удалось скопировать");
    }
  };
  return (
    <div className="card border-warn">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-warn">
          client_secret показывается один раз
        </h3>
        <button className="btn btn-ghost" onClick={onClose}>
          Скрыть
        </button>
      </div>
      <div className="text-xs text-dim mb-2">
        client_id: <span className="mono">{resp.client_id}</span>
      </div>
      <div className="mono text-xs break-all border border-token p-2 rounded">
        {resp.client_secret}
      </div>
      <div className="mt-2">
        <button className="btn btn-primary flex items-center gap-1" onClick={copy}>
          <Copy className="w-4 h-4" /> Скопировать
        </button>
      </div>
    </div>
  );
}

function AuthorizeTester() {
  const [clientId, setClientId] = useState("");
  const [redirectUri, setRedirectUri] = useState("");
  const [scope, setScope] = useState("openid");
  const [state, setState] = useState("");
  const [challenge, setChallenge] = useState("");
  const [method, setMethod] = useState<"S256" | "plain">("S256");

  const url = buildAuthorizeUrl({
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: scope || undefined,
    state: state || undefined,
    code_challenge: challenge || undefined,
    code_challenge_method: challenge ? method : undefined,
  });

  return (
    <div className="card">
      <h3 className="font-semibold mb-3">OAuth2 authorize URL builder</h3>
      <div className="text-xs text-dim mb-3">
        Endpoint требует пользовательский JWT в заголовке `Authorization`, поэтому
        браузерный редирект здесь только для построения URL — реальный вызов
        делает интегрирующее приложение через свой backend / SDK.
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">client_id</span>
          <input
            className="input mono"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">redirect_uri</span>
          <input
            className="input mono"
            value={redirectUri}
            onChange={(e) => setRedirectUri(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">scope</span>
          <input
            className="input mono"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">state (CSRF, max 2048)</span>
          <input
            className="input mono"
            value={state}
            onChange={(e) => setState(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">code_challenge (PKCE)</span>
          <input
            className="input mono"
            value={challenge}
            onChange={(e) => setChallenge(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">code_challenge_method</span>
          <select
            className="input"
            value={method}
            onChange={(e) => setMethod(e.target.value as "S256" | "plain")}
          >
            <option value="S256">S256</option>
            <option value="plain">plain</option>
          </select>
        </label>
      </div>
      <div className="mt-3">
        <div className="text-xs text-dim mb-1">Полный URL:</div>
        <div className="mono text-xs break-all border border-token p-2 rounded">
          {clientId && redirectUri ? url : "— заполните client_id и redirect_uri"}
        </div>
      </div>
    </div>
  );
}

function TokenTester() {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [grant, setGrant] = useState<"client_credentials" | "authorization_code">(
    "client_credentials",
  );
  const [code, setCode] = useState("");
  const [redirectUri, setRedirectUri] = useState("");
  const [verifier, setVerifier] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<OAuthTokenResponse | null>(null);
  const toast = useToast();

  const submit = async () => {
    setPending(true);
    setResult(null);
    try {
      const r = await exchangeToken({
        grant_type: grant,
        client_id: clientId,
        client_secret: clientSecret,
        code: grant === "authorization_code" ? code : undefined,
        redirect_uri: grant === "authorization_code" ? redirectUri : undefined,
        code_verifier: grant === "authorization_code" ? verifier : undefined,
      });
      setResult(r);
      toast.success("Токен получен");
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Ошибка получения токена");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold mb-3">POST /oauth2/token</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">grant_type</span>
          <select
            className="input"
            value={grant}
            onChange={(e) =>
              setGrant(
                e.target.value as "client_credentials" | "authorization_code",
              )
            }
          >
            <option value="client_credentials">client_credentials</option>
            <option value="authorization_code">authorization_code</option>
          </select>
        </label>
        <div />
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">client_id</span>
          <input
            className="input mono"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">client_secret</span>
          <input
            className="input mono"
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </label>
        {grant === "authorization_code" && (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">code</span>
              <input
                className="input mono"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">redirect_uri</span>
              <input
                className="input mono"
                value={redirectUri}
                onChange={(e) => setRedirectUri(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm md:col-span-2">
              <span className="text-dim text-xs">code_verifier</span>
              <input
                className="input mono"
                value={verifier}
                onChange={(e) => setVerifier(e.target.value)}
              />
            </label>
          </>
        )}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={pending || !clientId}
        >
          Запросить токен
        </button>
      </div>
      {result && (
        <div className="mt-3">
          <div className="text-xs text-dim mb-1">Response:</div>
          <pre className="mono text-xs whitespace-pre-wrap break-all border border-token p-2 rounded">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function OAuth2DeptLabel({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}
