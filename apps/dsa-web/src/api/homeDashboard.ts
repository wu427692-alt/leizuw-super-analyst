import apiClient from './index';
import { toCamelCase } from './utils';
import type { HomeDashboard } from '../types/homeDashboard';

export const homeDashboardApi = {
  get: async (force = false, refresh = false): Promise<HomeDashboard> => {
    const response = await apiClient.get('/api/v1/home-dashboard', { params: { force, refresh }, timeout: 120000 });
    return toCamelCase<HomeDashboard>(response.data);
  },
};
