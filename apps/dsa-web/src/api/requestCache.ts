type CacheEntry<T> = {
  value?: T;
  updatedAt: number;
  inflight?: Promise<T>;
};

const entries = new Map<string, CacheEntry<unknown>>();

type CachedQueryOptions = {
  freshMs: number;
  staleMs?: number;
  force?: boolean;
};

/**
 * Deduplicate identical aggregation requests and keep the last successful
 * result available across route unmounts. Once an entry is merely stale we
 * return it immediately and refresh the shared cache in the background; page
 * polling then adopts the refreshed value without blocking navigation.
 */
export async function cachedQuery<T>(
  key: string,
  query: () => Promise<T>,
  { freshMs, staleMs = freshMs, force = false }: CachedQueryOptions,
): Promise<T> {
  const now = Date.now();
  const current = entries.get(key) as CacheEntry<T> | undefined;
  const age = current?.value === undefined ? Number.POSITIVE_INFINITY : now - current.updatedAt;

  if (!force && current?.value !== undefined && age <= freshMs) return current.value;
  if (!force && current?.inflight) {
    if (current.value !== undefined && age <= staleMs) return current.value;
    return current.inflight;
  }

  const request = query().then((value) => {
    entries.set(key, { value, updatedAt: Date.now() });
    return value;
  }).catch((error) => {
    const latest = entries.get(key) as CacheEntry<T> | undefined;
    if (latest?.inflight === request) {
      if (latest.value !== undefined) entries.set(key, { value: latest.value, updatedAt: latest.updatedAt });
      else entries.delete(key);
    }
    throw error;
  });

  entries.set(key, { value: current?.value, updatedAt: current?.updatedAt ?? 0, inflight: request });
  if (!force && current?.value !== undefined && age <= staleMs) {
    void request.catch(() => undefined);
    return current.value;
  }
  return request;
}

export function invalidateCachedQueries(prefix: string): void {
  for (const key of entries.keys()) {
    if (key.startsWith(prefix)) entries.delete(key);
  }
}

export function clearRequestCache(): void {
  entries.clear();
}

export const resetRequestCacheForTests = clearRequestCache;
