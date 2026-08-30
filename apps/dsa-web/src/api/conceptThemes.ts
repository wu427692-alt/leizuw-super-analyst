import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { cachedQuery, invalidateCachedQueries } from './requestCache';
import { toCamelCase } from './utils';

export type ConceptTheme = {
  id: number;
  source: string;
  sourceLabel: string;
  sourceCode: string;
  name: string;
  canonicalName: string;
  themeType: string;
  level: number;
  parentCode?: string | null;
  constituentCount: number;
  marketDate?: string | null;
  heatScore?: number | null;
  pctChange?: number | null;
  fundFlow?: number | null;
  family: string;
  cluster: string;
  updatedAt?: string | null;
};

export type ConceptStock = {
  tsCode: string;
  name: string;
  sources: string[];
  reasons: string[];
  sourceCount: number;
  weightScore: number;
  beta?: number | null;
  marketBeta?: number | null;
  alphaAnnualized?: number | null;
  residualReturn?: number | null;
  rSquared?: number | null;
  observations?: number;
  confidence: string;
  betaInterpretation?: string;
  components?: Record<string, number | string | null>;
};

export type ConceptOverview = {
  items: ConceptTheme[];
  total: number;
  page: number;
  pageSize: number;
  summary: {
    themes: number;
    memberships: number;
    memberedThemes: number;
    attemptedThemes: number;
    failedThemes: number;
    scanCoveragePct: number;
    membershipCoveragePct: number;
    exposures: number;
    sources: Record<string, number>;
    types: Record<string, number>;
    families: Record<string, number>;
    clusterFamilies: Record<string, Record<string, number>>;
    marketDate?: string;
  };
  sync?: { status: string; progress: number; stage: string; marketDate?: string; sources?: Record<string, number> } | null;
  methodology: ConceptMethodology;
};

export type ConceptMethodology = {
  version: string;
  principles: string[];
  weightFormula: Record<string, number>;
  betaFormula: string;
  windows: number[];
  minimumObservations: number;
  sources: Array<{ key: string; name: string; reliability: number }>;
  licenseNote: string;
};

export type ThemeDetail = {
  theme: ConceptTheme;
  sourceNodes: ConceptTheme[];
  stocks: ConceptStock[];
  totalStocks: number;
  consensusStocks: number;
  consensusDistribution?: { strong: number; confirmed: number; singleSource: number };
  attributionReady: number;
  horizonDays: number;
  methodology: ConceptMethodology;
};

export type StockThemeLens = {
  tsCode: string;
  name: string;
  asOfDate?: string | null;
  horizonDays: number;
  themes: Array<ConceptStock & {
    canonicalName: string;
    family: string;
    cluster: string;
    themeType: string;
    themeIds: number[];
  }>;
  primaryThemes: StockThemeLens['themes'];
  uniqueDrivers: Array<{
    kind: string;
    title: string;
    summary?: string;
    source: string;
    date?: string;
    importance?: number;
    url?: string;
  }>;
  summary: { themeCount: number; sourceCount: number; consensusCount: number; alphaPositiveCount: number };
  methodology: ConceptMethodology;
};

type OverviewParams = {
  query?: string;
  themeType?: string;
  source?: string;
  family?: string;
  cluster?: string;
  sortBy?: 'heat' | 'name' | 'size' | 'change';
  page?: number;
  pageSize?: number;
};

type RawConceptOverview = {
  summary?: {
    sources?: Record<string, number>;
    types?: Record<string, number>;
    families?: Record<string, number>;
    cluster_families?: Record<string, Record<string, number>>;
  };
  sync?: { sources?: Record<string, number> } | null;
};

/**
 * API 字段名需要转成 camelCase，但题材名和来源代码本身是业务数据，不能改写。
 * camelcase-keys 的 deep 模式会把 “AI算力…” 误改为 “ai算力…”，继而让筛选条件失配。
 */
export function normalizeConceptOverview(data: unknown): ConceptOverview {
  const normalized = toCamelCase<ConceptOverview>(data);
  const raw = data as RawConceptOverview;
  if (raw.summary) {
    normalized.summary.sources = raw.summary.sources ?? {};
    normalized.summary.types = raw.summary.types ?? {};
    normalized.summary.families = raw.summary.families ?? {};
    normalized.summary.clusterFamilies = raw.summary.cluster_families ?? {};
  }
  if (normalized.sync && raw.sync?.sources) normalized.sync.sources = raw.sync.sources;
  return normalized;
}

export const conceptThemesApi = {
  overview: async (params: OverviewParams = {}): Promise<ConceptOverview> => {
    const key = `concept:overview:${JSON.stringify(params)}`;
    return cachedQuery(key, async () => {
      const response = await apiClient.get('/api/v1/concept-themes/overview', {
        params: {
          query: params.query || undefined,
          theme_type: params.themeType || undefined,
          source: params.source || undefined,
          family: params.family || undefined,
          cluster: params.cluster || undefined,
          sort_by: params.sortBy ?? 'heat',
          page: params.page ?? 1,
          page_size: params.pageSize ?? 80,
        },
        headers: BACKGROUND_ROUTE_HEADERS,
      });
      return normalizeConceptOverview(response.data);
    }, { freshMs: 20_000, staleMs: 5 * 60_000 });
  },
  theme: async (themeId: number, refreshIfEmpty = true, horizonDays = 60): Promise<ThemeDetail> => {
    const response = await apiClient.get(`/api/v1/concept-themes/themes/${themeId}`, {
      params: { refresh_if_empty: refreshIfEmpty, horizon_days: horizonDays },
      headers: BACKGROUND_ROUTE_HEADERS,
      timeout: 90_000,
    });
    return toCamelCase<ThemeDetail>(response.data);
  },
  stock: async (tsCode: string, refreshIfEmpty = true, horizonDays = 60): Promise<StockThemeLens> => {
    const response = await apiClient.get(`/api/v1/concept-themes/stocks/${encodeURIComponent(tsCode)}`, {
      params: { refresh_if_empty: refreshIfEmpty, horizon_days: horizonDays },
      headers: BACKGROUND_ROUTE_HEADERS,
      timeout: 90_000,
    });
    return toCamelCase<StockThemeLens>(response.data);
  },
  wakeSync: async (): Promise<void> => {
    await apiClient.post('/api/v1/concept-themes/sync/catalog', {});
    invalidateCachedQueries('concept:');
  },
};
