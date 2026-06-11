/**
 * Live-управление retention-политикой loging_service.
 *
 * Показывает активную политику (`GET /retention`), форму задания
 * (`PUT /retention`) и кнопку отключения ротации (`DELETE /retention`).
 * Доступ — только `loging_admin` (backend router-level `require_admin`).
 *
 * Ручного запуска sweep'а из API нет — фоновая ротация идёт 00:00 MSK.
 * Sweep-история и размер таблицы в backend-контракте не отдаются, поэтому
 * этих блоков (в отличие от mock-порта) здесь нет.
 */
import { useState } from "react";
import { Archive, AlertCircle, Save, Trash2, Clock } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import {
  disableRetention,
  getRetention,
  setRetention,
} from "@/api/loging/retention";
import type { RetentionPolicyPutRequest, Severity } from "@/api/loging/types";

const SEVERITIES: Severity[] = [
  "TRACE",
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
];

function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) return `${e.errorCode}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return fallback;
}

export function LogRetentionLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const canWrite = persona.platform_role === "logging_admin";

  const policyQ = useQuery(() => getRetention(), []);
  const policy = policyQ.data ?? null;

  const [retainDays, setRetainDays] = useState("");
  const [description, setDescription] = useState("");
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [serviceFilter, setServiceFilter] = useState("");
  const [busy, setBusy] = useState(false);

  // Подхватываем текущее значение в форму, когда GET вернул политику и
  // поле ещё не трогали (пустая строка). retain_days вне диапазона невозможен —
  // backend гарантирует [30, 3650].
  if (policy && retainDays === "") {
    setRetainDays(String(policy.retain_days));
    if (policy.description) setDescription(policy.description);
  }

  async function handleSave() {
    if (busy) return;
    const days = Number(retainDays);
    if (!Number.isFinite(days) || days < 30 || days > 3650) {
      toast.warn("retain_days должен быть в диапазоне [30, 3650]");
      return;
    }
    const body: RetentionPolicyPutRequest = {
      retain_days: days,
      description: description.trim() || null,
      severity_filter: severityFilter.length ? severityFilter : null,
      service_filter: parseServices(serviceFilter),
    };
    setBusy(true);
    try {
      await setRetention(body);
      toast.success("Retention-политика сохранена");
      policyQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Сохранение не удалось"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    if (busy) return;
    if (typeof window !== "undefined") {
      const ok = window.confirm(
        "Отключить ротацию? События будут храниться вечно, пока не задать новую политику.",
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      await disableRetention();
      toast.success("Ротация отключена");
      setRetainDays("");
      setDescription("");
      setSeverityFilter([]);
      setServiceFilter("");
      policyQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Отключение не удалось"));
    } finally {
      setBusy(false);
    }
  }

  function toggleSeverity(s: Severity) {
    setSeverityFilter((cur) =>
      cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s],
    );
  }

  return (
    <Shell breadcrumb="loging_service / retention">
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="scroll-block max-w-4xl w-full mx-auto px-8 py-8">
          <section className="mb-6">
            <div className="text-2xl font-bold mb-1 flex items-center gap-2">
              <Archive className="w-6 h-6 text-accent" /> Retention policy
            </div>
            <div className="text-dim text-sm">
              Срок хранения audit-событий. Фоновая ротация — 00:00 MSK. События
              loging_service от ротации защищены всегда.
            </div>
          </section>

          {policyQ.loading && (
            <div className="card text-sm text-dim">Загрузка…</div>
          )}
          {policyQ.error && (
            <div className="alert alert-danger flex items-start gap-2 mb-6">
              <AlertCircle className="w-4 h-4 mt-0.5" />
              <div className="flex-1 text-sm">
                <div>{apiErrMsg(policyQ.error, "Политика не загрузилась")}</div>
                <button
                  className="btn btn-ghost mt-2"
                  onClick={() => policyQ.refetch()}
                >
                  Повторить
                </button>
              </div>
            </div>
          )}

          {!policyQ.loading && !policyQ.error && (
            <section className="grid gap-4 md:grid-cols-2 mb-6">
              <div className="card">
                <div className="stat-label flex items-center gap-2">
                  <Clock className="w-3 h-3" /> Активная политика
                </div>
                {policy ? (
                  <>
                    <div className="stat-big">{policy.retain_days} days</div>
                    <div className="text-sm mt-3">
                      <div className="stat-row">
                        <span className="text-dim">severity</span>
                        <span className="mono">{policy.severity ?? "все"}</span>
                      </div>
                      <div className="stat-row">
                        <span className="text-dim">service</span>
                        <span className="mono">{policy.service ?? "все"}</span>
                      </div>
                      <div className="stat-row">
                        <span className="text-dim">id</span>
                        <span className="mono text-xs">{policy.id}</span>
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="stat-big text-dim">ротация выключена</div>
                    <div className="text-xs text-dim mt-2">
                      События хранятся бессрочно. Задайте политику справа.
                    </div>
                  </>
                )}
              </div>

              <div className="card">
                <div className="stat-label flex items-center gap-2">
                  <Save className="w-3 h-3" /> Задать политику
                </div>
                {!canWrite ? (
                  <div className="text-sm text-dim mt-3">
                    Управление retention доступно только loging_admin.
                  </div>
                ) : (
                  <div className="flex flex-col gap-3 mt-3">
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="text-dim text-xs">
                        retain_days * (30…3650)
                      </span>
                      <input
                        className="surface-2 border border-token rounded px-2 py-1"
                        type="number"
                        min={30}
                        max={3650}
                        value={retainDays}
                        onChange={(e) => setRetainDays(e.target.value)}
                        placeholder="90"
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="text-dim text-xs">description</span>
                      <input
                        className="surface-2 border border-token rounded px-2 py-1"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="опционально"
                      />
                    </label>
                    <div className="flex flex-col gap-1 text-sm">
                      <span className="text-dim text-xs">
                        severity_filter (пусто = все)
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {SEVERITIES.map((s) => (
                          <button
                            key={s}
                            type="button"
                            onClick={() => toggleSeverity(s)}
                            className={`badge ${
                              severityFilter.includes(s) ? "badge-ok" : ""
                            }`}
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                    </div>
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="text-dim text-xs">
                        service_filter (через запятую, пусто = все)
                      </span>
                      <input
                        className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
                        value={serviceFilter}
                        onChange={(e) => setServiceFilter(e.target.value)}
                        placeholder="auth_service, server_service"
                      />
                    </label>
                    <div className="flex items-center gap-2 mt-1">
                      <button
                        className="btn btn-primary flex items-center gap-1"
                        onClick={handleSave}
                        disabled={busy}
                      >
                        <Save className="w-4 h-4" />{" "}
                        {busy ? "Сохраняем…" : "Сохранить"}
                      </button>
                      {policy && (
                        <button
                          className="btn btn-danger flex items-center gap-1"
                          onClick={handleDisable}
                          disabled={busy}
                        >
                          <Trash2 className="w-4 h-4" /> Отключить
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}
        </div>
      </main>
    </Shell>
  );
}

/** Парсит CSV-строку сервисов в массив (или null, если пусто). */
function parseServices(raw: string): string[] | null {
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length ? parts : null;
}
