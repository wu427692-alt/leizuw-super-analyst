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
  view?: 'canonical' | 'source';
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
    exposureThemes: number;
    exposureStocks: number;
    researchableThemes: number;
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
    quality: {
      catalogDate?: string | null;
      exposureDate?: string | null;
      freshCatalogs: number;
      totalCatalogs: number;
      failedThemes: number;
      warnings: string[];
    };
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
  sources: Array<{ key: string; name: string; provider: string; reliability: number }>;
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
    independentClusterCount: number;
    themeOverlapRate: number;
    dominantCluster?: string | null;
    dominantClusterShare: number;
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

export type InstitutionThemeRadar = {
  items: Array<{
    canonicalName: string;
    status: 'provider_consensus' | 'provider_single' | 'corpus_candidate';
    providerCount: number;
    providerSources: string[];
    noteCount: number;
    recent7D: number;
    accelerationPct?: number | null;
    baselineWeek: number;
    discoveryScore: number;
    sentiment: { bullish: number; neutral: number; bearish: number };
    stocks: Array<{ tsCode: string; name: string; mentions: number }>;
    latestAt?: string | null;
    samples: Array<{ title: string; topicId: string; date: string; url: string }>;
  }>;
  totalCandidates: number;
  windowDays: number;
  asOfAt?: string | null;
  method: string;
};

export type ConceptLifecycle = {
  items: Array<{
    family: string;
    cluster: string;
    stage: '共识扩张' | '语料先行' | '价格驱动' | '分歧退潮' | '交叉观察';
    score: number;
    marketDate?: string | null;
    marketMomentum5D: number;
    marketChange?: number | null;
    marketSourceCount: number;
    corpusNotes: number;
    corpusRecent7D: number;
    corpusAccelerationPct?: number | null;
    marketThemes: string[];
    corpusThemes: string[];
    interpretation: string;
  }>;
  total: number;
  windowDays: number;
  marketDate?: string | null;
  corpusAsOfAt?: string | null;
  method: string;
};

export type WatchlistThemeMap = {
  stocks: Array<{
    tsCode: string;
    name: string;
    asOfDate?: string | null;
    rawThemeCount: number;
    independentClusterCount: number;
    overlapRate: number;
    dominantTheme?: (ConceptLeaderExposure & { family: string; cluster: string }) | null;
    themes: Array<ConceptLeaderExposure & { family: string; cluster: string }>;
  }>;
  themes: Array<{
    cluster: string;
    family: string;
    stockCount: number;
    stocks: Array<{ tsCode: string; name: string }>;
    averageWeight: number;
    themes: string[];
  }>;
  stockCount: number;
  horizonDays: number;
  asOfDate?: string | null;
  concentration: {
    level: '高' | '中' | '低';
    topCluster?: string | null;
    topCoveragePct: number;
    sharedClusterCount: number;
    coveredStockCount: number;
    averageClusterCount: number;
    divergentStocks: Array<{ tsCode: string; name: string }>;
    interpretation: string;
  };
  method: string;
};

export type ConceptMembershipChanges = {
  items: Array<{
    state: 'added' | 'removed';
    tsCode: string;
    name: string;
    canonicalName: string;
    family: string;
    cluster: string;
    sources: string[];
    sourceCount: number;
    eventAt: string;
    marketDate?: string | null;
    reasons: string[];
  }>;
  added: number;
  removed: number;
  baselineIgnored: number;
  windowDays: number;
  cutoffAt: string;
  method: string;
};

export type ConceptClusterDetail = {
  family: string;
  cluster: string;
  items: Array<{
    tsCode: string;
    name: string;
    clusterScore: number;
    themeCount: number;
    sourceCount: number;
    sources: string[];
    themes: string[];
    reasons: string[];
    dominantExposure?: ConceptLeaderExposure | null;
    inWatchlist?: boolean;
  }>;
  totalStocks: number;
  themeNodes: number;
  canonicalThemes: number;
  sourceCount: number;
  asOfDate?: string | null;
  horizonDays: number;
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
  history: {
    points: Array<{ date: string; pctChange: number; cumulativeReturn: number; sourceCount: number; heatScore: number }>;
    availableDates: number;
    latestDate?: string | null;
    cumulativeReturn?: number | null;
    method: string;
  };
  institutionCorpus: {
    total: number;
    bullish: number;
    bearish: number;
    neutral: number;
    score: number;
    recent14D: number;
    priorWindow: number;
    volumeChangePct?: number | null;
    truncated?: boolean;
    windowDays: number;
    items: Array<{
      topicId: string;
      title: string;
      summary: string;
      sentiment: 'bullish' | 'bearish' | 'neutral';
      importance: number;
      confidence: number;
      model: string;
      createdAt?: string | null;
      url: string;
    }>;
    method: string;
  };
  relatedThemes: {
    items: Array<{
      canonicalName: string;
      family: string;
      cluster: string;
      relationType: '高度重叠' | '同主题簇' | '同题材家族' | '跨题材共现';
      sharedStocks: number;
      targetCoveragePct: number;
      jaccardPct: number;
      targetExclusiveStocks: number;
      otherTotalStocks: number;
    }>;
    targetTotalStocks: number;
    method: string;
  };
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
    betaStability: 'stable' | 'shifting' | 'insufficient';
    horizonProfile: Array<{
      horizonDays: number;
      asOfDate: string;
      beta?: number | null;
      residualReturn?: number | null;
      rSquared?: number | null;
      observations: number;
      confidence: string;
    }>;
  }>;
  themesTruncated?: boolean;
  totalThemeCount?: number;
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
    category: string;
    direction: 'positive' | 'negative' | 'neutral';
  }>;
  uniqueDriverSummary: {
    categories: Record<string, number>;
    directions: Record<string, number>;
    method: string;
  };
  overlapAudit: {
    independentClusterCount: number;
    overlapRate: number;
    dominantClusterShare: number;
    dominantCluster?: string | null;
    clusters: Array<{
      family: string;
      cluster: string;
      themeCount: number;
      themes: string[];
      weightShare: number;
    }>;
    method: string;
  };
  summary: {
    themeCount: number;
    sourceCount: number;
    consensusCount: number;
    independentClusterCount: number;
    themeOverlapRate: number;
    alphaPositiveCount: number;
    stableBetaCount: number;
    persistentAlphaCount: number;
  };
  methodology: ConceptMethodology;
};

