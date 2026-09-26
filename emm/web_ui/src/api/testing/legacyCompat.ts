/**
 * Настройки публичного compat `/rest/api/*`: подсети, из которых
 * скрипты на стендах ходят к легаси-путям без авторизации, и отдел по
 * умолчанию для `get-jira-url`/`get-confluence-url`.
 *
 * Все вызовы — под матрицей `(legacy_compat, view|update)` (сид — системная
 * роль `admin` testing_service).
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/legacy_compat.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/api/client";
import type {
  CompatNetwork,
  CompatNetworkCreateRequest,
  CompatNetworkUpdateRequest,
  CompatResolveResult,
  LegacyCompatSettings,
} from "@/api/testing/types";

const BASE = "/testing/v1/legacy-compat";

export function getLegacyCompatSettings(): Promise<LegacyCompatSettings> {
  return apiGet<LegacyCompatSettings>(`${BASE}/settings`);
}

export function updateLegacyCompatSettings(body: {
  default_department_id: string | null;
}): Promise<LegacyCompatSettings> {
  return apiPut<LegacyCompatSettings>(`${BASE}/settings`, body);
}

export async function listCompatNetworks(): Promise<CompatNetwork[]> {
  const body = await apiGet<{ items: CompatNetwork[] }>(`${BASE}/networks`);
  return body.items ?? [];
}

/** Биты хоста в адресе — 422, дубль — 409 `COMPAT_NETWORK_DUPLICATE`. */
export function createCompatNetwork(body: CompatNetworkCreateRequest): Promise<CompatNetwork> {
  return apiPost<CompatNetwork>(`${BASE}/networks`, body);
}

export function updateCompatNetwork(
  id: string,
  body: CompatNetworkUpdateRequest,
): Promise<CompatNetwork> {
  return apiPatch<CompatNetwork>(`${BASE}/networks/${encodeURIComponent(id)}`, body);
}

export function deleteCompatNetwork(id: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`${BASE}/networks/${encodeURIComponent(id)}`);
}

/** Что compat сделает с запросом с этого IP: пустит ли и какой отдел выберет. */
export function resolveCompatIp(ip: string): Promise<CompatResolveResult> {
  return apiGet<CompatResolveResult>(`${BASE}/resolve`, { query: { ip } });
}
