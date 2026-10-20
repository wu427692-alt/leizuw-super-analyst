import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';
import type { IndustryResearchBlueprint, IndustryResearchProject, IndustryResearchProjectFilter, IndustryResearchProjectList } from '../types/industryResearch';
import { cachedQuery, invalidateCachedQueries } from './requestCache';

export const industryResearchApi = {
  blueprint: async (
    topic: string,
    lookbackDays = 730,
    researchType: 'industry' | 'company' = 'industry',
  ): Promise<IndustryResearchBlueprint> => {
    const normalizedTopic = topic.trim();
    return cachedQuery(`industry:blueprint:${researchType}:${normalizedTopic}:${lookbackDays}`, async () => {
      const response = await apiClient.get('/api/v1/industry-research/blueprint', {
        params: { topic: normalizedTopic, lookback_days: lookbackDays, research_type: researchType },
        headers: BACKGROUND_ROUTE_HEADERS,
        timeout: 60_000,
      });
      return toCamelCase<IndustryResearchBlueprint>(response.data);
    }, { freshMs: 10 * 60_000, staleMs: 24 * 60 * 60_000 });
  },
  createProject: async (payload: {
    topic: string; researchType: 'industry' | 'company'; objective: string; lookbackDays: number; queryTerms?: string[];
  }): Promise<IndustryResearchProject> => {
    const response = await apiClient.post('/api/v1/industry-research/projects', {
      topic: payload.topic,
      research_type: payload.researchType,
      objective: payload.objective,
      lookback_days: payload.lookbackDays,
      query_terms: payload.queryTerms ?? [],
    }, { timeout: 30_000 });
    invalidateCachedQueries('industry:projects');
    return toCamelCase<IndustryResearchProject>(response.data);
  },
  projects: async (options: {
    limit?: number; offset?: number; query?: string; status?: IndustryResearchProjectFilter; force?: boolean;
  } = {}): Promise<IndustryResearchProjectList> => {
    const { limit = 30, offset = 0, status = 'all', force = false } = options;
    const query = options.query?.trim() ?? '';
    return cachedQuery(`industry:projects:${JSON.stringify([limit, offset, query, status])}`, async () => {
      const response = await apiClient.get('/api/v1/industry-research/projects', {
        params: { limit, offset, query, status }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<IndustryResearchProjectList>(response.data);
    }, { freshMs: 5_000, staleMs: 30_000, force });
  },
  project: async (projectId: string): Promise<IndustryResearchProject> => {
    const response = await apiClient.get(`/api/v1/industry-research/projects/${encodeURIComponent(projectId)}`, {
      headers: BACKGROUND_ROUTE_HEADERS,
    });
    return toCamelCase<IndustryResearchProject>(response.data);
  },
  downloadUrl: (projectId: string, format: 'docx' | 'pdf' | 'markdown' | 'json' = 'docx'): string => (
    `/api/v1/industry-research/projects/${encodeURIComponent(projectId)}/download?format=${format}`
  ),
};
