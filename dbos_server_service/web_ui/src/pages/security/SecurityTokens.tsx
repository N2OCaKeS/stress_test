import { useCallback, useEffect, useState } from "react";
import { KeyRound, Plus, Trash2, Copy } from "lucide-react";
import { apiErrMsg } from "@/api/client";
import {
  createToken,
  listMyTokens,
  revokeToken,
} from "@/api/auth/tokens";
import type {
  PATCreateResponse,
  PersonalAccessToken,
  ServiceName,
} from "@/api/auth/types";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/contexts/ToastContext";
import { formatMskDate, mskDateOffset } from "@/lib/datetime";

const KNOWN_SERVICES: ServiceName[] = [
  "auth_service",
  "secret_service",
  "server_service",
  "worker_service",
  "loging_service",
  "config_service",
  "docker_registry",
];

/**
 * Returns the list of services the user can scope a PAT to. A PAT must name at
 * least one service (backend min_length=1); `account_admin` with an empty
 * `allowed_services` is expanded into the full `KNOWN_SERVICES` list so chips
 * render and the operator picks a concrete scope.
 */
function resolveAvailableServices(
  user: { allowed_services?: ServiceName[]; platform_role?: string | null } | null,
): string[] {
  if (!user) return [];
  const list = user.allowed_services ?? [];
  if (list.length > 0) return list;
  if (user.platform_role === "account_admin") return [...KNOWN_SERVICES];
  return [];
}

/**
 * Bounds для UI-поля «Срок действия». Backend режет expires_at < now или
 * > now + 180 дней одним INVALID_EXPIRATION; мы синхронно ограничиваем
 * `<input type="date">` тем же диапазоном. Default = +90 дней.
 */
function tokenExpiresBounds(): { min: string; max: string; default: string } {
  return {
    min: mskDateOffset(1),
    max: mskDateOffset(180),
    default: mskDateOffset(90),
  };
}

