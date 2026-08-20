import apiClient from './index';
import { toCamelCase } from './utils';
import type { AcquisitionCapabilities, AcquisitionJob, AcquisitionPlan } from '../types/dataAcquisition';

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
    const response = await apiClient.get('/api/v1/data-acquisition/capabilities');
    return toCamelCase<AcquisitionCapabilities>(response.data);
  },
  plan: async (request: string): Promise<AcquisitionPlan> => {
    const response = await apiClient.post('/api/v1/data-acquisition/plan', { request }, { timeout: 150000 });
    return readPlan(response.data as RawPlan);
  },
  run: async (request: string, plan: AcquisitionPlan): Promise<AcquisitionJob> => {
    const response = await apiClient.post('/api/v1/data-acquisition/run', { request, plan: writePlan(plan) }, { timeout: 900000 });
    const job = toCamelCase<AcquisitionJob>(response.data);
    job.plan = readPlan((response.data as { plan: RawPlan }).plan);
    return job;
  },
  jobs: async (): Promise<{ items: AcquisitionJob[]; total: number }> => {
    const response = await apiClient.get('/api/v1/data-acquisition/jobs');
    return toCamelCase(response.data);
  },
  download: async (jobId: string): Promise<Blob> => {
    const response = await apiClient.get(`/api/v1/data-acquisition/jobs/${encodeURIComponent(jobId)}/download`, {
      responseType: 'blob', timeout: 120000,
    });
    return response.data as Blob;
  },
};
