/**
 * Тонкая обёртка над `testing_service` `GET /pool-overview` (§F плана
 * 2026-09-11) — реальные агрегаты очереди/исходов + живые статусы стендов.
 * Заменяет прежний синтетический `FleetDashboard`. Чтение доступно любому
 * аутентифицированному актору своего отдела.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/pool_overview.py`.
 */

import { apiGet } from "@/api/client";
import type { PoolOverviewContext, PoolOverviewResponse } from "@/api/testing/types";

const BASE = "/testing/v1";

export interface PoolOverviewQuery {
  context?: PoolOverviewContext;
  test_run_id?: string;
  /** Только для `context: "standalone"`. */
  created_from?: string;
  created_until?: string;
}

/** `GET /pool-overview` — пустой пул/очередь отдаёт нули, не 500/null. */
export function getPoolOverview(
  query: PoolOverviewQuery = {},
): Promise<PoolOverviewResponse> {
  return apiGet<PoolOverviewResponse>(`${BASE}/pool-overview`, { query: { ...query } });
}
