import {
  AlertCircle,
  Check,
  Clock,
  Download,
  Grid3x3,
  RotateCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";

/**
 * Port of mockups/wizard-rotation.html.
 * 5-step rotation wizard, all steps visible. Step 1 done, step 2 in-progress.
 */
export function WizardRotation() {
  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="surface border-b border-token h-12 px-3 flex items-center gap-3 shrink-0">
        <Link to="/" className="text-sm font-semibold flex items-center gap-2">
          <Grid3x3 className="w-4 h-4 text-accent" />
          DBOS
        </Link>
        <span className="text-xs text-dim">
          / Admin / Ротация мастер-ключа
        </span>
        <div className="ml-auto flex items-center gap-2">
          <ThemeSwitcher />
        </div>
      </header>

      <main className="flex-1 flex items-start justify-center p-8">
        <div className="wizard-card wizard-card-wide">
          {/* header */}
          <div className="flex items-start px-6 py-4 border-b border-token">
            <div>
              <div className="text-lg font-semibold flex items-center gap-2">
                <RotateCw className="w-5 h-5 text-warn" />
                Ротация мастер-ключа
              </div>
              <div className="text-xs text-dim mt-0.5">
                account_admin only · cluster-wide операция · ~3-7 мин
              </div>
            </div>
            <Link
              to="/admin"
              className="ml-auto btn btn-ghost"
              title="Отмена"
            >
              <X className="w-5 h-5" />
            </Link>
          </div>

          {/* stepper */}
          <div className="stepper">
            <div className="stepper-step done">
              <span className="num">
                <Check className="w-3 h-3" />
              </span>
              <span>Pre-check</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step active">
              <span className="num">2</span>
              <span>Backup</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">3</span>
              <span>Rotate</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">4</span>
              <span>Verify</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">5</span>
              <span>Finalize</span>
            </div>
          </div>

          {/* step 1 */}
          <section className="step-card">
            <div className="flex items-center gap-2 mb-3">
              <ShieldCheck className="w-4 h-4 text-ok" />
              <h3 className="font-semibold">Шаг 1 · Pre-check</h3>
              <span className="badge badge-ok ml-auto">completed</span>
            </div>
            <div>
              {PRECHECKS.map((c) => (
                <div key={c.label} className="check-row">
                  <Check className="w-4 h-4 text-ok" />
                  <div className="flex-1">{c.label}</div>
                  <span className="text-xs text-dim mono">{c.meta}</span>
                </div>
              ))}
            </div>
          </section>

          {/* step 2 */}
          <section className="step-card">
            <div className="flex items-center gap-2 mb-3">
              <Download className="w-4 h-4 text-accent spin" />
              <h3 className="font-semibold">Шаг 2 · Backup</h3>
              <span className="badge badge-warn ml-auto">in progress</span>
            </div>

            <div className="flex items-center gap-3 mb-2 text-xs text-dim">
              <span>
                Снэпшот текущих credentials и keyset&apos;а до ротации
              </span>
              <span className="ml-auto mono">62%</span>
            </div>
            <div className="progress-wide mb-3">
              <span style={{ width: "62%" }} />
            </div>

            <div className="log-tail mb-3">
              <div>
                <span className="text-dim">[14:32:01]</span> opening snapshot
                session sid=snap_9b21f3
              </div>
              <div>
                <span className="text-dim">[14:32:03]</span> dumping table{" "}
                <span className="text-accent">credentials</span> (134 rows)
              </div>
              <div>
                <span className="text-dim">[14:32:11]</span> dumping table{" "}
                <span className="text-accent">keyset_versions</span> (8 rows)
              </div>
              <div>
                <span className="text-dim">[14:32:14]</span> encrypting snapshot
                with backup_key_v3
              </div>
              <div>
                <span className="text-dim">[14:32:18]</span> uploading to
                s3://dbos-backup/2026-06-10/...{" "}
                <span className="text-warn">[62%]</span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button className="btn btn-danger">
                <X className="w-4 h-4" />
                Стоп
              </button>
              <span className="text-xs text-dim">
                прерывание откатит шаг и вернёт к Pre-check
              </span>
            </div>
          </section>

          {/* step 3 */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <RotateCw className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">Шаг 3 · Rotate</h3>
              <span className="badge ml-auto">pending</span>
            </div>
            <div className="skeleton-line" style={{ width: "80%" }} />
            <div className="skeleton-line" style={{ width: "60%" }} />
            <div className="skeleton-line" style={{ width: "70%" }} />
            <div className="text-xs text-dim mt-2">
              генерация нового keyset_v9 и re-encrypt 134 credentials
            </div>
          </section>

          {/* step 4 */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <ShieldCheck className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">Шаг 4 · Verify</h3>
              <span className="badge ml-auto">pending</span>
            </div>
            <div className="skeleton-line" style={{ width: "75%" }} />
            <div className="skeleton-line" style={{ width: "55%" }} />
            <div className="text-xs text-dim mt-2">
              decrypt каждого credential новым ключом + сверка checksum&apos;ов
            </div>
          </section>

          {/* step 5 */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <Check className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">Шаг 5 · Finalize</h3>
              <span className="badge ml-auto">pending</span>
            </div>
            <div className="skeleton-line" style={{ width: "70%" }} />
            <div className="skeleton-line" style={{ width: "50%" }} />
            <div className="text-xs text-dim mt-2">
              пометка старого keyset как retired · audit event · уведомление
              dep_admin
            </div>
          </section>

          {/* footer */}
          <div className="flex items-center justify-between px-6 py-4 border-t border-token surface-2 rounded-b-xl text-sm">
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1 text-dim">
                <Clock className="w-3.5 h-3.5" />
                elapsed: <span className="mono">01:42</span>
              </span>
              <span className="flex items-center gap-1 text-dim">
                <AlertCircle className="w-3.5 h-3.5" />
                ETA: <span className="mono text-warn">~04:30</span>
              </span>
            </div>
            <Link to="/admin" className="btn btn-danger">
              <X className="w-4 h-4" />
              Отмена
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}

const PRECHECKS = [
  { label: "Cluster healthy", meta: "17/17 pods Running" },
  { label: "Smoke-тесты OK", meta: "12/12 passed · 4.2s" },
  { label: "Нет pending миграций", meta: "alembic head reached" },
  { label: "DLQ пуста", meta: "0 messages" },
];
