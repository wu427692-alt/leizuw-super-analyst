const CHUNK_ERROR = /chunkloaderror|failed to fetch dynamically imported module|loading chunk|importing a module script failed/i;
const RETRY_PREFIX = 'dsa:chunk-retry:';
const RELOAD_PARAM = '__dsa_reload';

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return CHUNK_ERROR.test(message);
}

export function chunkRetryKey(routeKey: string, buildId: string): string {
  return `${RETRY_PREFIX}${routeKey}:${buildId}`;
}

export function cacheBustedPageUrl(href: string, token: string): string {
  const url = new URL(href, window.location.origin);
  url.searchParams.set(RELOAD_PARAM, token);
  return `${url.pathname}${url.search}${url.hash}`;
}

export function reloadWithFreshFrontend(token: string): void {
  window.location.replace(cacheBustedPageUrl(window.location.href, token));
}

export function clearChunkRetryMarkers(): void {
  try {
    for (let index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = window.sessionStorage.key(index);
      if (key?.startsWith(RETRY_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    // Storage is optional; cache-busted navigation still works without it.
  }
}
