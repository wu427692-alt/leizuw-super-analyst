import { canPreloadRoutes } from './routePreload';
import { getRequestCacheGeneration } from '../api/requestCache';

type DataPreloader = {
  match: (pathname: string) => boolean;
  load: (isCurrent: () => boolean) => Promise<unknown>;
};

const DATA_PRELOADERS: DataPreloader[] = [
  // The task page owns its independent, current-user channel reads. Speculative
  // copies cannot reliably be reused by those APIs, so only warm its JS module.
  {
    match: (path) => path === '/app' || path === '/dashboard',
    load: async (isCurrent) => {
      const { homeDashboardApi } = await import('../api/homeDashboard');
      if (!isCurrent()) return;
      return homeDashboardApi.get(false, false, true);
    },
  },
  {
    match: (path) => path.startsWith('/research-center'),
    load: async (isCurrent) => {
      const { investmentMonitorApi } = await import('../api/investmentMonitor');
      if (!isCurrent()) return;
      return investmentMonitorApi.researchCenter();
    },
  },
  {
    match: (path) => path.startsWith('/industry-research'),
    load: async (isCurrent) => {
      const { industryResearchApi } = await import('../api/industryResearch');
      if (!isCurrent()) return;
      return industryResearchApi.projects({ limit: 12 });
    },
  },
  {
    match: (path) => path.startsWith('/concept-themes'),
    load: async (isCurrent) => {
      const { conceptThemesApi } = await import('../api/conceptThemes');
      if (!isCurrent()) return;
      return conceptThemesApi.overview({ pageSize: window.innerWidth <= 720 ? 10 : 24 });
    },
  },
  {
    match: (path) => path.startsWith('/essay-radar'),
    load: async (isCurrent) => {
      const { essayRadarApi } = await import('../api/essayRadar');
      if (!isCurrent()) return;
      // Status is shared across the radar pages; dashboard aggregates are not
      // consumed by the search page and should wait for their actual view.
      return essayRadarApi.status();
    },
  },
  {
    match: (path) => path.startsWith('/essay-quant'),
    load: async (isCurrent) => {
      const { essayQuantApi } = await import('../api/essayQuant');
      if (!isCurrent()) return;
      return Promise.allSettled([essayQuantApi.tasks(), essayQuantApi.runs(), essayQuantApi.catalog()]);
    },
  },
  {
    match: (path) => path === '/investment-monitor' || path.startsWith('/investment-monitor/'),
    load: async (isCurrent) => {
      const { investmentMonitorApi } = await import('../api/investmentMonitor');
      if (!isCurrent()) return;
      return investmentMonitorApi.status();
    },
  },
  {
    match: (path) => path.startsWith('/super-watchlist'),
    load: async (isCurrent) => {
      const [{ investmentMonitorApi }, { systemConfigApi }] = await Promise.all([
        import('../api/investmentMonitor'), import('../api/systemConfig'),
      ]);
      if (!isCurrent()) return;
      const symbols = await systemConfigApi.getWatchlist();
      if (!isCurrent()) return;
      if (!symbols[0]) return symbols;
      // A history GET may refill missing market data upstream. Hovering a link
      // is not a chart visit, so leave that request to the visible chart.
      return investmentMonitorApi.stockWorkspace(symbols[0], 183, false, 'overview');
    },
  },
  {
    match: (path) => path.startsWith('/data-acquisition'),
    load: async (isCurrent) => {
      const { dataAcquisitionApi } = await import('../api/dataAcquisition');
      if (!isCurrent()) return;
      return Promise.allSettled([
        dataAcquisitionApi.capabilities(),
        dataAcquisitionApi.jobs(),
        dataAcquisitionApi.researchReportStatus(),
        dataAcquisitionApi.researchReportFacets(),
      ]);
    },
  },
];

type WarmEntry = {
  startedAt: number;
  generation: number;
  pending: boolean;
  request: Promise<unknown>;
};

const warmed = new Map<DataPreloader, WarmEntry>();
const REUSE_WINDOW_MS = 15_000;

/**
 * Warm the first useful data for a route before the route mounts.
 *
 * The APIs handle their own caching and response freshness. This map only
 * prevents hover, focus and background warm-up from starting the same
 * batch repeatedly while the user moves across navigation items.
 */
export function preloadRouteData(pathname: string): Promise<unknown> {
  const preloader = DATA_PRELOADERS.find((entry) => entry.match(pathname));
  if (!preloader || document.visibilityState === 'hidden' || !canPreloadRoutes()) return Promise.resolve();

  const current = warmed.get(preloader);
  const generation = getRequestCacheGeneration();
  if (current?.generation === generation && (current.pending || Date.now() - current.startedAt <= REUSE_WINDOW_MS)) return current.request;

  const request = preloader.load(() => getRequestCacheGeneration() === generation).catch((error) => {
    if (warmed.get(preloader)?.request === request) warmed.delete(preloader);
    throw error;
  }).finally(() => {
    const entry = warmed.get(preloader);
    if (entry?.request === request) entry.pending = false;
  });
  warmed.set(preloader, { startedAt: Date.now(), generation, pending: true, request });
  return request;
}

export function resetRouteDataPreloadForTests(): void {
  warmed.clear();
}
