import { Link } from "react-router-dom";
import { Compass } from "lucide-react";

export function NotFound() {
  return (
    <div className="gradient-bg min-h-screen flex items-center justify-center p-8">
      <div className="card max-w-md text-center flex flex-col items-center gap-4">
        <Compass className="w-12 h-12 text-accent" />
        <div className="text-3xl font-bold">404</div>
        <div className="text-dim">Такой страницы нет.</div>
        <Link to="/home" className="btn btn-primary">
          → На главную
        </Link>
      </div>
    </div>
  );
}
