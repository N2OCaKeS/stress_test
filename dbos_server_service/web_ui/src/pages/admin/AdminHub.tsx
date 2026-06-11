import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { listServices } from "@/api/auth/services";
import type { Service } from "@/api/auth/types";
import { AdminMiddle } from "./AdminMiddle";
import { buildAdminItems, itemById, visibleItems, type AdminItem } from "./adminCatalog";

type LookupState =
  | { kind: "none" }
  | { kind: "ok"; item: AdminItem }
  | { kind: "forbidden"; item: AdminItem }
  | { kind: "missing"; itemId: string };

/**
 * В mock-режиме `listServices()` не вызывается — backend не поднят.
 * Подаём фиксированный набор сервисов, чтобы дин. «Роли · …» пункты были
 * видны в скриншотах и e2e-снимках. Список должен совпадать с тем, что
 * `ServicesCatalog` рендерит в mock-режиме (минус auth_service — он
 * исключается в `buildAdminItems`).
 */
const MOCK_SERVICES: Service[] = [
  {
    service_name: "server_service",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    service_name: "secret_service",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    service_name: "loging_service",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    service_name: "worker_service",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
];

export function AdminHub() {
  const { persona } = usePersona();
  const { itemId } = useParams<{ itemId?: string }>();
  const mockMode = useMockMode();

  const servicesQ = useQuery<Service[]>(
    () => listServices(),
    [],
    { enabled: !mockMode },
  );

  // Если live `listServices` ещё loading или упал — каталог собирается без
  // дин. ролевых пунктов (static-only). Так UI не блокируется на сетевом
  // запросе и не падает, если backend временно недоступен.
  const allItems = useMemo(() => {
    const services: Service[] = mockMode
      ? MOCK_SERVICES
      : servicesQ.data ?? [];
    return buildAdminItems(services);
  }, [mockMode, servicesQ.data]);
  const items = useMemo(
    () => visibleItems(allItems, persona),
    [allItems, persona],
  );
  const lookup = useMemo<LookupState>(() => {
    if (!itemId) return { kind: "none" };
    const it = itemById(allItems, itemId);
    if (!it) return { kind: "missing", itemId };
    if (!it.visibleFor(persona)) return { kind: "forbidden", item: it };
    return { kind: "ok", item: it };
  }, [itemId, persona, allItems]);

  const active = lookup.kind === "ok" ? lookup.item : undefined;
  const Content = active?.content;

  const breadcrumb = active
    ? `Администрирование / ${active.label}`
    : lookup.kind === "forbidden"
      ? `Администрирование / ${lookup.item.label} (нет доступа)`
      : lookup.kind === "missing"
        ? `Администрирование / 404`
        : "Администрирование";

  // Services items take over the workzone fully (inline editor),
  // cluster items keep the centered card layout.
  const fullPane = active?.block === "services";

  return (
    <Shell breadcrumb={breadcrumb} middle={<AdminMiddle items={items} activeId={active?.id} />}>
      {lookup.kind === "none" && (
        <main className="flex-1 overflow-y-auto min-h-0">
          <div className="max-w-5xl mx-auto px-8 py-8">
            <section>
              <div className="text-3xl font-bold mb-1">Администрирование</div>
              <div className="text-dim mb-6">
                {items.length > 0
                  ? "Выберите раздел слева."
                  : "Нет доступных операций для текущей роли."}
              </div>
              <div className="empty-card">
                <ShieldCheck className="w-10 h-10 mx-auto text-dim mb-3" />
                <div className="text-sm text-dim">
                  Каждый раздел открывается в этой панели. Скролл работает
                  внутри блоков слева и внутри workzone независимо.
                </div>
              </div>
            </section>
          </div>
        </main>
      )}
      {lookup.kind === "forbidden" && (
        <main className="flex-1 overflow-y-auto min-h-0">
          <div className="max-w-3xl mx-auto px-8 py-8">
            <div className="empty-card danger">
              <AlertTriangle className="w-10 h-10 mx-auto text-warn mb-3" />
              <div className="font-semibold mb-1">Нет доступа к разделу</div>
              <div className="text-sm text-dim mb-4">
                У роли <b>{persona.username}</b> ({persona.platform_role ?? "service-роли"})
                нет прав на раздел <span className="mono">{lookup.item.id}</span> — «{lookup.item.label}».
              </div>
              {items.length > 0 ? (
                <div className="text-sm">
                  <div className="text-dim mb-2">Доступно сейчас:</div>
                  <ul className="flex flex-col gap-1">
                    {items.slice(0, 8).map((it) => (
                      <li key={it.id}>
                        <Link to={`/admin/${it.id}`} className="hover-bg mono text-xs">
                          {it.id} — {it.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="text-sm text-dim">Для этой роли нет ни одного раздела.</div>
              )}
            </div>
          </div>
        </main>
      )}
      {lookup.kind === "missing" && (
        <main className="flex-1 overflow-y-auto min-h-0">
          <div className="max-w-3xl mx-auto px-8 py-8">
            <div className="empty-card">
              <AlertTriangle className="w-10 h-10 mx-auto text-dim mb-3" />
              <div className="font-semibold mb-1">Раздел не найден</div>
              <div className="text-sm text-dim mb-4">
                Раздел <span className="mono">{lookup.itemId}</span> не существует
                в каталоге администрирования.
              </div>
              <Link to="/admin" className="btn">
                Вернуться к списку доступных
              </Link>
            </div>
          </div>
        </main>
      )}
      {active && Content && !fullPane && (
        <main className="flex-1 overflow-y-auto min-h-0">
          <div className="max-w-5xl mx-auto px-8 py-8">
            <section>
              <div className="mb-6">
                <div className="text-3xl font-bold mb-1">{active.label}</div>
                {active.hint && (
                  <div className="text-dim">{active.hint}</div>
                )}
              </div>
              <Content />
            </section>
          </div>
        </main>
      )}
      {active && Content && fullPane && (
        <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <Content />
        </main>
      )}
    </Shell>
  );
}
