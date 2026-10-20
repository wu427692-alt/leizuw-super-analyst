import apiClient from './index';
import { toCamelCase } from './utils';
import type { AnnouncementCategoryList, AnnouncementSyncRequest, CloudKnowledgeStatus, DragonTigerDaily, DragonTigerHistory, DragonTigerSyncResult, EssayConsensusAnalysis, IntelligenceDashboard, InvestmentMonitorDashboard, MonitorEvent, MonitorEventList, MonitorStatus, MonitorSymbolDetail, ResearchCenterOverview, SourceBI, StockWorkspace, SuperWatchlistDashboard, WatchlistBackfillJob } from '../types/investmentMonitor';
import { cachedQuery, invalidateCachedQueries, invalidateCachedQueryValue, peekCachedQuery } from './requestCache';

const isNews = (event: MonitorEvent) => event.eventType === 'news' || event.eventType === 'long_news';
const newsSignature = (event: MonitorEvent) => JSON.stringify([
  event.sourceKey ?? '', event.externalId ?? '', event.eventType ?? '', event.eventAt ?? '',
  event.title ?? '', event.sourceName ?? '', event.url ?? '',
  Array.from(event.summary ?? '').slice(0, 240).join(''),
  (event.metrics?._evidence as { evidenceLevel?: string } | undefined)?.evidenceLevel ?? '',
]);

// Compare evidence, not quote timestamps or totals: a changing quote must not
// evict an otherwise usable, relatively expensive detail snapshot.
function overviewNewsMissing(overview: StockWorkspace | undefined, full: StockWorkspace | undefined) {
  if (!overview || (full && overview.stock.symbol !== full.stock.symbol)) return false;
  const preview = (overview.stock.overview?.sections.find(item => item.key === 'news')?.items
    ?? overview.stock.messages?.items ?? []).filter(isNews);
  const details = new Map((full?.stock.messages?.items ?? []).filter(isNews).map(item => [item.id, item]));
  return preview.some(item => Number.isSafeInteger(item.id) && item.id > 0
    && (!details.has(item.id) || newsSignature(item) !== newsSignature(details.get(item.id)!)));
}

