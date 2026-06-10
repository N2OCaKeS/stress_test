import type { ReactNode } from "react";
import { Shell } from "@/components/shell/Shell";

interface HomeShellProps {
  title: ReactNode;
  subtitle: ReactNode;
  children: ReactNode;
}

/**
 * Common layout for persona-specific Home dashboards.
 *
 * Wraps the page in `<Shell breadcrumb="Главная">`, builds the centered
 * scroll-block container and renders a "welcome" hero (large title +
 * one-line subtitle). Each Home*Admin variant supplies its own sections
 * (quick tiles, stats, activity, tip footer) as `children`.
 */
export function HomeShell({ title, subtitle, children }: HomeShellProps) {
  return (
    <Shell breadcrumb="Главная">
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="scroll-block max-w-6xl w-full mx-auto px-8 py-8">
          <section className="mb-8">
            <div className="text-3xl font-bold mb-1">{title}</div>
            <div className="text-dim">{subtitle}</div>
          </section>
          {children}
        </div>
      </main>
    </Shell>
  );
}
