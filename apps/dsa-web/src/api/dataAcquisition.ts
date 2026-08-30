import apiClient from './index';
import { toCamelCase } from './utils';
import { cachedQuery, invalidateCachedQueries } from './requestCache';
import type {
  AcquisitionCapabilities,
  AcquisitionDownloadProgress,
  AcquisitionJob,
  AcquisitionPlan,
  AcquisitionRunTask,
  ResearchReportFacets,
  ResearchReportLibraryStatus,
  ResearchReportSearchFilters,
  ResearchReportSearchResult,
} from '../types/dataAcquisition';

type RawPlan = Omit<AcquisitionPlan, 'outputFormats' | 'generatedAt' | 'scope'> & {
  output_formats?: string[];
  generated_at?: string;
  include_files?: boolean;
  scope?: { symbols?: string[]; company_names?: string[]; keywords?: string[]; start_date?: string; end_date?: string; market_wide?: boolean };
};

function readPlan(raw: RawPlan): AcquisitionPlan {
  return {
    ...raw,
    tasks: (raw.tasks ?? []).map((task) => ({ ...task, params: task.params ?? {} })),
    outputFormats: raw.output_formats ?? [],
    generatedAt: raw.generated_at ?? '',
    includeFiles: raw.include_files ?? false,
    scope: {
      symbols: raw.scope?.symbols ?? [], companyNames: raw.scope?.company_names ?? [],
      keywords: raw.scope?.keywords ?? [], startDate: raw.scope?.start_date ?? '',
      endDate: raw.scope?.end_date ?? '', marketWide: raw.scope?.market_wide ?? false,
    },
  };
}

function writePlan(plan: AcquisitionPlan) {
  return {
    title: plan.title, objective: plan.objective, model: plan.model, caveats: plan.caveats,
    generated_at: plan.generatedAt, output_formats: plan.outputFormats, include_files: plan.includeFiles ?? false,
    scope: { symbols: plan.scope.symbols, company_names: plan.scope.companyNames, keywords: plan.scope.keywords,
      start_date: plan.scope.startDate, end_date: plan.scope.endDate, market_wide: plan.scope.marketWide },
    // params is an opaque upstream contract and must never be camel-cased.
    tasks: plan.tasks.map((task) => ({ ...task, params: task.params })),
  };
}

export const dataAcquisitionApi = {
  capabilities: async (): Promise<AcquisitionCapabilities> => {
    return cachedQuery('acquisition:capabilities', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/capabilities');
      return toCamelCase<AcquisitionCapabilities>(response.data);
    }, { freshMs: 5 * 60_000, staleMs: 24 * 60 * 60_000 });
  },
  plan: async (request: string): Promise<AcquisitionPlan> => {
    const response = await apiClient.post('/api/v1/data-acquisition/plan', { request }, { timeout: 150000 });
    return readPlan(response.data as RawPlan);
  },
  runAsync: async (request: string, plan: AcquisitionPlan): Promise<AcquisitionRunTask> => {
    const response = await apiClient.post('/api/v1/data-acquisition/run-async', { request, plan: writePlan(plan) });
    return toCamelCase<AcquisitionRunTask>(response.data);
  },
  task: async (taskId: string): Promise<AcquisitionRunTask> => {
    const response = await apiClient.get(`/api/v1/data-acquisition/tasks/${encodeURIComponent(taskId)}`);
    const task = toCamelCase<AcquisitionRunTask>(response.data);
    if (task.result) task.result.plan = readPlan((response.data as { result: { plan: RawPlan } }).result.plan);
    return task;
  },
  jobs: async (): Promise<{ items: AcquisitionJob[]; total: number }> => {
    return cachedQuery('acquisition:jobs', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/jobs');
      return toCamelCase(response.data);
    }, { freshMs: 3_000, staleMs: 15_000 });
  },
  download: async (
    jobId: string,
    onProgress?: (progress: AcquisitionDownloadProgress) => void,
  ): Promise<Blob> => {
    const response = await apiClient.get(`/api/v1/data-acquisition/jobs/${encodeURIComponent(jobId)}/download`, {
      responseType: 'blob', timeout: 120000,
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
  researchReportStatus: async (): Promise<ResearchReportLibraryStatus> => {
    return cachedQuery('acquisition:research-status', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/status');
      return toCamelCase(response.data);
    }, { freshMs: 5_000, staleMs: 30_000 });
  },
  syncResearchReports: async (years = 2): Promise<ResearchReportLibraryStatus> => {
    const response = await apiClient.post('/api/v1/data-acquisition/research-reports/sync', undefined, { params: { years } });
    invalidateCachedQueries('acquisition:research-');
    return toCamelCase(response.data);
  },
  researchReportFacets: async (): Promise<ResearchReportFacets> => {
    return cachedQuery('acquisition:research-facets', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/facets');
      return toCamelCase(response.data);
    }, { freshMs: 10 * 60_000, staleMs: 24 * 60 * 60_000 });
  },
  searchResearchReports: async (
    filters: ResearchReportSearchFilters,
    page = 1,
    pageSize = 30,
  ): Promise<ResearchReportSearchResult> => {
    const key = `acquisition:research-search:${JSON.stringify({ filters, page, pageSize })}`;
    return cachedQuery(key, async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/search', {
        params: {
          title_query: filters.titleQuery, content_query: filters.contentQuery,
          broker: filters.broker, company: filters.company, ts_code: filters.tsCode,
          report_type: filters.reportType, industry: filters.industry, author: filters.author,
          tag: filters.tag, start_date: filters.startDate, end_date: filters.endDate,
          has_pdf: filters.hasPdf, sort: filters.sort, page, page_size: pageSize,
        },
      });
      return toCamelCase(response.data);
    }, { freshMs: 30_000, staleMs: 5 * 60_000 });
  },
  exportSelectedResearchReports: async (ids: number[]): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/data-acquisition/research-reports/export-selected', { ids }, {
      responseType: 'blob', timeout: 120000,
    });
    return response.data as Blob;
  },
};
