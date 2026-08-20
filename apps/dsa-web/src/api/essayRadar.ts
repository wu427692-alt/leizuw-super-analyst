import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayAnalysis, EssayAnalysisList, EssayDailyReportList, EssayDashboard, EssayInsights, EssayStatus, EssayWordCloud, EssayWorkerStatus } from '../types/essayRadar';

export type EssayFilters = {
  days?: number;
  query?: string;
  sentiment?: string;
  category?: string;
  tag?: string;
  stock?: string;
  minImportance?: number;
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
  wordCloud: async (period: 'day' | 'week' | 'month', kind: 'stocks' | 'tags' | 'themes' = 'stocks'): Promise<EssayWordCloud> => {
    const response = await apiClient.get('/api/v1/essay-radar/word-cloud', { params: { period, kind } });
    return toCamelCase<EssayWordCloud>(response.data);
  },
  dailyReports: async (): Promise<EssayDailyReportList> => {
    const response = await apiClient.get('/api/v1/essay-radar/daily-reports', { params: { limit: 30 } });
    return toCamelCase<EssayDailyReportList>(response.data);
  },
  runDailyReports: async (): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/essay-radar/daily-reports/run', { force: false });
    return toCamelCase(response.data);
  },
  list: async (filters: EssayFilters): Promise<EssayAnalysisList> => {
    const response = await apiClient.get('/api/v1/essay-radar/analyses', {
      params: {
        days: filters.days ?? 30,
        query: filters.query || undefined,
        sentiment: filters.sentiment || undefined,
        category: filters.category || undefined,
        tag: filters.tag || undefined,
        stock: filters.stock || undefined,
        min_importance: filters.minImportance,
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
  backfill: async (days = 30): Promise<unknown> => {
    const response = await apiClient.post('/api/v1/essay-radar/backfill', { days });
    return toCamelCase(response.data);
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
    const response = await apiClient.post('/api/v1/financial-data/zsxq/sync');
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
