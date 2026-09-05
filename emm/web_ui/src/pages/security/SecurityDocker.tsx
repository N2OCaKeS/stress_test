import { useCallback, useEffect, useRef, useState } from "react";
import { Container, FileText, KeyRound, Loader2, RefreshCcw } from "lucide-react";
import { ApiError, apiErrMsg } from "@/api/client";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  deleteRegistry,
  getDockerCerts,
  getDockerJwks,
  getDockerToken,
  getRegistry,
  patchRegistry,
  putRegistry,
} from "@/api/auth/docker";
import type {
  DockerRegistryConfig,
  DockerRegistryConfigPatchRequest,
  DockerTokenResponse,
} from "@/api/auth/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

function errToMessage(e: unknown): string {
  return apiErrMsg(e, String(e));
}

function parseIdList(input: string): string[] {
  return input
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function SecurityDocker() {
  // services-блок отдаёт workzone компоненту целиком (main — overflow-hidden),
  // поэтому страница сама держит скролл и высоту, иначе длинный контент
  // (registry + token + PEM/JWKS) уезжает за нижнюю кромку без прокрутки.
  return (
    <div className="flex-1 min-h-0 overflow-auto p-6 flex flex-col gap-4">
      <RegistryConfig />
      <TokenIssuer />
      <CertsAndJwks />
    </div>
  );
}

function RegistryConfig() {
  const { persona } = usePersona();
  const toast = useToast();
  const [deptInput, setDeptInput] = useState(persona.dept_id ?? "");
  const [cfg, setCfg] = useState<DockerRegistryConfig | null>(null);
  const [missing, setMissing] = useState(false);
  const [pending, setPending] = useState(false);
  const [busyToggle, setBusyToggle] = useState(false);
  const [busyCreate, setBusyCreate] = useState(false);
  const [busyDelete, setBusyDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  // печатая department_id юзер выпускает пачку загрузок; считаем актуальной
  // только последнюю, чтобы ответ по старому отделу не перетёр свежий
  const loadSeq = useRef(0);

  const load = useCallback(async (dept: string) => {
    if (!dept) return;
    const seq = ++loadSeq.current;
    setPending(true);
    setCfg(null);
    setMissing(false);
    try {
      const r = await getRegistry(dept);
      if (seq !== loadSeq.current) return;
      setCfg(r);
    } catch (e) {
      if (seq !== loadSeq.current) return;
      if (e instanceof ApiError && e.errorCode === "DOCKER_REGISTRY_NOT_CONFIGURED") {
        setMissing(true);
      } else {
        toast.error(errToMessage(e));
      }
    } finally {
      if (seq === loadSeq.current) setPending(false);
    }
  }, [toast]);

  // Авто-load по вводу отдела — с задержкой, чтобы каждый keystroke не дёргал
  // backend (и не спамил toast'ами на 403 по недонабранному dept_id).
  // Reload-кнопка ниже грузит немедленно, минуя debounce.
  useEffect(() => {
    if (!deptInput) return;
    const t = setTimeout(() => void load(deptInput), 450);
    return () => clearTimeout(t);
  }, [deptInput, load]);

  const create = async () => {
    if (busyCreate) return;
    // мутация — авторитетный результат; гасим возможный отстающий load
    loadSeq.current++;
    setBusyCreate(true);
    try {
      const r = await putRegistry(deptInput, {
        pull_policy: "all",
        pull_user_ids: [],
        push_user_ids: [],
      });
      setCfg(r);
      setMissing(false);
      toast.success("Конфиг создан");
    } catch (e) {
      toast.error(errToMessage(e));
    } finally {
      setBusyCreate(false);
    }
  };

  const toggle = async () => {
    if (!cfg || busyToggle) return;
    loadSeq.current++;
    setBusyToggle(true);
    try {
      const r = await patchRegistry(cfg.department_id, {
        is_enabled: !cfg.is_enabled,
      });
      setCfg(r);
    } catch (e) {
      toast.error(errToMessage(e));
    } finally {
      setBusyToggle(false);
    }
  };

  const remove = async () => {
    if (!cfg || busyDelete) return;
    if (!window.confirm(`Удалить конфиг docker registry для ${cfg.department_id}?`))
      return;
    loadSeq.current++;
    setBusyDelete(true);
    try {
      await deleteRegistry(cfg.department_id);
      setCfg(null);
      setMissing(true);
      toast.success("Конфиг удалён");
    } catch (e) {
      toast.error(errToMessage(e));
    } finally {
      setBusyDelete(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold flex items-center gap-2 mb-3">
        <Container className="w-4 h-4 text-accent" /> Конфигурация registry
      </h3>
      <div className="flex items-end gap-2 mb-3">
        <label className="flex flex-col gap-1 text-sm flex-1">
          <span className="text-dim text-xs">department_id</span>
          <input
            className="input mono"
            value={deptInput}
            onChange={(e) => setDeptInput(e.target.value)}
            placeholder="dep_xyz"
          />
        </label>
        <Button
          className="flex items-center gap-1"
          onClick={() => deptInput && void load(deptInput)}
          disabled={pending}
        >
          {pending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCcw className="w-4 h-4" />
          )}{" "}
          Обновить
        </Button>
      </div>
      {cfg && !editing && (
        <div className="flex flex-col gap-2">
          <div className="text-sm">
            <span className="text-dim">is_enabled:</span>{" "}
            <span className="mono">{String(cfg.is_enabled)}</span>{" "}
            <Button variant="ghost"
              className="text-xs"
              onClick={toggle}
              disabled={busyToggle}
            >
              {busyToggle ? <Loader2 className="w-3 h-3 animate-spin inline" /> : "переключить"}
            </Button>
          </div>
          <div className="text-sm">
            <span className="text-dim">pull_policy:</span>{" "}
            <span className="mono">{cfg.pull_policy}</span>
          </div>
          <div className="text-sm">
            <span className="text-dim">pull_user_ids:</span>{" "}
            <span className="mono">{cfg.pull_user_ids.join(", ") || "—"}</span>
          </div>
          <div className="text-sm">
            <span className="text-dim">push_user_ids:</span>{" "}
            <span className="mono">{cfg.push_user_ids.join(", ") || "—"}</span>
          </div>
          <div className="flex gap-2 mt-2">
            <Button
              onClick={() => setEditing(true)}
              disabled={busyDelete || busyToggle}
            >
              Изменить
            </Button>
            <Button variant="danger"
              className="flex items-center gap-1"
              onClick={remove}
              disabled={busyDelete}
            >
              {busyDelete && <Loader2 className="w-4 h-4 animate-spin" />}
              Удалить конфиг
            </Button>
          </div>
          <div className="text-[11px] text-dim mt-2">
            «Изменить» отправляет `PATCH /docker/registry/{"{dept}"}` с
            изменёнными полями. Полная замена (PUT) — через пересоздание после
            удаления.
          </div>
        </div>
      )}
      {cfg && editing && (
        <RegistryEditor
          cfg={cfg}
          onSaved={(next) => {
            setCfg(next);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
      {missing && (
        <div className="text-sm text-dim flex items-center gap-2">
          Конфига нет для этого отдела.
          <Button variant="primary"
            className="flex items-center gap-1"
            onClick={create}
            disabled={busyCreate || !deptInput}
          >
            {busyCreate && <Loader2 className="w-4 h-4 animate-spin" />}
            Создать (pull_policy=all)
          </Button>
        </div>
      )}
    </div>
  );
}

function RegistryEditor({
  cfg,
  onSaved,
  onCancel,
}: {
  cfg: DockerRegistryConfig;
  onSaved: (next: DockerRegistryConfig) => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [pullPolicy, setPullPolicy] = useState<"all" | "restricted">(cfg.pull_policy);
  const [pullIds, setPullIds] = useState(cfg.pull_user_ids.join(", "));
  const [pushIds, setPushIds] = useState(cfg.push_user_ids.join(", "));
  const [isEnabled, setIsEnabled] = useState(cfg.is_enabled);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (busy) return;
    const next: DockerRegistryConfigPatchRequest = {};
    if (pullPolicy !== cfg.pull_policy) next.pull_policy = pullPolicy;
    const pullArr = parseIdList(pullIds);
    if (pullArr.join(",") !== cfg.pull_user_ids.join(",")) next.pull_user_ids = pullArr;
    const pushArr = parseIdList(pushIds);
    if (pushArr.join(",") !== cfg.push_user_ids.join(",")) next.push_user_ids = pushArr;
    if (isEnabled !== cfg.is_enabled) next.is_enabled = isEnabled;

    if (Object.keys(next).length === 0) {
      onCancel();
      return;
    }
    setBusy(true);
    try {
      const r = await patchRegistry(cfg.department_id, next);
      toast.success("Конфиг обновлён");
      onSaved(r);
    } catch (e) {
      toast.error(errToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={isEnabled}
          onChange={(e) => setIsEnabled(e.target.checked)}
        />
        <span>is_enabled</span>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">pull_policy</span>
        <Dropdown
          mode="single"
          className="mono"
          options={[
            { value: "all", label: "all" },
            { value: "restricted", label: "restricted" },
          ]}
          value={pullPolicy}
          onChange={(v) => setPullPolicy(v as "all" | "restricted")}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">
          pull_user_ids (через запятую; обязателен для restricted)
        </span>
        <input
          className="input mono"
          value={pullIds}
          onChange={(e) => setPullIds(e.target.value)}
          placeholder="user_1, user_2"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">push_user_ids (через запятую)</span>
        <input
          className="input mono"
          value={pushIds}
          onChange={(e) => setPushIds(e.target.value)}
          placeholder="user_1, user_2"
        />
      </label>
      <div className="flex gap-2 justify-end">
        <Button onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button variant="primary"
          className="flex items-center gap-1"
          onClick={save}
          disabled={busy}
        >
          {busy && <Loader2 className="w-4 h-4 animate-spin" />} Сохранить
        </Button>
      </div>
    </div>
  );
}

function TokenIssuer() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [service, setService] = useState("");
  const [scope, setScope] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<DockerTokenResponse | null>(null);
  const toast = useToast();

  const submit = async () => {
    setPending(true);
    setResult(null);
    try {
      const r = await getDockerToken(username, password, {
        service: service || undefined,
        scope: scope || undefined,
      });
      setResult(r);
      toast.success("Docker token выдан");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка получения токена");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold flex items-center gap-2 mb-3">
        <KeyRound className="w-4 h-4 text-accent" /> Получить docker token
      </h3>
      <div className="text-xs text-dim mb-3">
        `GET /docker/token` использует Basic-auth. Параметры — те же, что у
        `docker pull`. Поддерживаются: пароль юзера, PAT (`dbos_pat_…`) или
        bot-токен (`dbos_bot_…`).
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">username</span>
          <input
            className="input mono"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">password / PAT / bot-token</span>
          <input
            className="input mono"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">service (опц.)</span>
          <input
            className="input mono"
            value={service}
            onChange={(e) => setService(e.target.value)}
            placeholder="registry.dbos.local"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">scope (опц.)</span>
          <input
            className="input mono"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            placeholder="repository:dep_x/app:pull"
          />
        </label>
      </div>
      <div className="mt-3">
        <Button variant="primary"
          onClick={submit}
          disabled={pending || !username || !password}
        >
          Получить
        </Button>
      </div>
      {result && (
        <div className="mt-3">
          <div className="text-xs text-dim mb-1">Ответ:</div>
          <pre className="mono text-xs whitespace-pre-wrap break-all border border-token p-2 rounded">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function CertsAndJwks() {
  const [pem, setPem] = useState<string | null>(null);
  const [jwks, setJwks] = useState<unknown>(null);
  const [pendingPem, setPendingPem] = useState(false);
  const [pendingJwks, setPendingJwks] = useState(false);
  const toast = useToast();

  const loadCerts = async () => {
    setPendingPem(true);
    try {
      setPem(await getDockerCerts());
    } catch (e) {
      toast.error(apiErrMsg(e, "Ошибка загрузки PEM"));
    } finally {
      setPendingPem(false);
    }
  };
  const loadJwks = async () => {
    setPendingJwks(true);
    try {
      setJwks(await getDockerJwks());
    } catch (e) {
      toast.error(apiErrMsg(e, "Ошибка загрузки JWKS"));
    } finally {
      setPendingJwks(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold flex items-center gap-2 mb-3">
        <FileText className="w-4 h-4 text-accent" /> Сертификаты и JWKS
      </h3>
      <div className="flex gap-2 mb-3">
        <Button
          className="flex items-center gap-1"
          onClick={loadCerts}
          disabled={pendingPem}
        >
          GET /docker/certs
        </Button>
        <Button
          className="flex items-center gap-1"
          onClick={loadJwks}
          disabled={pendingJwks}
        >
          GET /docker/jwks
        </Button>
      </div>
      {pem && (
        <div className="mb-3">
          <div className="text-xs text-dim mb-1">PEM:</div>
          <pre className="mono text-[11px] whitespace-pre-wrap border border-token p-2 rounded max-h-60 overflow-auto">
            {pem}
          </pre>
        </div>
      )}
      {jwks !== null && (
        <div>
          <div className="text-xs text-dim mb-1">JWKS:</div>
          <pre className="mono text-[11px] whitespace-pre-wrap border border-token p-2 rounded max-h-60 overflow-auto">
            {JSON.stringify(jwks, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
