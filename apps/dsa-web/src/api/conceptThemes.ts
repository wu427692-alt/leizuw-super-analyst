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
  canonicalSourceCount: number;
  canonicalNodeCount: number;
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
  specificityScore?: number | null;
  confidence: string;
  betaInterpretation?: string;
  components?: Record<string, number | string | null>;
  evidence?: Array<string | { kind?: string; title?: string; summary?: string; source?: string; topicId?: string }>;
  inWatchlist?: boolean;
};

export type ConceptOverview = {
  items: ConceptTheme[];
  stockMatches: Array<{ tsCode: string; name: string; themeCount: number; sourceCount: number }>;
  total: number;
  page: number;
  pageSize: number;
  summary: {
    themes: number;
    classifiedThemes: number;
    semanticCoveragePct: number;
    memberships: number;
    memberedThemes: number;
    attemptedThemes: number;
    failedThemes: number;
    scanCoveragePct: number;
    membershipCoveragePct: number;
    exposures: number;
    sources: Record<string, number>;
    sourceHealth: Record<string, {
      catalogNodes: number;
      marketDate?: string | null;
      updatedAt?: string | null;
      attemptedThemes: number;
      memberedThemes: number;
      scanCoveragePct: number;
      status: 'fresh' | 'lagging';
    }>;
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

export type ConceptRotation = {
  items: Array<{
    canonicalName: string;
    family: string;
    cluster: string;
    marketDate: string;
    pctChange?: number | null;
    momentum5d?: number | null;
    heatScore?: number | null;
    sourceCount: number;
    rotationScore: number;
    historyDays: number;
    points: Array<{ date: string; pctChange?: number | null; heatScore?: number | null; sourceCount: number }>;
  }>;
  total: number;
  windowDays: number;
  availableDates: number;
  latestDate?: string | null;
  method: string;
};

export type ConceptLeaderExposure = {
  canonicalName: string;
  weightScore: number;
  sourceCount: number;
  beta?: number | null;
  residualReturn?: number | null;
  specificityScore?: number | null;
  confidence: string;
};

export type ConceptLeaders = {
  items: Array<{
    tsCode: string;
    name: string;
    asOfDate?: string | null;
    radarScore: number;
    totalThemeCount: number;
    consensusThemeCount: number;
    positiveAlphaCount: number;
    sourceBreadth: number;
    averageWeight: number;
    primaryThemes: ConceptLeaderExposure[];
    betaFocus?: ConceptLeaderExposure | null;
    alphaFocus?: ConceptLeaderExposure | null;
    divergenceFocus?: ConceptLeaderExposure | null;
    specificityFocus?: ConceptLeaderExposure | null;
    inWatchlist?: boolean;
  }>;
  totalCandidates: number;
  mode: 'consensus' | 'alpha' | 'beta' | 'specificity';
  horizonDays: number;
  asOfDate?: string | null;
  method: string;
};

export type ThemeDetail = {
  theme: ConceptTheme;
  sourceNodes: ConceptTheme[];
  stocks: ConceptStock[];
  watchlistStocks: Array<{ tsCode: string; name: string }>;
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
  uniqueThemes: StockThemeLens['themes'];
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
  minSources?: number;
  sortBy?: 'heat' | 'name' | 'size' | 'change';
  page?: number;
  pageSize?: number;
};

type RawConceptOverview = {
  summary?: {
    sources?: Record<string, number>;
    source_health?: Record<string, unknown>;
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
    if (raw.summary.source_health) {
      normalized.summary.sourceHealth = Object.fromEntries(Object.entries(raw.summary.source_health).map(([key, value]) => [key, toCamelCase(value)])) as ConceptOverview['summary']['sourceHealth'];
    }
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
          min_sources: params.minSources && params.minSources > 1 ? params.minSources : undefined,
          sort_by: params.sortBy ?? 'heat',
          page: params.page ?? 1,
          page_size: params.pageSize ?? 80,
        },
        headers: BACKGROUND_ROUTE_HEADERS,
      });
      return normalizeConceptOverview(response.data);
    }, { freshMs: 20_000, staleMs: 5 * 60_000 });
  },
  rotation: async (days = 20, limit = 24): Promise<ConceptRotation> => cachedQuery(
    `concept:rotation:${days}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/rotation', {
        params: { days, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<ConceptRotation>(response.data);
    },
    { freshMs: 60_000, staleMs: 15 * 60_000 },
  ),
  leaders: async (horizonDays = 60, mode: ConceptLeaders['mode'] = 'consensus', limit = 24): Promise<ConceptLeaders> => cachedQuery(
    `concept:leaders:${horizonDays}:${mode}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/leaders', {
        params: { horizon_days: horizonDays, mode, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<ConceptLeaders>(response.data);
    },
    { freshMs: 30_000, staleMs: 5 * 60_000 },
  ),
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
  exportTheme: async (themeId: number, horizonDays = 60): Promise<void> => {
    const response = await apiClient.get(`/api/v1/concept-themes/themes/${themeId}/export.csv`, {
      params: { horizon_days: horizonDays }, responseType: 'blob', timeout: 90_000,
    });
    const disposition = String(response.headers['content-disposition'] || '');
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encodedName ? decodeURIComponent(encodedName) : `题材归因_${themeId}.csv`;
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  },
  wakeSync: async (): Promise<void> => {
    await apiClient.post('/api/v1/concept-themes/sync/catalog', {});
    invalidateCachedQueries('concept:');
  },
};