type OverviewParams = {
  query?: string;
  themeType?: string;
  source?: string;
  family?: string;
  cluster?: string;
  minSources?: number;
  readiness?: 'all' | 'membered' | 'attributed' | 'researchable';
  view?: 'canonical' | 'source';
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
          readiness: params.readiness && params.readiness !== 'all' ? params.readiness : undefined,
          view: params.view ?? 'canonical',
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
  institutionRadar: async (days = 30, limit = 16): Promise<InstitutionThemeRadar> => cachedQuery(
    `concept:institution-radar:${days}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/institution-radar', {
        params: { days, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<InstitutionThemeRadar>(response.data);
    },
    { freshMs: 60_000, staleMs: 10 * 60_000 },
  ),
  lifecycle: async (days = 30, limit = 12): Promise<ConceptLifecycle> => cachedQuery(
    `concept:lifecycle:${days}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/lifecycle', {
        params: { days, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<ConceptLifecycle>(response.data);
    },
    { freshMs: 60_000, staleMs: 10 * 60_000 },
  ),
  watchlistMap: async (horizonDays = 60): Promise<WatchlistThemeMap> => cachedQuery(
    `concept:watchlist-map:${horizonDays}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/watchlist-map', {
        params: { horizon_days: horizonDays }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<WatchlistThemeMap>(response.data);
    },
    { freshMs: 30_000, staleMs: 5 * 60_000 },
  ),
  membershipChanges: async (days = 7, limit = 24): Promise<ConceptMembershipChanges> => cachedQuery(
    `concept:membership-changes:${days}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/membership-changes', {
        params: { days, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<ConceptMembershipChanges>(response.data);
    },
    { freshMs: 60_000, staleMs: 10 * 60_000 },
  ),
  cluster: async (family: string, cluster: string, horizonDays = 60, limit = 80): Promise<ConceptClusterDetail> => cachedQuery(
    `concept:cluster:${family}:${cluster}:${horizonDays}:${limit}`,
    async () => {
      const response = await apiClient.get('/api/v1/concept-themes/cluster-detail', {
        params: { family, cluster, horizon_days: horizonDays, limit }, headers: BACKGROUND_ROUTE_HEADERS,
      });
      return toCamelCase<ConceptClusterDetail>(response.data);
    },
    { freshMs: 30_000, staleMs: 5 * 60_000 },
  ),
  theme: async (themeId: number, refreshIfEmpty = true, horizonDays = 60): Promise<ThemeDetail> => cachedQuery(
    `concept:theme:${themeId}:${horizonDays}`,
    async () => {
      const response = await apiClient.get(`/api/v1/concept-themes/themes/${themeId}`, {
        params: { refresh_if_empty: refreshIfEmpty, horizon_days: horizonDays },
        headers: BACKGROUND_ROUTE_HEADERS,
        timeout: 90_000,
      });
      return toCamelCase<ThemeDetail>(response.data);
    },
    { freshMs: 20_000, staleMs: 2 * 60_000 },
  ),
  stock: async (tsCode: string, refreshIfEmpty = true, horizonDays = 60): Promise<StockThemeLens> => cachedQuery(
    `concept:stock:${tsCode}:${horizonDays}`,
    async () => {
      const response = await apiClient.get(`/api/v1/concept-themes/stocks/${encodeURIComponent(tsCode)}`, {
        params: { refresh_if_empty: refreshIfEmpty, horizon_days: horizonDays },
        headers: BACKGROUND_ROUTE_HEADERS,
        timeout: 90_000,
      });
      return toCamelCase<StockThemeLens>(response.data);
    },
    { freshMs: 30_000, staleMs: 3 * 60_000 },
  ),
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
  exportStock: async (tsCode: string, horizonDays = 60): Promise<void> => {
    const response = await apiClient.get(`/api/v1/concept-themes/stocks/${encodeURIComponent(tsCode)}/export.csv`, {
      params: { horizon_days: horizonDays }, responseType: 'blob', timeout: 90_000,
    });
    const disposition = String(response.headers['content-disposition'] || '');
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encodedName ? decodeURIComponent(encodedName) : `${tsCode}_题材画像.csv`;
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  },
  exportWatchlistMap: async (horizonDays = 60): Promise<void> => {
    const response = await apiClient.get('/api/v1/concept-themes/watchlist-map/export.csv', {
      params: { horizon_days: horizonDays }, responseType: 'blob', timeout: 90_000,
    });
    const disposition = String(response.headers['content-disposition'] || '');
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encodedName ? decodeURIComponent(encodedName) : '我的自选题材暴露.csv';
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
