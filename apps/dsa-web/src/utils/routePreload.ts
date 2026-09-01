import type React from 'react';

type RouteModule = Promise<{ default: React.ComponentType }>;

type NetworkInformationLike = {
  effectiveType?: string;
  saveData?: boolean;
};

const ROUTE_LOADERS: Array<{ match: (pathname: string) => boolean; load: () => RouteModule }> = [
  { match: (path) => path === '/app' || path === '/dashboard', load: () => import('../pages/MarketDashboardPage') },
  { match: (path) => path.startsWith('/industry-research'), load: () => import('../pages/IndustryResearchPage') },
  { match: (path) => path.startsWith('/concept-themes'), load: () => import('../pages/ConceptThemesPage') },
  { match: (path) => path.startsWith('/tasks'), load: () => import('../pages/TasksHubPage') },
  { match: (path) => path.startsWith('/chat'), load: () => import('../pages/ChatPage') },
  { match: (path) => path.startsWith('/essay-radar'), load: () => import('../pages/EssayRadarPage') },
  { match: (path) => path.startsWith('/essay-quant'), load: () => import('../pages/EssayQuantPage') },
  { match: (path) => path.startsWith('/investment-monitor/feed'), load: () => import('../pages/InvestmentMonitorPage') },
  { match: (path) => path.startsWith('/investment-monitor/dragon-tiger'), load: () => import('../pages/DragonTigerPage') },
  { match: (path) => path.startsWith('/super-watchlist'), load: () => import('../pages/SuperWatchlistPage') },
  { match: (path) => path.startsWith('/data-acquisition'), load: () => import('../pages/DataAcquisitionPage') },
  { match: (path) => path === '/guide', load: () => import('../pages/UserGuidePage') },
  { match: (path) => path.startsWith('/screening'), load: () => import('../pages/StockScreeningPage') },
  { match: (path) => path.startsWith('/settings'), load: () => import('../pages/SettingsPage') },
  { match: (path) => path === '/admin' || path.startsWith('/admin/'), load: () => import('../pages/AdminConsolePage') },
];

const inflight = new Map<() => RouteModule, Promise<unknown>>();

export function canPreloadRoutes(): boolean {
  const connection = (navigator as Navigator & { connection?: NetworkInformationLike }).connection;
  return !connection?.saveData && connection?.effectiveType !== 'slow-2g' && connection?.effectiveType !== '2g';
}

/**
 * Start downloading a route chunk before navigation. Dynamic imports are
 * cached by the browser, while this map also coalesces repeated hover/focus
 * events. A failed speculative request is removed so lazyRoute can retry it.
 */
export function preloadRoute(pathname: string): Promise<unknown> {
  if (!canPreloadRoutes()) return Promise.resolve();
  const loader = ROUTE_LOADERS.find((entry) => entry.match(pathname))?.load;
  if (!loader) return Promise.resolve();
  const existing = inflight.get(loader);
  if (existing) return existing;
  const request = loader().catch((error) => {
    inflight.delete(loader);
    throw error;
  });
  inflight.set(loader, request);
  return request;
}

export function resetRoutePreloadCacheForTests(): void {
  inflight.clear();
}
