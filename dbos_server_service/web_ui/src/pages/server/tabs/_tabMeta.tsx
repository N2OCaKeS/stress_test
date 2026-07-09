import {
  MonitorPlay,
  Cpu,
  Power,
  Users,
  TerminalSquare,
  Package,
  ShieldCheck,
  HardDrive,
  Camera,
} from "lucide-react";

/**
 * Значки вкладок карточки — единый набор для сервера и ВМ, чтобы таб-бар
 * выглядел одинаково у обеих сущностей. Ключ — id вкладки; стиль икон взят из
 * прежней VM-панели. Серверный `ipmi` и VM-специфичные `power`/`disks`/
 * `snapshots` берут иконки из того же набора lucide.
 */
export const TAB_ICON: Record<string, React.ReactNode> = {
  overview: <MonitorPlay className="w-4 h-4" />,
  hardware: <Cpu className="w-4 h-4" />,
  ipmi: <Power className="w-4 h-4" />,
  power: <Power className="w-4 h-4" />,
  accounts: <Users className="w-4 h-4" />,
  console: <TerminalSquare className="w-4 h-4" />,
  packages: <Package className="w-4 h-4" />,
  manage: <ShieldCheck className="w-4 h-4" />,
  disks: <HardDrive className="w-4 h-4" />,
  snapshots: <Camera className="w-4 h-4" />,
};
