import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Grid3x3, LogIn } from "lucide-react";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";

/**
 * Login screen stub. In mockup mode any submit just navigates back to /.
 * Real auth_service POST /api/auth/v1/login will wire here in Phase 3.
 */
export function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="border-b border-token surface px-6 h-12 flex items-center gap-3">
        <Grid3x3 className="w-4 h-4 text-accent" />
        <span className="text-sm font-semibold">DBOS Server Manager</span>
        <div className="ml-auto">
          <ThemeSwitcher />
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center p-8">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            navigate("/");
          }}
          className="card w-full max-w-sm flex flex-col gap-4"
        >
          <div className="flex items-center gap-2 text-lg font-semibold">
            <LogIn className="w-5 h-5 text-accent" />
            Вход в DBOS
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">Логин</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full surface-2 border border-token rounded px-2 py-1.5 text-sm"
              autoFocus
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">Пароль</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full surface-2 border border-token rounded px-2 py-1.5 text-sm"
            />
          </div>
          <button type="submit" className="btn btn-primary">
            Войти
          </button>
          <Link to="/" className="text-xs text-accent text-center">
            ← Mockup persona selector
          </Link>
        </form>
      </main>
    </div>
  );
}
