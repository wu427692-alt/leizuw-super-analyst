import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayAnalysis, EssayAnalysisList, EssayAudioAnalysisCapability, EssayAudioAnalysisTask, EssayAudioBatchTask, EssayAudioDownloadProgress, EssayAudioFileList, EssayAudioTranscript, EssayCountBackfillResponse, EssayDailyReportList, EssayDashboard, EssayDeepInsights, EssayHistoricalBacklog, EssayInsights, EssayStatus, EssayWordCloud, EssayWorkerStatus, ResearchNoteDetail } from '../types/essayRadar';
import { cachedQuery } from './requestCache';

export type EssayFilters = {
  days?: number;
  query?: string;
  queryScope?: 'title' | 'full';
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
    return cachedQuery(`essay:status:${days}`, async () => {
      const response = await apiClient.get('/api/v1/essay-radar/status', { params: { days } });
      return toCamelCase<EssayStatus>(response.data);
    }, { freshMs: 8_000, staleMs: 60_000 });
  },
  dashboard: async (days = 30): Promise<EssayDashboard> => {
    return cachedQuery(`essay:dashboard:${days}`, async () => {
      const response = await apiClient.get('/api/v1/essay-radar/dashboard', { params: { days } });
      return toCamelCase<EssayDashboard>(response.data);
    }, { freshMs: 15_000, staleMs: 120_000 });
  },
  insights: async (days = 30, trendDays = 14): Promise<EssayInsights> => {
    return cachedQuery(`essay:insights:${days}:${trendDays}`, async () => {
      const response = await apiClient.get('/api/v1/essay-radar/insights', { params: { days, trend_days: trendDays } });
      return toCamelCase<EssayInsights>(response.data);
    }, { freshMs: 15_000, staleMs: 120_000 });
  },
  deepInsights: async (params: {
    days?: number; trendDays?: number; horizon?: 'short' | 'medium' | 'long' | 'custom';
    startDate?: string; endDate?: string;
  } = {}): Promise<EssayDeepInsights> => {
    const key = `essay:deep:${JSON.stringify(params)}`;
    return cachedQuery(key, async () => {
      const response = await apiClient.get('/api/v1/essay-radar/deep-insights', {
        params: {
          days: params.days ?? 30,
          trend_days: params.trendDays ?? 14,
          horizon: params.horizon,
          start_date: params.startDate,
          end_date: params.endDate,
        },
        timeout: 120000,
      });
      return toCamelCase<EssayDeepInsights>(response.data);
    }, { freshMs: 30_000, staleMs: 600_000 });
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
    return cachedQuery(`essay:word-cloud:${period}:${kind}`, async () => {
      const response = await apiClient.get('/api/v1/essay-radar/word-cloud', { params: { period, kind } });
      return toCamelCase<EssayWordCloud>(response.data);
    }, { freshMs: 15_000, staleMs: 120_000 });
  },
  dailyReports: async (): Promise<EssayDailyReportList> => {
    return cachedQuery('essay:daily-reports', async () => {
      const response = await apiClient.get('/api/v1/essay-radar/daily-reports', { params: { limit: 30 } });
      return toCamelCase<EssayDailyReportList>(response.data);
    }, { freshMs: 30_000, staleMs: 300_000 });
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
        query_scope: filters.queryScope ?? 'full',
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
  exportFeed: async (filters: EssayFilters): Promise<Blob> => {
    const response = await apiClient.get('/api/v1/essay-radar/feed/export', {
      params: {
        days: filters.days ?? 0,
        query: filters.query || undefined,
        query_scope: filters.queryScope ?? 'full',
        analysis_status: filters.analysisStatus || undefined,
        sentiment: filters.sentiment || undefined,
        category: filters.category || undefined,
        tag: filters.tag || undefined,
        stock: filters.stock || undefined,
        min_importance: filters.minImportance ? filters.minImportance : undefined,
      },
      responseType: 'blob',
      timeout: 300000,
    });
    return response.data as Blob;
  },
  exportSelected: async (topicIds: string[]): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/essay-radar/feed/export-selected', {
      topic_ids: topicIds,
    }, {
      responseType: 'blob',
      timeout: 300000,
    });
    return response.data as Blob;
  },
  audioFiles: async (filters: Pick<EssayFilters, 'days' | 'query' | 'page' | 'pageSize'>): Promise<EssayAudioFileList> => {
    const response = await apiClient.get('/api/v1/financial-data/research-notes/audio-files', {
      params: {
        days: filters.days ?? 0,
        query: filters.query || undefined,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 20,
      },
    });
    return toCamelCase<EssayAudioFileList>(response.data);
  },
  downloadSelectedAudio: async (items: Array<{ topicId: string; fileId: string }>): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/financial-data/research-notes/audio-files/batch-download', {
      items: items.map((item) => ({ topic_id: item.topicId, file_id: item.fileId })),
    }, {
      responseType: 'blob',
      timeout: 600000,
    });
    return response.data as Blob;
  },
  startAudioBatchTask: async (items: Array<{ topicId: string; fileId: string }>): Promise<EssayAudioBatchTask> => {
    const response = await apiClient.post('/api/v1/financial-data/research-notes/audio-files/batch-download-tasks', {
      items: items.map((item) => ({ topic_id: item.topicId, file_id: item.fileId })),
    });
    return toCamelCase<EssayAudioBatchTask>(response.data);
  },
  audioBatchTask: async (taskId: string): Promise<EssayAudioBatchTask> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/audio-files/batch-download-tasks/${encodeURIComponent(taskId)}`);
    return toCamelCase<EssayAudioBatchTask>(response.data);
  },
  audioBatchTasks: async (): Promise<{ items: EssayAudioBatchTask[]; total: number }> => {
    const response = await apiClient.get('/api/v1/financial-data/research-notes/audio-files/batch-download-tasks', { params: { limit: 20 } });
    return toCamelCase(response.data);
  },
  downloadAudioBatchTask: async (
    taskId: string,
    onProgress?: (progress: EssayAudioDownloadProgress) => void,
  ): Promise<Blob> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/audio-files/batch-download-tasks/${encodeURIComponent(taskId)}/download`, {
      responseType: 'blob',
      timeout: 600000,
      onDownloadProgress: (event) => {
        const total = event.total && event.total > 0 ? event.total : undefined;
        onProgress?.({
          loaded: event.loaded,
          total,
          percent: total ? Math.min(100, Math.round((event.loaded / total) * 100)) : undefined,
        });
      },
    });
    return response.data as Blob;
  },
  audioAnalysisCapability: async (): Promise<EssayAudioAnalysisCapability> => {
    const response = await apiClient.get('/api/v1/financial-data/research-notes/audio-analysis/capability');
    return toCamelCase<EssayAudioAnalysisCapability>(response.data);
  },
  startAudioAnalysisTask: async (
    items: Array<{ topicId: string; fileId: string }>,
    options: { title?: string; focus?: string; hotwords?: string[]; speakerCount?: number; generateMemo?: boolean } = {},
  ): Promise<EssayAudioAnalysisTask> => {
    const response = await apiClient.post('/api/v1/financial-data/research-notes/audio-analysis/tasks', {
      items: items.map((item) => ({ topic_id: item.topicId, file_id: item.fileId })),
      title: options.title || undefined,
      focus: options.focus || undefined,
      hotwords: options.hotwords || [],
      speaker_count: options.speakerCount || undefined,
      generate_memo: options.generateMemo ?? true,
    });
    return toCamelCase<EssayAudioAnalysisTask>(response.data);
  },
  audioAnalysisTask: async (taskId: string): Promise<EssayAudioAnalysisTask> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/audio-analysis/tasks/${encodeURIComponent(taskId)}`);
    return toCamelCase<EssayAudioAnalysisTask>(response.data);
  },
  audioAnalysisTasks: async (): Promise<{ items: EssayAudioAnalysisTask[]; total: number }> => {
    const response = await apiClient.get('/api/v1/financial-data/research-notes/audio-analysis/tasks', { params: { limit: 20 } });
    return toCamelCase(response.data);
  },
  retryAudioAnalysisTask: async (taskId: string): Promise<EssayAudioAnalysisTask> => {
    const response = await apiClient.post(`/api/v1/financial-data/research-notes/audio-analysis/tasks/${encodeURIComponent(taskId)}/retry`);
    return toCamelCase<EssayAudioAnalysisTask>(response.data);
  },
  audioAnalysisTranscript: async (taskId: string, fileId: string): Promise<EssayAudioTranscript> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/audio-analysis/tasks/${encodeURIComponent(taskId)}/transcripts/${encodeURIComponent(fileId)}`);
    return toCamelCase<EssayAudioTranscript>(response.data);
  },
  downloadAudioTranscript: async (taskId: string, fileId: string): Promise<Blob> => {
    const response = await apiClient.get(
      `/api/v1/financial-data/research-notes/audio-analysis/tasks/${encodeURIComponent(taskId)}/transcripts/${encodeURIComponent(fileId)}/download`,
      { responseType: 'blob', timeout: 300000 },
    );
    return response.data as Blob;
  },
  downloadSelectedAudioTranscripts: async (items: Array<{ topicId: string; fileId: string }>): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/financial-data/research-notes/audio-files/transcripts/batch-download', {
      items: items.map((item) => ({ topic_id: item.topicId, file_id: item.fileId })),
    }, { responseType: 'blob', timeout: 300000 });
    return response.data as Blob;
  },
  downloadAudioAnalysis: async (taskId: string, format: 'zip' | 'md' | 'docx' | 'json'): Promise<Blob> => {
    const response = await apiClient.get(`/api/v1/financial-data/research-notes/audio-analysis/tasks/${encodeURIComponent(taskId)}/download`, {
      params: { format }, responseType: 'blob', timeout: 600000,
    });
    return response.data as Blob;
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
