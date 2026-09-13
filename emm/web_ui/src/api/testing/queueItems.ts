import { apiGet, apiPost } from "@/api/client";
import type { TestingPaginatedResponse } from "./types";
export { getCurrentQueueItem, findActiveQueueItemForServer } from "@/api/testing/testStands";

export interface PublicQueueItem {
  log_status?: "available" | "rotated" | "pending" | "missing";
  test_code?: string | null; test_name?: string | null; is_current?: boolean;
  id: string; test_id: string; stand_id: string; test_run_id: string | null;
  retry_of_id: string | null; debug_mode: boolean; state: string;
  rc: string | null; kernel: string | null; mode: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; error: string | null;
}
export interface QueueLaunchRequest {
  request_id: string; test_id: string; stand_id: string; os_version_id: string;
  kernel: string; mode: "orel" | "smolensk"; debug_mode: boolean;
}
export interface QueueItemsQuery {
  kind?: "standalone" | "campaign" | "all";
  os_version_id?: string; kernel?: string;
  test_run_id?: string; stand_id?: string; test_id?: string;
  attempt_id?: string; retry_of_id?: string;
  created_from?: string; created_until?: string;
  states?: string[]; debug_mode?: boolean; q?: string;
  order?: "asc" | "desc"; limit?: number; offset?: number;
}
export function listQueueItems(query: QueueItemsQuery = {}) {
  const params = new URLSearchParams({ kind: "standalone", limit: "50" });
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else params.set(key, String(value));
  }
  return apiGet<TestingPaginatedResponse<PublicQueueItem>>(`/testing/v1/queue-items?${params}`);
}
export function launchQueueItem(body: QueueLaunchRequest) {
  return apiPost<PublicQueueItem>("/testing/v1/queue-items", body);
}
export function retryQueueItem(id: string, requestId: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/queue-items/${encodeURIComponent(id)}/retry`, { request_id: requestId });
}
