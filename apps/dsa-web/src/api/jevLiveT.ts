import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';
import type { LiveTControl, LiveTRun, LiveTRunSummary, LiveTStatus } from '../types/jevLiveT';

const BASE = '/api/v1/jev-live-t';
// Control only the owner-scoped paper account; this API has no brokerage operation.
export const jevLiveTApi = {
  control: async (signal?: AbortSignal): Promise<LiveTControl> =>
    toCamelCase((await apiClient.get(`${BASE}/control`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  setControl: async (enabled: boolean, signal?: AbortSignal): Promise<LiveTControl> =>
    toCamelCase((await apiClient.post(`${BASE}/control`, { enabled }, { signal })).data),
  status: async (signal?: AbortSignal): Promise<LiveTStatus> =>
    toCamelCase((await apiClient.get(`${BASE}/status`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  runs: async (signal?: AbortSignal): Promise<{ items: LiveTRunSummary[] }> =>
    toCamelCase((await apiClient.get(`${BASE}/runs`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  run: async (runId: string, signal?: AbortSignal): Promise<LiveTRun> =>
    toCamelCase((await apiClient.get(`${BASE}/runs/${encodeURIComponent(runId)}`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
};
