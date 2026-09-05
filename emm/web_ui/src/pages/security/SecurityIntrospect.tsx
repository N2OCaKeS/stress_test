import { useState } from "react";
import { ScanSearch } from "lucide-react";
import { ApiError } from "@/api/client";
import { introspect, type IntrospectResponse } from "@/api/auth/authorization";
import { useToast } from "@/contexts/ToastContext";
import { ServiceKeyFields } from "./ServiceKeyFields";
import { useServiceKey } from "./useServiceKey";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

export function SecurityIntrospect() {
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<IntrospectResponse | null>(null);
  const svc = useServiceKey();
  const toast = useToast();

  const submit = async () => {
    if (!token.trim()) {
      toast.warn("Введите токен");
      return;
    }
    if (!svc.serviceKey.trim()) {
      toast.warn("Введите SERVICE_API_KEY");
      return;
    }
    setPending(true);
    setResult(null);
    try {
      const r = await introspect(
        { token: token.trim() },
        {
          serviceKey: svc.serviceKey.trim(),
          serviceIdentity: svc.serviceIdentity.trim() || undefined,
        },
      );
      setResult(r);
    } catch (e) {
      if (e instanceof ApiError) toast.error(`${e.errorCode}: ${e.message}`);
      else toast.error("Ошибка introspect");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <ScanSearch className="w-4 h-4 text-accent" /> POST /authorization/introspect
        </h3>
        <div className="text-xs text-dim mb-3">
          Принимает JWT, PAT (`dbos_pat_…`) или bot-токен (`dbos_bot_…`).
          Возвращает identity и effective `allowed_services` / `service_roles`,
          вычисленные из БД (а не из JWT payload).
        </div>
        <ServiceKeyFields svc={svc} />
        <label className="flex flex-col gap-1 text-sm mt-3">
          <span className="text-dim text-xs">token</span>
          <textarea
            className="input mono"
            rows={4}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="eyJhbGciOiJI… / dbos_pat_… / dbos_bot_…"
          />
        </label>
        <div className="mt-3">
          <Button variant="primary" onClick={submit} disabled={pending}>
            Introspect
          </Button>
        </div>
      </div>

      {result && (
        <div className="card">
          <h3 className="font-semibold mb-3">Результат</h3>
          <div className="flex items-center gap-2 mb-3">
            <Badge kind={result.active ? "ok" : "danger"}>
              active: {String(result.active)}
            </Badge>
            {result.subject_type && (
              <Badge>{result.subject_type}</Badge>
            )}
            {result.is_banned && <Badge kind="danger">забанен</Badge>}
            {result.must_change_password && (
              <Badge className="warn">must_change_password</Badge>
            )}
          </div>
          <pre className="mono text-xs whitespace-pre-wrap break-all border border-token p-2 rounded">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
