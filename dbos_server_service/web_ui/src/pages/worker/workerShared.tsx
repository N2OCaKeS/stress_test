import {
  HardDrive,
  Terminal,
  Zap,
  Database,
  Box,
  Cog,
  HeartPulse,
  type LucideIcon,
} from "lucide-react";

export type TaskStatus = "OK" | "RUN" | "Q" | "FAIL" | "RTY" | "ORP" | "summ";

export interface TaskRow {
  id: string;
  icon: LucideIcon;
  iconClass: string;
  title: string;
  target: string;
  meta: string;
  status: TaskStatus;
  statusBadgeClass: string;
  running?: boolean;
}

export function MetaPiece({
  pieces,
}: {
  pieces: { text: string; isHeartbeat?: boolean }[];
}) {
  return (
    <div className="text-[11px] text-dim flex items-center gap-2 flex-wrap">
      {pieces.map((p, i) => (
        <span key={i} className="flex items-center gap-1">
          {p.isHeartbeat && <HeartPulse className="w-3 h-3 inline" />}
          {p.text}
          {i < pieces.length - 1 && <span className="ml-1">·</span>}
        </span>
      ))}
    </div>
  );
}

export function statusBadge(status: TaskStatus): string {
  switch (status) {
    case "OK":
      return "badge badge-ok";
    case "FAIL":
    case "ORP":
      return "badge badge-danger";
    case "RUN":
    case "RTY":
      return "badge badge-warn";
    case "Q":
    case "summ":
      return "badge";
  }
}

export const ICON_KIND: Record<string, LucideIcon> = {
  disk: HardDrive,
  ssh: Terminal,
  power: Zap,
  db: Database,
  pkg: Box,
  cog: Cog,
};
