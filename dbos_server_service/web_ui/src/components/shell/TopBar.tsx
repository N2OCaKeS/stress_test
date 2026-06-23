import { Grid3x3 } from "lucide-react";
import { Link } from "react-router-dom";

interface TopBarProps {
  breadcrumb?: string;
}

export function TopBar({ breadcrumb = "Главная" }: TopBarProps) {
  return (
    <header className="surface border-b border-token h-12 px-3 flex items-center gap-3 shrink-0">
      <Link
        to="/home"
        className="text-sm font-semibold flex items-center gap-2 px-2 hover-bg rounded h-8"
        title="EMM — Easy Machine Manager"
      >
        <Grid3x3 className="w-4 h-4 text-accent" />
        EMM
      </Link>
      <span className="text-xs text-dim">/ {breadcrumb}</span>
    </header>
  );
}
