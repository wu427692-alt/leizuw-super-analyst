import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';
import type { IndustryResearchBlueprint, IndustryResearchProject } from '../types/industryResearch';
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
  projects: async (): Promise<{ items: IndustryResearchProject[]; total: number }> => {
    return cachedQuery('industry:projects', async () => {
      const response = await apiClient.get('/api/v1/industry-research/projects', { headers: BACKGROUND_ROUTE_HEADERS });
      return toCamelCase(response.data);
    }, { freshMs: 5_000, staleMs: 30_000 });
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
