/**
 * Тонкая обёртка над `testing_service` `GET /pool-overview` (§F плана
 * 2026-09-11, доработка 2026-09-23) — реальные агрегаты очереди/исходов +
 * живые статусы стендов. Заменяет прежний синтетический `FleetDashboard`.
 * Чтение доступно любому аутентифицированному актору своего отдела.
 *
 * Без параметров: backend сам решает режим агрегации succeeded/failed
 * (`active_run`/`rolling_24h`, см. `PoolOverviewMode`) — раньше это был
 * переключатель на UI, теперь фронтенд просто отображает то, что пришло.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/pool_overview.py`.
 */

import { apiGet } from "@/api/client";
import type { PoolOverviewResponse } from "@/api/testing/types";

const BASE = "/testing/v1";

/** `GET /pool-overview` — пустой пул/очередь отдаёт нули, не 500/null. */
export function getPoolOverview(): Promise<PoolOverviewResponse> {
  return apiGet<PoolOverviewResponse>(`${BASE}/pool-overview`);
}
