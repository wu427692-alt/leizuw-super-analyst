import { API_BASE_URL } from '../utils/constants';

type FetchLike = typeof fetch;

export type ServiceRecoveryOptions = {
  fetchImpl?: FetchLike;
  timeoutMs?: number;
  probeTimeoutMs?: number;
  intervalMs?: number;
};

const DEFAULT_RECOVERY_TIMEOUT_MS = 40_000;
const DEFAULT_PROBE_TIMEOUT_MS = 2_500;
const DEFAULT_INTERVAL_MS = 1_200;

let activeRecovery: Promise<boolean> | null = null;

function healthUrl() {
  const baseUrl = API_BASE_URL.replace(/\/+$/, '');
  return `${baseUrl}/api/health`;
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

async function probeService(fetchImpl: FetchLike, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(healthUrl(), {
      cache: 'no-store',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

async function runRecovery(options: ServiceRecoveryOptions): Promise<boolean> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== 'function') return false;

  const timeoutMs = Math.max(0, options.timeoutMs ?? DEFAULT_RECOVERY_TIMEOUT_MS);
  const probeTimeoutMs = Math.max(50, options.probeTimeoutMs ?? DEFAULT_PROBE_TIMEOUT_MS);
  const intervalMs = Math.max(0, options.intervalMs ?? DEFAULT_INTERVAL_MS);
  const deadline = Date.now() + timeoutMs;

  do {
    if (await probeService(fetchImpl, probeTimeoutMs)) return true;
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) return false;
    await delay(Math.min(intervalMs, remainingMs));
  } while (Date.now() <= deadline);

  return false;
}

/**
 * Coalesces simultaneous page failures into one health probe loop. This avoids a
 * retry storm when the API container is briefly being recreated and lets all
 * read-only requests resume as soon as the service is healthy again.
 */
export function waitForApiServiceRecovery(
  options: ServiceRecoveryOptions = {},
): Promise<boolean> {
  if (!activeRecovery) {
    activeRecovery = runRecovery(options).finally(() => {
      activeRecovery = null;
    });
  }
  return activeRecovery;
}

export function resetApiServiceRecoveryForTests() {
  activeRecovery = null;
}
