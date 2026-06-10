import { ShieldCheck } from "lucide-react";
import { PLATFORM_ROLES } from "@/mocks/cluster";
import { InlineEditor, StatRow } from "./_inline";

/**
 * Platform roles surface — the fixed enum (`account_admin`, `dep_admin`,
 * `logging_admin`, `logging_reader`) baked into `auth_service` and not
 * managed via HTTP. CRUD'ы для per-`(department, service)`-каталога ролей
 * (что в API называется service_roles) живут в `pages/serviceRoles/*Admin`
 * — там идёт реальный wire to `GET/POST/PATCH/DELETE /departments/{id}/services/{svc}/roles`
 * через `src/api/auth/service_roles.ts`. Этот файл остаётся плоским
 * списком, потому что назначать платформенную роль можно только через
 * `PATCH /users/{id}` (`platform_role` поле), а каталог — это сам enum.
 */
export function ServicesPlatformRoles() {
  // Platform roles — read-only fixed set defined in auth_service.
  return (
    <InlineEditor
      title="Платформенные роли"
      icon={ShieldCheck}
      hint="фиксированный набор ролей платформы · назначение — отдельным интерфейсом per-user"
      items={PLATFORM_ROLES}
      getId={(r) => r.id}
      canEdit={false}
      readonlyNote="Набор фиксирован в auth_service · назначение/отзыв ролей — только account_admin через карточку пользователя"
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.id}</div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(r) => (
        <div className="card max-w-2xl">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-semibold flex items-center gap-2 mono">
              <ShieldCheck className="w-4 h-4 text-accent" /> {r.id}
            </h3>
          </div>
          <StatRow k="description" v={r.description} />
          <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim">
            Для назначения этой роли пользователю — открой карточку user → раздел «Платформенные роли».
          </div>
        </div>
      )}
    />
  );
}
