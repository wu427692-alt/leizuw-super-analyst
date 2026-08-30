type DataPreloader = {
  match: (pathname: string) => boolean;
  load: () => Promise<unknown>;
};

const DATA_PRELOADERS: DataPreloader[] = [
  {
    match: (path) => path === '/app' || path === '/dashboard',
    load: async () => {
      const { homeDashboardApi } = await import('../api/homeDashboard');
      return homeDashboardApi.get(false, false, true);
    },
  },
  {
    match: (path) => path.startsWith('/research-center'),
    load: async () => {
      const { investmentMonitorApi } = await import('../api/investmentMonitor');
      return investmentMonitorApi.researchCenter();
    },
  },
  {
    match: (path) => path.startsWith('/industry-research'),
    load: async () => {
      const { industryResearchApi } = await import('../api/industryResearch');
      return industryResearchApi.projects();
    },
  },
  {
    match: (path) => path.startsWith('/essay-radar'),
    load: async () => {
      const { essayRadarApi } = await import('../api/essayRadar');
      return Promise.allSettled([essayRadarApi.status(), essayRadarApi.dashboard()]);
    },
  },
  {
    match: (path) => path.startsWith('/essay-quant'),
    load: async () => {
      const { essayQuantApi } = await import('../api/essayQuant');
      return Promise.allSettled([essayQuantApi.tasks(), essayQuantApi.runs(), essayQuantApi.catalog()]);
    },
  },
  {
    match: (path) => path === '/investment-monitor' || path.startsWith('/investment-monitor/'),
    load: async () => {
      const { investmentMonitorApi } = await import('../api/investmentMonitor');
      return investmentMonitorApi.status();
    },
  },
  {
    match: (path) => path.startsWith('/super-watchlist'),
    load: async () => {
      const { investmentMonitorApi } = await import('../api/investmentMonitor');
      return investmentMonitorApi.superWatchlist(183);
    },
  },
  {
    match: (path) => path.startsWith('/data-acquisition'),
    load: async () => {
      const { dataAcquisitionApi } = await import('../api/dataAcquisition');
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
  request: Promise<unknown>;
};

const warmed = new Map<DataPreloader, WarmEntry>();
const REUSE_WINDOW_MS = 15_000;

/**
 * Warm the first useful data for a route before the route mounts.
 *
 * Every API used here already has stale-while-revalidate semantics. This map
 * only prevents hover, focus and background warm-up from starting the same
 * batch repeatedly while the user moves across navigation items.
 */
export function preloadRouteData(pathname: string): Promise<unknown> {
  const preloader = DATA_PRELOADERS.find((entry) => entry.match(pathname));
  if (!preloader || document.visibilityState === 'hidden') return Promise.resolve();

  const current = warmed.get(preloader);
  if (current && Date.now() - current.startedAt <= REUSE_WINDOW_MS) return current.request;

  const request = preloader.load().catch((error) => {
    if (warmed.get(preloader)?.request === request) warmed.delete(preloader);
    throw error;
  });
  warmed.set(preloader, { startedAt: Date.now(), request });
  return request;
}

export function resetRouteDataPreloadForTests(): void {
  warmed.clear();
}
