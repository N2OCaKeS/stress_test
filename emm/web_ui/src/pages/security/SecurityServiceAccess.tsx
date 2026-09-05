import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { ApiError } from "@/api/client";
import {
  checkServiceAccess,
  type ServiceAccessResponse,
} from "@/api/auth/authorization";
import type { ServiceName } from "@/api/auth/types";
import { Dropdown } from "@/components/ui/Dropdown";
import { useToast } from "@/contexts/ToastContext";
import { useDeptLabel } from "@/lib/labels";
import { ServiceKeyFields } from "./ServiceKeyFields";
import { useServiceKey } from "./useServiceKey";

const KNOWN_SERVICES: ServiceName[] = [
  "auth_service",
  "secret_service",
  "server_service",
  "loging_service",
  "config_service",
  "docker_registry",
];

export function SecurityServiceAccess() {
  const [token, setToken] = useState("");
  const [service, setService] = useState<ServiceName>("config_service");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ServiceAccessResponse | null>(null);
  const svc = useServiceKey();
  const toast = useToast();

  const submit = async () => {
    if (!token.trim()) {
      toast.warn("Введите subject_token");
      return;
    }
    if (!svc.serviceKey.trim()) {
      toast.warn("Введите SERVICE_API_KEY");
      return;
    }
    setPending(true);
    setResult(null);
    try {
      const r = await checkServiceAccess(
        {
          subject_token: token.trim(),
          service_name: service,
        },
        {
          serviceKey: svc.serviceKey.trim(),
          serviceIdentity: svc.serviceIdentity.trim() || undefined,
        },
      );
      setResult(r);
    } catch (e) {
      if (e instanceof ApiError) toast.error(`${e.errorCode}: ${e.message}`);
      else toast.error("Ошибка проверки access");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <ShieldCheck className="w-4 h-4 text-accent" /> POST /authorization/service-access
        </h3>
        <div className="text-xs text-dim mb-3">
          Тонкая обёртка над introspect: возвращает только `allowed` для
          конкретного целевого сервиса и связанные `service_roles`.
        </div>
        <div className="mb-3">
          <ServiceKeyFields svc={svc} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm md:col-span-2">
            <span className="text-dim text-xs">subject_token</span>
            <textarea
              className="input mono"
              rows={3}
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">service_name</span>
            <Dropdown
              mode="single"
              className="mono"
              options={KNOWN_SERVICES.map((s) => ({ value: s, label: s }))}
              value={service}
              onChange={(v) => setService(v as ServiceName)}
            />
          </label>
        </div>
        <div className="mt-3">
          <button className="btn btn-primary" onClick={submit} disabled={pending}>
            Проверить
          </button>
        </div>
      </div>

      {result && (
        <div className="card">
          <h3 className="font-semibold mb-3">Результат</h3>
          <div className="flex items-center gap-2 mb-3">
            <span
              className={`badge ${result.allowed ? "active" : "danger"}`}
            >
              allowed: {String(result.allowed)}
            </span>
            {result.department_id && (
              <ResultDeptBadge deptId={result.department_id} />
            )}
          </div>
          {result.service_roles && (
            <div className="text-sm">
              <span className="text-dim">service_roles:</span>{" "}
              <span className="mono">
                {result.service_roles.join(", ") || "—"}
              </span>
            </div>
          )}
          <pre className="mono text-xs whitespace-pre-wrap break-all border border-token p-2 rounded mt-3">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function ResultDeptBadge({ deptId }: { deptId: string }) {
  const label = useDeptLabel(deptId);
  return <span className="badge">отдел: {label}</span>;
}
