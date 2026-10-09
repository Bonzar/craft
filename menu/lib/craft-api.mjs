// Доступ к connect-API Craft: список коллекций, чтение и запись элементов.
// Базовый URL с токеном приходит из env CRAFT_API_BASE и в логи не попадает.

const JSON_HEADERS = { "content-type": "application/json" };

export function createClient({ base, fetchImpl = fetch } = {}) {
  if (!base) throw new Error("нет базового URL connect-API (CRAFT_API_BASE)");
  const root = base.replace(/\/+$/, "");

  async function request(method, path, body) {
    const res = await fetchImpl(root + path, {
      method,
      headers: body === undefined ? undefined : JSON_HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`${method} ${path}: HTTP ${res.status} ${text.slice(0, 200)}`);
    try {
      return JSON.parse(text);
    } catch {
      throw new Error(`${method} ${path}: ответ не JSON: ${text.slice(0, 200)}`);
    }
  }

  return {
    listCollections: async () => (await request("GET", "/collections")).items ?? [],
    getItems: async (id) => (await request("GET", `/collections/${id}/items`)).items ?? [],
    updateItems: async (id, itemsToUpdate) =>
      itemsToUpdate.length === 0
        ? { items: [] }
        : request("PUT", `/collections/${id}/items`, { itemsToUpdate }),
    updateBlocks: async (blocks) =>
      blocks.length === 0 ? { items: [] } : request("PUT", "/blocks", { blocks }),
  };
}
