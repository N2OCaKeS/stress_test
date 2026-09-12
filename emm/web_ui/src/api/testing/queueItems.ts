import { apiGet, apiPost } from "@/api/client";
import type { TestingPaginatedResponse } from "./types";
export { getCurrentQueueItem, findActiveQueueItemForServer } from "@/api/testing/testStands";

export interface PublicQueueItem {
  id: string; test_id: string; stand_id: string; test_run_id: string | null;
  retry_of_id: string | null; debug_mode: boolean; state: string;
  rc: string | null; kernel: string | null; mode: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; error: string | null;
}
export interface QueueLaunchRequest {
  request_id: string; test_id: string; stand_id: string; os_version_id: string;
  kernel: string; mode: "orel" | "smolensk"; debug_mode: boolean;
}
export function listQueueItems(kind: "standalone" | "campaign" | "all" = "standalone") {
  return apiGet<TestingPaginatedResponse<PublicQueueItem>>(`/testing/v1/queue-items?kind=${kind}&limit=500`);
}
export function launchQueueItem(body: QueueLaunchRequest) {
  return apiPost<PublicQueueItem>("/testing/v1/queue-items", body);
}
export function retryQueueItem(id: string, requestId: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/queue-items/${encodeURIComponent(id)}/retry`, { request_id: requestId });
}
