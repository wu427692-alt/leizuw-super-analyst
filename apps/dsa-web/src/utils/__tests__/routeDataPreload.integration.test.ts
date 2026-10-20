import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../../api';
import { conceptThemesApi } from '../../api/conceptThemes';
import { industryResearchApi } from '../../api/industryResearch';
import { clearRequestCache } from '../../api/requestCache';
import { preloadRouteData, resetRouteDataPreloadForTests } from '../routeDataPreload';

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

describe('route preload reuses the mounted page request', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearRequestCache();
    resetRouteDataPreloadForTests();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.stubGlobal('navigator', { connection: { effectiveType: '4g', saveData: false } });
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0 } });
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it.each([{ width: 1440, pageSize: 24 }, { width: 390, pageSize: 10 }])(
    'loads the concept catalog once at width $width, including explicit page defaults', async ({ width, pageSize }) => {
      vi.stubGlobal('innerWidth', width);
      const warmed = await preloadRouteData('/concept-themes');
      const mounted = await conceptThemesApi.overview({
        query: '', themeType: '', source: '', family: '', cluster: '', minSources: 1,
        readiness: 'all', sortBy: 'heat', view: 'canonical', page: 1, pageSize,
      });
      expect(apiClient.get).toHaveBeenCalledTimes(1);
      expect(mounted).toBe(warmed);
      expect(apiClient.get).toHaveBeenCalledWith('/api/v1/concept-themes/overview', expect.objectContaining({
        params: expect.objectContaining({ page_size: pageSize }),
      }));
    },
  );

  it('loads the twelve-row research task page once', async () => {
    const warmed = await preloadRouteData('/industry-research');
    const mounted = await industryResearchApi.projects({ limit: 12, offset: 0, query: '', status: 'all', force: false });
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(mounted).toBe(warmed);
  });

  it('keeps page, title and viewport queries separate while sharing equivalent defaults', async () => {
    const first = await conceptThemesApi.overview({ pageSize: 24, query: '光模块' });
    const reordered = await conceptThemesApi.overview({ page: 1, query: '光模块', pageSize: 24, view: 'canonical' });
    expect(reordered).toBe(first);
    await conceptThemesApi.overview({ query: '光模块', pageSize: 24, page: 2 });
    await conceptThemesApi.overview({ query: '光模块', pageSize: 10 });
    await conceptThemesApi.overview({ query: '机器人', pageSize: 24 });
    expect(apiClient.get).toHaveBeenCalledTimes(4);
  });

  it('still fetches fresh concept data after its original twenty-second lifetime', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('innerWidth', 1440);
    await preloadRouteData('/concept-themes');
    await vi.advanceTimersByTimeAsync(20_001);
    await conceptThemesApi.overview({ pageSize: 24 });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });
});
