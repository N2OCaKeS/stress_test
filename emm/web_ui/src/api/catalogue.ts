/** Загрузить каталог для выбора по имени, включая последующие страницы. */
export async function listCatalogue<T extends { id: string }>(fetchPage: (query: { limit: number; offset: number }) => Promise<{ items: T[]; total?: number }>): Promise<T[]> {
  const items = new Map<string, T>();
  const limit = 500;
  for (let offset = 0; ; offset += limit) {
    const page = await fetchPage({ limit, offset });
    const previous = items.size;
    page.items.forEach((item) => items.set(item.id, item));
    if (page.items.length < limit || items.size === previous || (page.total !== undefined && offset + limit >= page.total)) break;
  }
  return Array.from(items.values());
}
