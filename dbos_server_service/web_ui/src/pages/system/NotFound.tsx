import { AlertCircle, ArrowLeft, Frown, Grid3x3, X } from "lucide-react";
import { Link } from "react-router-dom";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";
import { USE_MOCK_AUTH } from "@/contexts/AuthContext";

const MSK_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatMsk(d: Date): string {
  // ru-RU даёт "DD.MM.YYYY, HH:mm:ss" — приводим к "YYYY-MM-DD HH:mm:ss MSK"
  const parts = MSK_FORMATTER.formatToParts(d).reduce<Record<string, string>>(
    (acc, p) => {
      if (p.type !== "literal") acc[p.type] = p.value;
      return acc;
    },
    {},
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} MSK`;
}

/**
 * Branded 404 page. Чисто клиентская страница: backend-запроса нет,
 * поэтому request_id здесь не показываем — пользователь видит метку времени
 * (по ней админ может найти соответствующий лог).
 */
export function NotFound() {
  const now = formatMsk(new Date());
  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="surface border-b border-token h-12 px-4 flex items-center gap-3 shrink-0">
        <Link to="/" className="text-sm font-semibold flex items-center gap-2">
          <Grid3x3 className="w-4 h-4 text-accent" />
          EMM
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
              Выйти
            </Link>
          </div>

          <div className="surface-2 border border-token rounded-lg p-4 text-left">
            <div className="text-xs text-dim flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                Если думаете, что это ошибка — напишите в канал{" "}
                <span className="mono">#dbos-support</span> и приложите URL и
                время. Время: <span className="mono">{now}</span>.
              </div>
            </div>
          </div>
        </div>
      </main>

      <footer className="border-t border-token surface px-6 py-3 flex items-center justify-between text-xs text-dim">
        <div>
          EMM · <span className="mono">v1.8.5.46</span>
        </div>
        {USE_MOCK_AUTH && (
          <Link to="/" className="hover:underline">
            demo · persona-picker
          </Link>
        )}
      </footer>
    </div>
  );
}