export function SecurityTokens() {
  const [items, setItems] = useState<PersonalAccessToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [oneShot, setOneShot] = useState<PATCreateResponse | null>(null);
  const toast = useToast();

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listMyTokens();
      const now = Date.now();
      const active = list.filter(
        (t) =>
          !t.revoked_at &&
          (!t.expires_at || new Date(t.expires_at).getTime() > now),
      );
      setItems(active);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось загрузить токены"));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-dim">
          PAT привязаны к вашему пользователю (`actor_id`). Токен показывается
          один раз при создании.
        </div>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={() => setCreating(true)}
        >
          <Plus className="w-4 h-4" /> Создать PAT
        </button>
      </div>

      {oneShot && <OneShotPanel resp={oneShot} onClose={() => setOneShot(null)} />}
      {creating && (
        <CreateForm
          onCancel={() => setCreating(false)}
          onCreated={(resp) => {
            setCreating(false);
            setOneShot(resp);
            void reload();
          }}
        />
      )}

      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <KeyRound className="w-4 h-4 text-accent" /> Активные PAT
        </h3>
        {loading ? (
          <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
        ) : items.length === 0 ? (
          <div className="text-xs text-dim py-4 text-center">Токенов нет.</div>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((t) => (
              <TokenRow key={t.token_id} t={t} onRevoke={reload} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TokenRow({
  t,
  onRevoke,
}: {
  t: PersonalAccessToken;
  onRevoke: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const toast = useToast();
  const onClick = async () => {
    if (!window.confirm(`Отозвать PAT «${t.name}»?`)) return;
    setPending(true);
    try {
      await revokeToken(t.token_id);
      toast.success("Токен отозван");
      await onRevoke();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось отозвать токен"));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="cred-row flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{t.name}</div>
        <div className="text-[11px] text-dim mono truncate">{t.token_id}</div>
        <div className="text-[11px] text-dim">
          scope: {t.allowed_services.join(", ")}
          {t.expires_at && ` · истекает ${formatMskDate(t.expires_at)}`}
        </div>
      </div>
      <button
        className="btn btn-danger flex items-center gap-1"
        onClick={onClick}
        disabled={pending}
      >
        <Trash2 className="w-4 h-4" /> Revoke
      </button>
    </div>
  );
}

function CreateForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (r: PATCreateResponse) => void;
}) {
  const { user } = useAuth();
  const availableServices = resolveAvailableServices(user);
  const expBounds = tokenExpiresBounds();
  const [name, setName] = useState("");
  const [expires, setExpires] = useState(expBounds.default);
  const [scopes, setScopes] = useState<string[]>(availableServices);
  const [pending, setPending] = useState(false);
  const toast = useToast();

  const toggle = (svc: string) =>
    setScopes((prev) =>
      prev.includes(svc) ? prev.filter((s) => s !== svc) : [...prev, svc],
    );

  const noScopes = scopes.length === 0;
  const noServicesAvailable = availableServices.length === 0;
  const expiresInvalid =
    !expires || expires < expBounds.min || expires > expBounds.max;

  const submit = async () => {
    if (!name.trim()) {
      toast.warn("Укажите имя токена");
      return;
    }
    if (noScopes) {
      toast.warn("Выберите минимум 1 сервис");
      return;
    }
    if (expiresInvalid) {
      toast.warn("Срок действия обязателен и не должен превышать 6 месяцев");
      return;
    }
    setPending(true);
    try {
      const resp = await createToken({
        name: name.trim(),
        expires_at: new Date(`${expires}T23:59:59Z`).toISOString(),
        allowed_services: scopes as ServiceName[],
      });
      onCreated(resp);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось создать токен"));
    } finally {
      setPending(false);
    }
  };

  const submitDisabled =
    pending || noScopes || noServicesAvailable || expiresInvalid;
  const submitTooltip = noServicesAvailable
    ? "У вас нет доступных сервисов для PAT"
    : noScopes
      ? "Минимум 1 сервис"
      : expiresInvalid
        ? "Срок действия обязателен и не должен превышать 6 месяцев"
        : undefined;

  return (
    <div className="card">
      <h3 className="font-semibold mb-3">Новый PAT</h3>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">name</span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="cli-token"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            Срок действия (до) — max 6 месяцев
          </span>
          <input
            className="input"
            type="date"
            value={expires}
            min={expBounds.min}
            max={expBounds.max}
            onChange={(e) => setExpires(e.target.value)}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            allowed_services — отметьте сервисы, к которым токен будет иметь доступ (минимум 1)
          </span>
          {noServicesAvailable ? (
            <div className="text-xs text-warn italic">
              У вас нет доступных сервисов — PAT создать нельзя.
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {availableServices.map((svc) => (
                <button
                  key={svc}
                  type="button"
                  onClick={() => toggle(svc)}
                  className={`badge ${scopes.includes(svc) ? "active" : ""}`}
                >
                  {svc}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={submitDisabled}
          title={submitTooltip}
        >
          Создать
        </button>
      </div>
    </div>
  );
}

function OneShotPanel({
  resp,
  onClose,
}: {
  resp: PATCreateResponse;
  onClose: () => void;
}) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(resp.token);
      toast.success("Токен скопирован");
    } catch {
      toast.warn("Не удалось скопировать — выделите вручную");
    }
  };
  return (
    <div className="card border-warn">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-warn">
          Токен показан один раз — сохраните его сейчас
        </h3>
        <button className="btn btn-ghost" onClick={onClose}>
          Скрыть
        </button>
      </div>
      <div className="mono text-xs break-all border border-token p-2 rounded">
        {resp.token}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button className="btn btn-primary flex items-center gap-1" onClick={copy}>
          <Copy className="w-4 h-4" /> Скопировать
        </button>
        <div className="text-[11px] text-dim">
          {resp.name} · {resp.token_id}
        </div>
      </div>
    </div>
  );
}
