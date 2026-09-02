import { useRef, useState, type FormEvent } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import {
  AlertCircle,
  Eye,
  EyeOff,
  Loader,
  Lock,
  ShieldCheck,
  User,
} from "lucide-react";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";
import { CertHelpModal } from "@/components/CertHelpModal";
import { useAuth, USE_MOCK_AUTH } from "@/contexts/AuthContext";
import { ApiError } from "@/api/client";

const SERVICE_NAME = "EMM";
const SERVICE_VERSION = "1.0.0";
const SERVICE_FULL =
  "Easy Machine Manager — платформа управления тестовыми серверами и виртуальными машинами";
const SERVICE_TOOLTIP = "EMM — Easy Machine Manager";

export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const auth = useAuth();
  // RouteGuard кладёт сюда исходный путь, когда отбивает гостя на /login.
  // После успешного входа возвращаем туда, а не безусловно на /home.
  const from = (location.state as { from?: string } | null)?.from;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [certHelpOpen, setCertHelpOpen] = useState(false);
  // Авто-помощь по cert показываем один раз за жизнь экрана, чтобы серия
  // повторных попыток входа не мигала модалкой на каждый submit.
  const certAutoShown = useRef(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await auth.login({ username, password });
      const dest = from && from !== "/login" && from !== "/" ? from : "/home";
      navigate(dest, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        // Сетевой обрыв на логине почти всегда означает недоверенный CA:
        // браузер режет fetch к /api ещё до ответа. Подсказываем установку
        // сертификата автоматически (один раз).
        setError(
          new ApiError(0, {
            error: "network_error",
            error_code: "NETWORK_ERROR",
            message: "Не удалось связаться с auth_service",
          }),
        );
        if (!certAutoShown.current) {
          certAutoShown.current = true;
          setCertHelpOpen(true);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  const isLockout = error?.errorCode === "ACCOUNT_TEMPORARILY_LOCKED";
  const isRateLimit = error?.errorCode === "RATE_LIMIT_EXCEEDED";
  const isNetworkError = error?.errorCode === "NETWORK_ERROR";

  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="surface border-b border-token h-12 px-4 flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold" title={SERVICE_TOOLTIP}>
          {SERVICE_NAME}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-dim">Тема:</label>
          <ThemeSwitcher />
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center p-8">
        <section className="w-full max-w-md flex flex-col gap-6">
          <div className="login-card">
            <div className="text-center mb-6">
              <h1 className="text-2xl font-bold mb-1">Добро пожаловать</h1>
              <div className="text-sm text-accent font-semibold mt-2">
                {SERVICE_NAME}
              </div>
              <p className="text-xs text-dim mt-1 leading-snug">
                {SERVICE_FULL}
              </p>
            </div>

            {error && (
              <div className="alert-danger mb-4">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <div>
                  <div className="font-semibold">
                    {isLockout
                      ? "Аккаунт временно заблокирован"
                      : isRateLimit
                        ? "Слишком много попыток"
                        : "Ошибка входа"}
                  </div>
                  <div className="text-xs opacity-80 mt-0.5">{error.message}</div>
                  {(isLockout || isRateLimit) && error.retryAfter !== undefined && (
                    <div className="text-xs opacity-80 mt-1">
                      Повтор через {Math.ceil(error.retryAfter / 60) || 1} мин
                      ({error.retryAfter} сек).
                    </div>
                  )}
                  {isNetworkError && (
                    <button
                      type="button"
                      className="text-xs underline mt-1"
                      onClick={() => setCertHelpOpen(true)}
                    >
                      Похоже на недоверенный сертификат — как установить
                    </button>
                  )}
                  <div className="text-[10px] opacity-60 mt-1 mono">
                    {error.errorCode}
                  </div>
                </div>
              </div>
            )}

            <form className="space-y-4" onSubmit={onSubmit}>
              <div>
                <label className="block text-xs text-dim mb-1" htmlFor="username">
                  Логин
                </label>
                <div className="input-wrap">
                  <input
                    id="username"
                    type="text"
                    className="input pr-9"
                    placeholder="ivanov"
                    autoComplete="username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={submitting}
                    autoFocus
                  />
                  <User className="input-icon w-4 h-4" />
                </div>
              </div>

              <div>
                <label className="block text-xs text-dim mb-1" htmlFor="password">
                  Пароль
                </label>
                <div className="input-wrap">
                  <input
                    id="password"
                    type={showPw ? "text" : "password"}
                    className="input pr-9"
                    placeholder="••••••••"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                  />
                  {showPw ? (
                    <EyeOff
                      className="input-icon w-4 h-4 cursor-pointer"
                      onClick={() => setShowPw(false)}
                    />
                  ) : (
                    <Eye
                      className="input-icon w-4 h-4 cursor-pointer"
                      onClick={() => setShowPw(true)}
                    />
                  )}
                </div>
              </div>

              <button
                type="submit"
                disabled={submitting || !username || !password}
                className="btn btn-primary w-full py-2.5 text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
              >
                {submitting ? (
                  <>
                    <Loader className="w-4 h-4 spin" />
                    <span>Проверка...</span>
                  </>
                ) : (
                  <>
                    <Lock className="w-4 h-4" />
                    <span>Войти</span>
                  </>
                )}
              </button>
            </form>

            <div className="mt-5 text-center text-xs text-dim">
              Забыли пароль? Обратитесь к своему руководителю.
            </div>

            <div className="mt-3 text-center">
              <button
                type="button"
                className="text-xs text-accent hover:underline inline-flex items-center gap-1"
                onClick={() => setCertHelpOpen(true)}
              >
                <ShieldCheck className="w-3.5 h-3.5" />
                Проблемы с подключением? Установить сертификат
              </button>
            </div>
          </div>

          {USE_MOCK_AUTH && (
            <div className="text-center text-xs text-dim">
              <Link to="/" className="text-accent hover:underline">
                → DEV: persona-picker (mock)
              </Link>
            </div>
          )}
        </section>
      </main>

      <footer className="border-t border-token surface px-6 py-3 flex items-center justify-center text-xs text-dim">
        {SERVICE_NAME} · <span className="mono ml-1">v{SERVICE_VERSION}</span>
      </footer>

      <CertHelpModal
        open={certHelpOpen}
        onClose={() => setCertHelpOpen(false)}
      />
    </div>
  );
}
