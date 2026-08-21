import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayAnalysis, EssayAnalysisList, EssayCountBackfillResponse, EssayDailyReportList, EssayDashboard, EssayDeepInsights, EssayHistoricalBacklog, EssayInsights, EssayStatus, EssayWordCloud, EssayWorkerStatus, ResearchNoteDetail } from '../types/essayRadar';

export type EssayFilters = {
  days?: number;
  query?: string;
  sentiment?: string;
  category?: string;
  tag?: string;
  stock?: string;
  minImportance?: number;
  analysisStatus?: string;
  knownTotal?: number;
  page?: number;
  pageSize?: number;
};

export const essayRadarApi = {
  status: async (days = 30): Promise<EssayStatus> => {
    const response = await apiClient.get('/api/v1/essay-radar/status', { params: { days } });
    return toCamelCase<EssayStatus>(response.data);
  },
  dashboard: async (days = 30): Promise<EssayDashboard> => {
    const response = await apiClient.get('/api/v1/essay-radar/dashboard', { params: { days } });
    return toCamelCase<EssayDashboard>(response.data);
  },
  insights: async (days = 30, trendDays = 14): Promise<EssayInsights> => {
    const response = await apiClient.get('/api/v1/essay-radar/insights', { params: { days, trend_days: trendDays } });
    return toCamelCase<EssayInsights>(response.data);
  },
  deepInsights: async (params: {
    days?: number; trendDays?: number; horizon?: 'short' | 'medium' | 'long' | 'custom';
    startDate?: string; endDate?: string;
  } = {}): Promise<EssayDeepInsights> => {
    const response = await apiClient.get('/api/v1/essay-radar/deep-insights', { params: {
      days: params.days ?? 30,
      trend_days: params.trendDays ?? 14,
      horizon: params.horizon,
      start_date: params.startDate,
      end_date: params.endDate,
    } });
    return toCamelCase<EssayDeepInsights>(response.data);
  },
  interpretMarketImpact: async (payload: {
    tsCode: string; horizon: 'short' | 'medium' | 'long' | 'custom'; startDate?: string; endDate?: string;
  }): Promise<{
    generatedAt: string; model: string; tsCode: string;
    interpretation: { conclusion: string; evidence: string[]; limitations: string[]; nextChecks: string[] };
  }> => {
    const response = await apiClient.post('/api/v1/essay-radar/deep-insights/market-interpretation', {
      ts_code: payload.tsCode,
      horizon: payload.horizon,
      start_date: payload.startDate,
      end_date: payload.endDate,
    }, { timeout: 120000 });
    return toCamelCase(response.data);
  },
  wordCloud: async (period: 'day' | 'week' | 'month', kind: 'stocks' | 'tags' | 'themes' = 'stocks'): Promise<EssayWordCloud> => {
    const response = await apiClient.get('/api/v1/essay-radar/word-cloud', { params: { period, kind } });
    return toCamelCase<EssayWordCloud>(response.data);
  },
  dailyReports: async (): Promise<EssayDailyReportList> => {
    const response = await apiClient.get('/api/v1/essay-radar/daily-reports', { params: { limit: 30 } });
    return toCamelCase<EssayDailyReportList>(response.data);
  },
  runDailyReports: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/essay-radar/daily-reports/run', { force: false }, { timeout: 300000 });
    return toCamelCase(response.data);
  },
  list: async (filters: EssayFilters): Promise<EssayAnalysisList> => {
    const response = await apiClient.get('/api/v1/essay-radar/feed', {
      params: {
        days: filters.days ?? 0,
        query: filters.query || undefined,
        analysis_status: filters.analysisStatus || undefined,
        known_total: filters.knownTotal,
        sentiment: filters.sentiment || undefined,
        category: filters.category || undefined,
        tag: filters.tag || undefined,
        stock: filters.stock || undefined,
        min_importance: filters.minImportance ? filters.minImportance : undefined,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 20,
      },
    });
    return toCamelCase<EssayAnalysisList>(response.data);
  },
  detail: async (topicId: string): Promise<EssayAnalysis> => {
    const response = await apiClient.get(`/api/v1/essay-radar/analyses/${encodeURIComponent(topicId)}`);
    return toCamelCase<EssayAnalysis>(response.data);
  },
  note: async (topicId: string): Promise<ResearchNoteDetail> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/${encodeURIComponent(topicId)}`);
    return toCamelCase<ResearchNoteDetail>(response.data);
  },
  backfill: async (days = 30): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/essay-radar/backfill', { days }, { timeout: 180000 });
    return toCamelCase(response.data);
  },
  historicalBacklog: async (): Promise<EssayHistoricalBacklog> => {
    const response = await apiClient.get('/api/v1/essay-radar/historical-backlog');
    // camelcase-keys treats a trailing unit after a number as an acronym
    // (notes_24h -> notes24H). Normalize these three public fields explicitly
    // so the dashboard cannot silently show zero for factual activity data.
    const converted = toCamelCase<EssayHistoricalBacklog & {
      notes24H?: number;
      notes7D?: number;
      notes30D?: number;
    }>(response.data);
    return {
      ...converted,
      notes24h: converted.notes24H ?? converted.notes24h ?? 0,
      notes7d: converted.notes7D ?? converted.notes7d ?? 0,
      notes30d: converted.notes30D ?? converted.notes30d ?? 0,
    };
  },
  backfillCount: async (count: number, order: 'newest' | 'oldest'): Promise<EssayCountBackfillResponse> => {
    const response = await apiClient.post(
      '/api/v1/essay-radar/backfill-count',
      { count, order },
      { timeout: 180000 },
    );
    return toCamelCase<EssayCountBackfillResponse>(response.data);
  },
  startWorker: async (): Promise<EssayWorkerStatus> => {
    const response = await apiClient.post('/api/v1/essay-radar/worker/start');
    return toCamelCase<EssayWorkerStatus>(response.data);
  },
  stopWorker: async (): Promise<EssayWorkerStatus> => {
    const response = await apiClient.post('/api/v1/essay-radar/worker/stop');
    return toCamelCase<EssayWorkerStatus>(response.data);
  },
  retryFailed: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/essay-radar/retry-failed', { start_worker: true });
    return toCamelCase(response.data);
  },
  syncMcp: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/financial-data/zsxq/sync', undefined, { timeout: 120000 });
    return toCamelCase(response.data);
  },
  backfillMcpHistory: async (years: 1 | 2): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/financial-data/zsxq/history/backfill', { years });
    return toCamelCase(response.data);
  },
  startMcpWorker: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/financial-data/zsxq/sync/worker/start');
    return toCamelCase(response.data);
  },
  stopMcpWorker: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/financial-data/zsxq/sync/worker/stop');
    return toCamelCase(response.data);
  },
};
