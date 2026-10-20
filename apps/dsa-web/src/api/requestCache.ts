type CacheEntry<T> = {
  value?: T;
  updatedAt: number;
  inflight?: Promise<T>;
};

const entries = new Map<string, CacheEntry<unknown>>();
let generation = 0;

type CachedQueryOptions = {
  freshMs: number;
  staleMs?: number;
  force?: boolean;
};

type CachedQueryContext = {
  /** Whether this request still belongs to the current cache/session. */
  isCurrent: () => boolean;
};

/**
 * Deduplicate identical aggregation requests and keep the last successful
 * result available across route unmounts. Once an entry is merely stale we
 * return it immediately and refresh the shared cache in the background; page
 * polling then adopts the refreshed value without blocking navigation.
 */
export async function cachedQuery<T>(
  key: string,
  query: (context: CachedQueryContext) => Promise<T>,
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

  // Register ownership before invoking the loader, including its synchronous
  // portion. Value-only invalidation keeps this identity; logout/force do not.
  const entry: CacheEntry<T> = { value: current?.value, updatedAt: current?.updatedAt ?? 0 };
  const isCurrent = () => entries.get(key) === entry;
  const fail = (error: unknown): never => {
    if (isCurrent()) {
      if (entry.value !== undefined) entries.set(key, { value: entry.value, updatedAt: entry.updatedAt });
      else entries.delete(key);
    }
    throw error;
  };
  entries.set(key, entry);
  let request: Promise<T>;
  try {
    request = query({ isCurrent }).then((value) => {
      // A late response cannot replace a new request or a cleared session.
      if (isCurrent()) entries.set(key, { value, updatedAt: Date.now() });
      return value;
    }).catch(fail);
  } catch (error) {
    return fail(error);
  }

  entry.inflight = request;
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

/** Invalidate one snapshot while retaining its pending request and deduplication. */
export function invalidateCachedQueryValue(key: string): void {
  const entry = entries.get(key);
  if (entry?.inflight) {
    entry.value = undefined;
    entry.updatedAt = 0;
  } else {
    entries.delete(key);
  }
}

/** Read an existing snapshot without fetching or extending its lifetime. */
export function peekCachedQuery<T>(key: string): T | undefined {
  return entries.get(key)?.value as T | undefined;
}

export function clearRequestCache(): void {
  generation++;
  entries.clear();
}

/** Retire multi-step preloads when authentication clears the private cache. */
export function getRequestCacheGeneration(): number {
  return generation;
}

export const resetRequestCacheForTests = clearRequestCache;
