import { Link } from "react-router-dom";
import { EmmLogo } from "@/components/Logo";

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
        <EmmLogo className="w-5 h-5" />
        <span className="tracking-wide">EMM</span>
      </Link>
      <span className="text-xs text-dim">/ {breadcrumb}</span>
    </header>
  );
}
