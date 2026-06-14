import {
  ChevronLeft,
  ChevronRight,
  Cloud,
  Database,
  Download,
  Github,
  Grid3x3,
  KeyRound,
  Terminal,
  Webhook,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";

/**
 * Credential-creation wizard. 4-step layout, all steps shown side-by-side
 * for demo (step 1 active); not yet wired to an interactive flow.
 */
export function WizardCreateCredential() {
  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="surface border-b border-token h-12 px-3 flex items-center gap-3 shrink-0">
        <Link to="/" className="text-sm font-semibold flex items-center gap-2">
          <Grid3x3 className="w-4 h-4 text-accent" />
          DBOS
        </Link>
        <span className="text-xs text-dim">
          / Secrets / Создание credential
        </span>
        <div className="ml-auto flex items-center gap-2">
          <ThemeSwitcher />
          <Link
            to="/secret"
            className="btn btn-ghost"
            title="Закрыть"
          >
            <X className="w-4 h-4" />
          </Link>
        </div>
      </header>

      <main className="flex-1 flex items-start justify-center p-8">
        <div className="wizard-card">
          {/* header */}
          <div className="flex items-center px-6 py-4 border-b border-token">
            <div>
              <div className="text-lg font-semibold">Создать credential</div>
              <div className="text-xs text-dim">
                мастер из 4 шагов · все шаги показаны для демо
              </div>
            </div>
            <Link
              to="/secret"
              className="ml-auto btn btn-ghost"
              title="Отмена"
            >
              <X className="w-5 h-5" />
            </Link>
          </div>

          {/* stepper */}
          <div className="stepper">
            <div className="stepper-step active">
              <span className="num">1</span>
              <span>Тип</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">2</span>
              <span>Название</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">3</span>
              <span>Содержимое</span>
            </div>
            <div className="stepper-sep" />
            <div className="stepper-step">
              <span className="num">4</span>
              <span>ACL</span>
            </div>
          </div>

          {/* step 1: type */}
          <section className="step-card">
            <div className="flex items-center gap-2 mb-3">
              <Database className="w-4 h-4 text-accent" />
              <h3 className="font-semibold">Шаг 1 · Тип credential</h3>
              <span className="text-xs text-dim ml-auto">обязательно</span>
            </div>
            <div className="text-xs text-dim mb-4">
              Выберите, что вы храните. От типа зависят валидаторы и формат
              содержимого.
            </div>

            <div className="grid grid-cols-2 gap-3">
              {TYPES.map((t, i) => (
                <div
                  key={t.title}
                  className={`type-tile ${i === 0 ? "selected" : ""}`}
                >
                  <t.icon className="type-icon w-5 h-5" />
                  <div className="text-sm font-medium">{t.title}</div>
                  <div className="text-xs text-dim">{t.subtitle}</div>
                </div>
              ))}
            </div>
          </section>

          {/* step 2: name */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <ChevronRight className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">
                Шаг 2 · Название и принадлежность
              </h3>
            </div>

            <label className="block text-xs text-dim mb-1">
              Имя credential <span className="text-danger">*</span>
            </label>
            <input
              className="input mb-3"
              placeholder="напр. github-bot-token-ci"
            />

            <label className="block text-xs text-dim mb-1">Department</label>
            <select className="input mb-3">
              <option>ДБОС (Ядро DBOS)</option>
              <option>ДТКК</option>
              <option>Инфра</option>
            </select>

            <label className="block text-xs text-dim mb-1">Описание</label>
            <textarea
              className="input"
              placeholder="зачем нужен, кто пользуется, ссылка на тикет..."
            />
          </section>

          {/* step 3: content */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <ChevronRight className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">Шаг 3 · Содержимое</h3>
            </div>

            <label className="block text-xs text-dim mb-1">
              Token / секрет <span className="text-danger">*</span>
            </label>
            <textarea
              className="input mb-3"
              placeholder="вставьте значение (никуда не логируется)"
            />

            <div className="text-[11px] text-dim mb-2">
              — или загрузить из файла —
            </div>
            <div className="surface-2 border border-token rounded p-3 mb-4 flex items-center gap-3 text-sm">
              <Download className="w-4 h-4 text-accent" />
              <div className="flex-1">
                <div>Перетащите файл сюда</div>
                <div className="text-xs text-dim">
                  .pem · .key · .txt до 1 MiB
                </div>
              </div>
              <button className="btn">Browse</button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-dim mb-1">
                  Valid from
                </label>
                <input
                  className="input"
                  type="datetime-local"
                  defaultValue="2026-06-10T14:00"
                />
              </div>
              <div>
                <label className="block text-xs text-dim mb-1">
                  Valid until (optional)
                </label>
                <input
                  className="input"
                  type="datetime-local"
                  defaultValue="2026-12-10T14:00"
                />
              </div>
            </div>
          </section>

          {/* step 4: ACL */}
          <section className="step-card inactive">
            <div className="flex items-center gap-2 mb-3">
              <ChevronRight className="w-4 h-4 text-dim" />
              <h3 className="font-semibold">Шаг 4 · ACL</h3>
            </div>

            <div className="text-xs text-dim mb-2">Scope</div>
            <div className="grid grid-cols-3 gap-2 mb-4 text-sm">
              <label className="flex items-center gap-2 surface-2 border border-token rounded p-2 cursor-pointer">
                <input type="radio" name="scope" /> my
              </label>
              <label
                className="flex items-center gap-2 surface-2 border border-token rounded p-2 cursor-pointer"
                style={{ borderColor: "var(--accent)" }}
              >
                <input type="radio" name="scope" defaultChecked /> my_dep
              </label>
              <label className="flex items-center gap-2 surface-2 border border-token rounded p-2 cursor-pointer">
                <input type="radio" name="scope" /> cross_dep
              </label>
            </div>

            <div className="text-xs text-dim mb-2">Per-user grants</div>
            <div className="surface-2 border border-token rounded p-3 mb-4">
              <div className="grant-row">
                <div>
                  <span className="mono">alice</span> · dep_admin
                </div>
                <span className="badge">read+rotate</span>
                <button className="btn btn-ghost">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="grant-row">
                <div>
                  <span className="mono">pavel</span> · server.admin (Инфра)
                </div>
                <span className="badge">read</span>
                <button className="btn btn-ghost">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <button className="btn btn-ghost text-accent text-xs mt-2">
                + добавить пользователя
              </button>
            </div>

            <div className="text-xs text-dim mb-2">Per-bot grants</div>
            <div className="surface-2 border border-token rounded p-3">
              <div className="grant-row">
                <div>
                  <span className="mono">ci-deploy-bot</span> · dept Инфра
                </div>
                <span className="badge">read</span>
                <button className="btn btn-ghost">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <button className="btn btn-ghost text-accent text-xs mt-2">
                + добавить бота
              </button>
            </div>
          </section>

          {/* footer */}
          <div className="flex items-center justify-between px-6 py-4 border-t border-token surface-2 rounded-b-xl">
            <button
              className="btn"
              disabled
              style={{ opacity: 0.5, cursor: "not-allowed" }}
            >
              <ChevronLeft className="w-4 h-4" />
              Назад
            </button>
            <div className="text-xs text-dim">
              Шаг <b>1</b> из 4
            </div>
            <button className="btn btn-primary">
              Далее
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

const TYPES = [
  { icon: KeyRound, title: "API token", subtitle: "bearer / HMAC, opaque string" },
  { icon: Database, title: "DB password", subtitle: "PostgreSQL / Mongo / Clickhouse" },
  { icon: Terminal, title: "SSH password", subtitle: "пароль root / sudoer на сервере" },
  { icon: Cloud, title: "AWS / cloud key", subtitle: "access_key + secret_key пара" },
  { icon: Github, title: "SSH private key", subtitle: "PEM / OpenSSH, с passphrase" },
  { icon: Webhook, title: "Webhook secret", subtitle: "подпись HMAC для callback'ов" },
];
