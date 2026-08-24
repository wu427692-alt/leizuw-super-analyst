import { useEffect, useRef } from 'react';

type PageActivationRefreshOptions = {
  enabled?: boolean;
  intervalMs?: number;
  minIntervalMs?: number;
  runOnMount?: boolean;
};

/**
 * Refresh a data surface when it becomes useful to the user again.
 *
 * Route mounts, browser-tab visibility, bfcache restores and window focus all
 * converge here. Requests are coalesced so Chrome's focus/visibility pair does
 * not create a burst, and failures stay with the page's existing quiet retry UI.
 */
export function usePageActivationRefresh(
  refresh: () => void | Promise<unknown>,
  {
    enabled = true,
    intervalMs = 0,
    minIntervalMs = 2_000,
    runOnMount = true,
  }: PageActivationRefreshOptions = {},
): void {
  const callbackRef = useRef(refresh);
  const runningRef = useRef(false);
  const lastRunRef = useRef(0);

  useEffect(() => { callbackRef.current = refresh; }, [refresh]);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;

    const trigger = (immediate = false) => {
      if (!active || document.visibilityState === 'hidden' || runningRef.current) return;
      const now = Date.now();
      if (!immediate && now - lastRunRef.current < Math.max(0, minIntervalMs)) return;
      lastRunRef.current = now;
      runningRef.current = true;
      Promise.resolve(callbackRef.current()).catch(() => undefined).finally(() => {
        runningRef.current = false;
      });
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') trigger(true);
    };
    const onFocus = () => trigger(false);
    const onPageShow = () => trigger(false);

    if (runOnMount) trigger(true);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('focus', onFocus);
    window.addEventListener('pageshow', onPageShow);
    const timer = intervalMs > 0
      ? window.setInterval(() => trigger(false), Math.max(1_000, intervalMs))
      : undefined;

    return () => {
      active = false;
      if (timer !== undefined) window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [enabled, intervalMs, minIntervalMs, runOnMount]);
}

export default usePageActivationRefresh;
