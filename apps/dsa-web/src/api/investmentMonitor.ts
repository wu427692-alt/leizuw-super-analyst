import apiClient from './index';
import { toCamelCase } from './utils';
import type { AnnouncementCategoryList, AnnouncementSyncRequest, CloudKnowledgeStatus, DragonTigerDaily, DragonTigerHistory, DragonTigerSyncResult, EssayConsensusAnalysis, IntelligenceDashboard, InvestmentMonitorDashboard, MonitorEvent, MonitorEventList, MonitorStatus, MonitorSymbolDetail, ResearchCenterOverview, SourceBI, StockWorkspace, SuperWatchlistDashboard, WatchlistBackfillJob } from '../types/investmentMonitor';

export const investmentMonitorApi = {
  dashboard: async (days = 7): Promise<InvestmentMonitorDashboard> => {
    const response = await apiClient.get('/api/v1/investment-monitor/dashboard', { params: { days } });
    return toCamelCase<InvestmentMonitorDashboard>(response.data);
  },
  intelligenceDashboard: async (days = 14): Promise<IntelligenceDashboard> => {
    const response = await apiClient.get('/api/v1/investment-monitor/intelligence-dashboard', { params: { days } });
    return toCamelCase<IntelligenceDashboard>(response.data);
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
  superWatchlist: async (days = 365): Promise<SuperWatchlistDashboard> => {
    const response = await apiClient.get('/api/v1/investment-monitor/super-watchlist', { params: { days } });
    return toCamelCase<SuperWatchlistDashboard>(response.data);
  },
  stockWorkspace: async (symbol: string, days = 365, refresh = false): Promise<StockWorkspace> => {
    const response = await apiClient.get(`/api/v1/investment-monitor/stock-workspace/${encodeURIComponent(symbol)}`, {
      params: { days, refresh },
    });
    return toCamelCase<StockWorkspace>(response.data);
  },
  researchCenter: async (): Promise<ResearchCenterOverview> => {
    const response = await apiClient.get('/api/v1/investment-monitor/research-center', { timeout: 45000 });
    return toCamelCase<ResearchCenterOverview>(response.data);
  },
  refreshSuperWatchlist: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/super-watchlist/refresh', undefined, { timeout: 60000 });
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
    const response = await apiClient.get('/api/v1/investment-monitor/status');
    return toCamelCase<MonitorStatus>(response.data);
  },
  events: async (params: { days?: number; perspective?: string; query?: string; symbol?: string; sourceKey?: string; channel?: string; evidenceLevel?: string; pageSize?: number }): Promise<MonitorEventList> => {
    const response = await apiClient.get('/api/v1/investment-monitor/events', { params: {
      days: params.days, perspective: params.perspective, query: params.query, symbol: params.symbol,
      source_key: params.sourceKey, channel: params.channel, evidence_level: params.evidenceLevel,
      page_size: params.pageSize,
    } });
    return toCamelCase<MonitorEventList>(response.data);
  },
  event: async (eventId: number): Promise<MonitorEvent> => {
    const response = await apiClient.get(`/api/v1/investment-monitor/events/${eventId}`);
    return toCamelCase<MonitorEvent>(response.data);
  },
  sync: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/investment-monitor/sync', { categories: null });
    return toCamelCase(response.data);
  },
  syncSource: async (sourceKey: string): Promise<unknown> => {
    const response = await apiClient.post(`/api/v1/investment-monitor/sources/${encodeURIComponent(sourceKey)}/sync`);
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
