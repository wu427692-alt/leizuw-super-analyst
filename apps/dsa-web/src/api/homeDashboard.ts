import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { cachedQuery } from './requestCache';
import { toCamelCase } from './utils';
import type { HomeDashboard } from '../types/homeDashboard';

export const homeDashboardApi = {
  get: async (force = false, refresh = false, background = false): Promise<HomeDashboard> => {
    const query = async () => {
      const response = await apiClient.get('/api/v1/home-dashboard', {
        params: { force, refresh },
        headers: background ? BACKGROUND_ROUTE_HEADERS : undefined,
        timeout: 120000,
      });
      return toCamelCase<HomeDashboard>(response.data);
    };
    return cachedQuery('home:dashboard', query, {
      freshMs: 15_000,
      staleMs: 5 * 60_000,
      force: force || refresh,
    });
  },
};