export const investmentMonitorApi = {
  dashboard: async (days = 7): Promise<InvestmentMonitorDashboard> => {
    const response = await apiClient.get('/api/v1/investment-monitor/dashboard', { params: { days } });
    return toCamelCase<InvestmentMonitorDashboard>(response.data);
  },
  intelligenceDashboard: async (days = 14): Promise<IntelligenceDashboard> => {
    return cachedQuery(`monitor:intelligence:${days}`, async () => {
      const response = await apiClient.get('/api/v1/investment-monitor/intelligence-dashboard', { params: { days } });
      return toCamelCase<IntelligenceDashboard>(response.data);
    }, { freshMs: 8_000, staleMs: 60_000 });
  },
  sourceBI: async (days = 30): Promise<SourceBI> => {
    const response = await apiClient.get('/api/v1/investment-monitor/source-bi', { params: { days } });
    return toCamelCase<SourceBI>(response.data);
  },
  dragonTigerDaily: async (tradeDate?: string, refresh = false): Promise<DragonTigerDaily> => {
    const response = await apiClient.get('/api/v1/investment-monitor/dragon-tiger/daily', {
      params: { trade_date: tradeDate || undefined, refresh },
      timeout: 60000,
    });
    return toCamelCase<DragonTigerDaily>(response.data);
  },
  dragonTigerHistory: async (params: {
    startDate: string; endDate: string; symbol?: string; query?: string; page?: number; pageSize?: number;
  }): Promise<DragonTigerHistory> => {
    const response = await apiClient.get('/api/v1/investment-monitor/dragon-tiger/history', { params: {
      start_date: params.startDate, end_date: params.endDate,
      symbol: params.symbol || undefined, query: params.query || undefined,
      page: params.page ?? 1, page_size: params.pageSize ?? 50,
    } });
    return toCamelCase<DragonTigerHistory>(response.data);
  },
  syncDragonTiger: async (startDate: string, endDate: string): Promise<DragonTigerSyncResult> => {
    const response = await apiClient.post('/api/v1/investment-monitor/dragon-tiger/sync', {
      start_date: startDate, end_date: endDate,
    }, { timeout: 120000 });
    return toCamelCase<DragonTigerSyncResult>(response.data);
  },
  symbol: async (symbol: string, days = 30): Promise<MonitorSymbolDetail> => {
    const response = await apiClient.get(`/api/v1/investment-monitor/symbols/${encodeURIComponent(symbol)}`, { params: { days } });
    return toCamelCase<MonitorSymbolDetail>(response.data);
  },
  superWatchlist: async (days = 365, force = false): Promise<SuperWatchlistDashboard> => {
    return cachedQuery(`monitor:super-watchlist:${days}`, async () => {
      const response = await apiClient.get('/api/v1/investment-monitor/super-watchlist', { params: { days } });
      return toCamelCase<SuperWatchlistDashboard>(response.data);
    }, { freshMs: 8_000, staleMs: 120_000, force });
  },
  stockWorkspace: async (symbol: string, days = 365, refresh = false, view: 'overview' | 'full' = 'full'): Promise<StockWorkspace> => {
    const prefix = `monitor:stock-workspace:${symbol}:${days}:`;
    const key = `${prefix}${view}`;
    const overviewKey = `${prefix}overview`;
    const fullKey = `${prefix}full`;
    const result = await cachedQuery(key, async ({ isCurrent }) => {
      const fetchWorkspace = async (force: boolean) => {
        const response = await apiClient.get(`/api/v1/investment-monitor/stock-workspace/${encodeURIComponent(symbol)}`, {
          params: { days, refresh: force, view },
        });
        return toCamelCase<StockWorkspace>(response.data);
      };
      const overviewBefore = peekCachedQuery<StockWorkspace>(overviewKey);
      let value = await fetchWorkspace(refresh);
      const overviewAfter = peekCachedQuery<StockWorkspace>(overviewKey);
      if (isCurrent() && view === 'full' && (value.cache?.state === 'superseded'
        || (overviewAfter !== overviewBefore && overviewNewsMissing(overviewAfter, value)))) {
        // An older in-flight detail request must not erase newly visible news.
        // Retry once from local data only; never start a collector or AI task.
        value = await fetchWorkspace(true);
        const latest = peekCachedQuery<StockWorkspace>(overviewKey);
        if (value.cache?.state === 'superseded' || (latest !== overviewAfter && overviewNewsMissing(latest, value))) {
          throw new Error('研究资料正在更新，请稍后再试；已显示内容仍保留。');
        }
      }
      return value;
    // The page retains its last successful stock while refreshing. Return the
    // actual new response here: an unobserved SWR cache update can otherwise
    // leave the visible evidence stale until the next trading-session poll.
    }, { freshMs: 30_000, staleMs: 0, force: refresh });
    // Only the current cache owner may invalidate a sibling. A late response
    // after logout or a newer request must not change the replacement cache.
    if (view === 'overview' && peekCachedQuery(key) === result) {
      const full = peekCachedQuery<StockWorkspace>(fullKey);
      const overviewTime = Date.parse(result.generatedAt);
      const fullTime = Date.parse(full?.generatedAt ?? '');
      const olderOverview = Number.isFinite(overviewTime) && Number.isFinite(fullTime) && overviewTime < fullTime;
      if (refresh || (!olderOverview && overviewNewsMissing(result, full))) invalidateCachedQueryValue(fullKey);
    }
    return result;
  },
  researchCenter: async (): Promise<ResearchCenterOverview> => {
    return cachedQuery('monitor:research-center', async () => {
      const response = await apiClient.get('/api/v1/investment-monitor/research-center', { timeout: 45000 });
      return toCamelCase<ResearchCenterOverview>(response.data);
    }, { freshMs: 15_000, staleMs: 300_000 });
  },
  refreshSuperWatchlist: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/super-watchlist/refresh', undefined, { timeout: 60000 });
    invalidateCachedQueries('monitor:super-watchlist:');
    invalidateCachedQueries('monitor:stock-workspace:');
    return toCamelCase(response.data);
  },
  backfillWatchlist: async (symbol: string): Promise<WatchlistBackfillJob> => {
    const response = await apiClient.post(`/api/v1/investment-monitor/super-watchlist/${encodeURIComponent(symbol)}/backfill`);
    return toCamelCase<WatchlistBackfillJob>(response.data);
  },
  essayConsensus: async (symbol: string): Promise<{ symbol: string; name: string; consensus: EssayConsensusAnalysis }> => {
    const response = await apiClient.get(`/api/v1/investment-monitor/super-watchlist/${encodeURIComponent(symbol)}/essay-consensus`);
    return toCamelCase(response.data);
  },
  analyzeEssayConsensus: async (symbol: string): Promise<{ symbol: string; name: string; consensus: EssayConsensusAnalysis }> => {
    const response = await apiClient.post(`/api/v1/investment-monitor/super-watchlist/${encodeURIComponent(symbol)}/essay-consensus/analyze`);
    return toCamelCase(response.data);
  },
  status: async (): Promise<MonitorStatus> => {
    return cachedQuery('monitor:status', async () => {
      const response = await apiClient.get('/api/v1/investment-monitor/status');
      return toCamelCase<MonitorStatus>(response.data);
    }, { freshMs: 3_000, staleMs: 20_000 });
  },
  events: async (params: { days?: number; perspective?: string; query?: string; symbol?: string; sourceKey?: string; channel?: string; evidenceLevel?: string; pageSize?: number }): Promise<MonitorEventList> => {
    const cacheKey = `monitor:events:${JSON.stringify(params)}`;
    return cachedQuery(cacheKey, async () => {
      const response = await apiClient.get('/api/v1/investment-monitor/events', { params: {
        days: params.days, perspective: params.perspective, query: params.query, symbol: params.symbol,
        source_key: params.sourceKey, channel: params.channel, evidence_level: params.evidenceLevel,
        page_size: params.pageSize,
      } });
      return toCamelCase<MonitorEventList>(response.data);
    }, { freshMs: 3_000, staleMs: 20_000 });
  },
  event: async (eventId: number): Promise<MonitorEvent> => {
    const response = await apiClient.get(`/api/v1/investment-monitor/events/${eventId}`);
    return toCamelCase<MonitorEvent>(response.data);
  },
  sync: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/sync', { categories: null });
    invalidateCachedQueries('monitor:');
    return toCamelCase(response.data);
  },
  syncSource: async (sourceKey: string): Promise<unknown> => {
    const response = await apiClient.post(`/api/v1/investment-monitor/sources/${encodeURIComponent(sourceKey)}/sync`);
    invalidateCachedQueries('monitor:');
    return toCamelCase(response.data);
  },
  startWorker: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/worker/start');
    return toCamelCase(response.data);
  },
  stopWorker: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/worker/stop');
    return toCamelCase(response.data);
  },
  cloudStatus: async (): Promise<CloudKnowledgeStatus> => {
    const response = await apiClient.get('/api/v1/investment-monitor/cloud/status');
    return toCamelCase<CloudKnowledgeStatus>(response.data);
  },
  createCloudSnapshot: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/cloud/snapshot');
    return toCamelCase(response.data);
  },
  announcementCategories: async (): Promise<AnnouncementCategoryList> => {
    const response = await apiClient.get('/api/v1/investment-monitor/announcements/categories');
    return toCamelCase<AnnouncementCategoryList>(response.data);
  },
  announcements: async (params: { days?: number; startDate?: string; endDate?: string; symbol?: string; category?: string; query?: string }): Promise<MonitorEventList> => {
    const normalized = { days: params.days, start_date: params.startDate, end_date: params.endDate, symbol: params.symbol, category: params.category, query: params.query };
    const response = await apiClient.get('/api/v1/investment-monitor/announcements', { params: normalized });
    return toCamelCase<MonitorEventList>(response.data);
  },
  syncAnnouncements: async (request: AnnouncementSyncRequest): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/announcements/sync', {
      start_date: request.startDate, end_date: request.endDate, symbols: request.symbols ?? [],
      categories: request.categories ?? [], keyword: request.keyword ?? '', max_pages: request.maxPages ?? 20,
    });
    return toCamelCase(response.data);
  },
  exportAnnouncements: async (params: { startDate: string; endDate: string; symbol?: string; category?: string; query?: string }): Promise<Blob> => {
    const response = await apiClient.get('/api/v1/investment-monitor/announcements/export', {
      params: { start_date: params.startDate, end_date: params.endDate, symbol: params.symbol, category: params.category, query: params.query },
      responseType: 'blob',
    });
    return response.data as Blob;
  },
  packageAnnouncements: async (eventIds: number[], includeText = true): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/investment-monitor/announcements/package', {
      event_ids: eventIds, include_text: includeText,
    }, { responseType: 'blob' });
    return response.data as Blob;
  },
};
