import axios from 'axios';
import type { InternalAxiosRequestConfig } from 'axios';
import { API_BASE_URL } from '../utils/constants';
import { finishRouteRequest, startRouteRequest } from '../utils/routeLoadTracker';
import type { RouteLoadToken } from '../utils/routeLoadTracker';
import { attachParsedApiError } from './error';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

type RetryableRequestConfig = InternalAxiosRequestConfig & {
  __dsaRetryCount?: number;
  __dsaRouteLoadToken?: RouteLoadToken | null;
};

const TRANSIENT_HTTP_STATUSES = new Set([408, 425, 500, 502, 503, 504]);

function isRetryableRead(error: unknown): boolean {
  if (!axios.isAxiosError(error) || axios.isCancel(error) || !error.config) return false;
  const method = String(error.config.method || 'get').toLowerCase();
  if (method !== 'get' && method !== 'head') return false;
  const status = error.response?.status;
  if (status != null) return TRANSIENT_HTTP_STATUSES.has(status);
  return ['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK'].includes(String(error.code || ''));
}

function retryDelay(attempt: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, attempt * 280));
}

function completeTrackedRequest(config?: RetryableRequestConfig) {
  if (!config?.__dsaRouteLoadToken) return;
  finishRouteRequest(config.__dsaRouteLoadToken);
  config.__dsaRouteLoadToken = null;
}

apiClient.interceptors.request.use((config: RetryableRequestConfig) => {
  if (config.__dsaRouteLoadToken === undefined) {
    config.__dsaRouteLoadToken = startRouteRequest();
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => {
    completeTrackedRequest(response.config as RetryableRequestConfig);
    return response;
  },
  async (error) => {
    // Superseded searches and route changes intentionally cancel requests.
    // Keep them silent instead of converting them into user-facing failures.
    if (axios.isCancel(error) || error?.code === 'ERR_CANCELED' || error?.name === 'AbortError') {
      completeTrackedRequest(error.config as RetryableRequestConfig | undefined);
      return Promise.reject(error);
    }
    if (error.response?.status === 401) {
      const path = window.location.pathname + window.location.search;
      if (!path.startsWith('/login')) {
        const redirect = encodeURIComponent(path);
        window.location.assign(`/login?redirect=${redirect}`);
      }
    }
    const requestConfig = error.config as RetryableRequestConfig | undefined;
    if (requestConfig && isRetryableRead(error)) {
      const retryCount = requestConfig.__dsaRetryCount ?? 0;
      if (retryCount < 2) {
        requestConfig.__dsaRetryCount = retryCount + 1;
        await retryDelay(requestConfig.__dsaRetryCount);
        return apiClient.request(requestConfig);
      }
    }
    completeTrackedRequest(requestConfig);
    attachParsedApiError(error);
    return Promise.reject(error);
  }
);

export default apiClient;
