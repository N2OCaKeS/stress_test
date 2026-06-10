import { AlertCircle, ArrowLeft, Frown, Grid3x3, X } from "lucide-react";
import { Link } from "react-router-dom";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";

/**
 * Port of mockups/404.html.
 * Branded header + error card with request_id + footer.
 */
export function NotFound() {
  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="surface border-b border-token h-12 px-4 flex items-center gap-3 shrink-0">
        <Link to="/" className="text-sm font-semibold flex items-center gap-2">
          <Grid3x3 className="w-4 h-4 text-accent" />
          DBOS Server Manager
        </Link>
        <span className="text-xs text-dim ml-2">404</span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-dim">Тема:</label>
          <ThemeSwitcher />
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center p-8">
        <div className="error-card">
          <div className="flex justify-center mb-5">
            <Frown className="text-warn" style={{ width: 96, height: 96 }} />
          </div>
          <h1 className="text-2xl font-bold mb-2">
            404 · Страница не найдена
          </h1>
          <p className="text-dim text-sm mb-6">
            Ресурс не существует или у вас нет к нему доступа.
          </p>

          <div className="flex items-center justify-center gap-3 mb-7">
            <Link
              to="/home"
              className="btn btn-primary flex items-center gap-2"
            >
              <ArrowLeft className="w-4 h-4" />
              На главную
            </Link>
            <Link to="/login" className="btn flex items-center gap-2">
              <X className="w-4 h-4" />
              Logout
            </Link>
          </div>

          <div className="surface-2 border border-token rounded-lg p-4 text-left">
            <div className="text-[11px] uppercase tracking-wider text-dim mb-2">
              Request ID
            </div>
            <div className="mono text-sm text-accent break-all mb-3">
              req_8c2a4f91-3b6d-4e2a-9f1c-7d8e0b2c5a3f
            </div>
            <div className="text-xs text-dim flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                Если думаете, что это ошибка — отправьте{" "}
                <span className="mono">request_id</span> админу (или в канал{" "}
                <span className="mono">#dbos-support</span>). Время:{" "}
                <span className="mono">2026-06-10 14:32:07 MSK</span>.
              </div>
            </div>
          </div>
        </div>
      </main>

      <footer className="border-t border-token surface px-6 py-3 flex items-center justify-between text-xs text-dim">
        <div>
          DBOS Server Manager · <span className="mono">v1.8.5.46</span>
        </div>
        <Link to="/" className="hover:underline">
          demo · persona-picker
        </Link>
      </footer>
    </div>
  );
}
