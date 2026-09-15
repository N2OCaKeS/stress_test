import { listCatalogue } from "@/api/catalogue";
import { getTestStand, listTestStands, type ListTestStandsQuery } from "./testStands";
import type { TestStand } from "./types";

export function standName(stand?: TestStand): string {
  const server = stand?.server as { display_name?: string; hostname?: string } | undefined;
  return server?.display_name || server?.hostname || "Имя стенда недоступно";
}
export async function listNamedTestStands(query: ListTestStandsQuery = {}): Promise<TestStand[]> {
  const stands = await listCatalogue((page) => listTestStands({ ...query, ...page }));
  return Promise.all(stands.map(async (stand) => {
    if (stand.server) return stand;
    try { return { ...stand, ...await getTestStand(stand.id) }; }
    catch { return stand; }
  }));
}
