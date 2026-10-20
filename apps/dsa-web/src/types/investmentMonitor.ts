export type CountRow = { name: string; count: number };

export type StockResearchRelation = {
  kind: 'company_direct' | 'industry_related' | 'other_company_mention' | 'unknown';
  targetSymbol: string;
  subjectSymbol?: string | null;
  subjectName?: string | null;
  matchField?: string | null;
  matchText?: string | null;
  hasSubjectConflict?: boolean;
};

export type MonitorEvent = {
  id: number;
  sourceKey: string;
  sourceName: string;
  sourceType: string;
  externalId: string;
  eventType: string;
  perspective: 'investor' | 'company' | 'institution';
  title: string;
  summary?: string | null;
  url?: string | null;
  symbols: string[];
  sentiment: 'bullish' | 'bearish' | 'neutral' | 'mixed';
  importanceScore: number;
  confidenceScore: number;
  tags: string[];
  actors: string[];
  metrics: Record<string, unknown>;
  eventAt: string;
  ingestedAt?: string | null;
  stockRelation?: StockResearchRelation | null;
};

export type WatchlistCard = {
  symbol: string;
  name: string;
  eventCount: number;
  highPriorityCount: number;
  opportunityScore: number;
  riskScore: number;
  perspectives: Record<string, number>;
  sentiment: Record<string, number>;
  latestQuote?: Record<string, unknown> | null;
  institutionRatingCount: number;
  latestRating?: string | null;
  latestEventAt?: string | null;
  todayEventCount?: number;
};

export type MonitoringSource = {
  sourceKey: string;
  name: string;
  category: string;
  provider: string;
  adapterType: string;
  enabled: boolean;
  pollIntervalSeconds: number;
  lastStatus?: string | null;
  lastError?: string | null;
  lastSuccessAt?: string | null;
  lastCheckAt?: string | null;
  lastCheckAgeSeconds?: number | null;
  nextCheckAt?: string | null;
  monitoringSlaSeconds?: number;
  monitoringStatus?: 'live' | 'delayed' | 'failed' | 'pending' | 'not_configured';
  upstreamState?: 'current' | 'quiet' | 'stale' | 'no_data';
  lastItemCount: number;
  lastReceivedCount?: number;
  lastCreatedCount?: number;
  lastUpdatedCount?: number;
  lastDurationMs?: number;
  totalItemCount: number;
  storedEventCount?: number;
  latestEventAt?: string | null;
  latestIngestedAt?: string | null;
  dataAgeSeconds?: number | null;
  freshnessStatus?: 'fresh' | 'stale' | 'empty';
  freshnessSlaSeconds?: number;
  dataState?: 'fresh' | 'stale' | 'empty' | 'not_configured';
  lastRunState?: 'received' | 'empty' | 'failed' | 'not_configured';
  config?: { evidenceLevel?: string; originApis?: string[]; [key: string]: unknown };
};

export type InvestmentMonitorDashboard = {
  days: number;
  generatedAt: string;
  watchlist: WatchlistCard[];
  summary: {
    eventCount: number;
    watchlistCount: number;
    highPriorityCount: number;
    bullishCount: number;
    bearishCount: number;
    activeSourceCount: number;
    factualCount: number;
    unverifiedCount: number;
    originalLinkCount: number;
    originalLinkCoverage: number;
  };
  perspectives: CountRow[];
  eventTypes: CountRow[];
  sourceActivity: CountRow[];
  evidenceLevels: CountRow[];
  channels: CountRow[];
  latestEvents: MonitorEvent[];
  highPriority: MonitorEvent[];
};

export type MonitorStatus = {
  worker: { running: boolean; pollSeconds: number; lastSyncAt?: string | null; lastError?: string | null };
  sources: SourceHealthSummary;
};

export type SourceHealthSummary = {
  items: MonitoringSource[]; total: number; healthy: number; enabled: number;
  operational?: number; notConfigured?: number;
  withData?: number; fresh?: number; stale?: number; empty?: number;
  monitoringLive?: number; monitoringDelayed?: number;
};

export type SourceBI = {
  days: number; generatedAt: string;
  summary: {
    total: number; healthy: number; operational: number; enabled: number; withData: number;
    fresh: number; stale: number; empty: number; notConfigured: number;
    monitoringLive: number; monitoringDelayed: number;
    storedEventCount: number; periodEventCount: number; lastRunReceived: number; lastRunCreated: number;
  };
  dailyTrend: Array<{ date: string; count: number }>;
  categories: CountRow[];
  providers: CountRow[];
  sources: Array<MonitoringSource & {
    periodEventCount: number;
    dailyActivity: Array<{ date: string; count: number }>;
    directUse: { eventsApi: string; syncApi: string; originApis: string[]; localStore: string };
  }>;
};

export type MonitorEventList = { items: MonitorEvent[]; total: number; page: number; pageSize: number; startDate?: string; endDate?: string };

export type IntelligenceDashboard = {
  days: number;
  generatedAt: string;
  summary: {
    eventCount: number; previousEventCount: number; eventChangePct: number; factualCount: number;
    highPriorityCount: number; watchlistHits: number; sourceCount: number;
  };
  dailyTrend: Array<{ date: string; total?: number; factual?: number; unverified?: number; highPriority?: number }>;
  channels: Array<{ name: string; count: number; previousCount: number; changePct: number; highPriority?: number; bullish?: number; bearish?: number }>;
  watchlist: WatchlistCard[];
  signalEvents: MonitorEvent[];
  pulse: MonitorEvent[];
  contradictions: Array<{ symbol: string; name: string; bullishCount: number; bearishCount: number; bullishEvidence: MonitorEvent; bearishEvidence: MonitorEvent }>;
  sources: SourceHealthSummary;
};

export type MonitorSymbolDetail = {
  symbol: string; name: string; scorecard: WatchlistCard;
  perspectives: Record<'investor' | 'company' | 'institution', MonitorEvent[]>;
  events: MonitorEvent[]; total: number;
};

export type EssayExpectationEstimate = {
  eventId?: number | null; topicId?: string | null; title?: string | null; eventAt?: string | null; proposedAt?: string | null;
  sourceKind?: 'related' | 'dedicated' | null;
  subject?: string | null; subjectRelation?: 'target_stock' | 'consolidated' | 'subsidiary' | 'acquisition_target' | 'business_segment' | null;
  metric: string; period: string; valueText: string; valueLow?: number | null; valueHigh?: number | null;
  unit?: string | null; direction?: string | null; evidence: string; confidence?: number | null;
};

export type EssayConsensusAnalysis = {
  status: 'not_loaded' | 'not_started' | 'pending' | 'processing' | 'completed' | 'failed' | 'stale';
  sourceCount: number; relatedSourceCount?: number; dedicatedSourceCount?: number;
  analyzedCount: number; pendingCount: number; model?: string | null; promptVersion?: string | null;
  summary: string; hasExplicitExpectations: boolean; profitOutlook: string; valuationOutlook: string;
  estimates: EssayExpectationEstimate[]; metricCounts: Record<string, number>;
  consensusPoints: string[]; conflicts: string[]; timeObservations?: string[]; caveats: string[];
  verificationConditions?: Array<{ condition: string; window: string; impact: string; expiryAt: string }>;
  sourceNotes: Array<{ topicId: string; eventId?: number | null; title?: string | null; eventAt?: string | null; authorName?: string | null; sourceKind?: 'related' | 'dedicated' | null; estimateCount: number }>;
  candidateSourceNotes?: EssayConsensusAnalysis['sourceNotes'];
  candidateCutoffAt?: string | null;
  hasReport?: boolean; reportSourceCount?: number | null; reportSourcesComplete?: boolean;
  analysisCutoffAt?: string | null;
  error?: string | null; updatedAt?: string | null; completedAt?: string | null;
};

export type FundamentalSourceKind = 'indicator' | 'income' | 'cashflow';
export type FundamentalSource = {
  eventId: number | null; source: string | null; period: string | null; announcedAt: string | null; reportType: string | null;
  selectionStatus: 'selected' | 'conflict'; candidateEventIds?: number[]; candidateCount?: number;
};

export type SuperWatchlistStock = {
  symbol: string; name: string;
  history: Array<{ date: string; open?: number | null; high?: number | null; low?: number | null; close?: number | null; volume?: number | null; amount?: number | null; pctChg?: number | null }>;
  market: { price?: number | null; changePct?: number | null; prevClose?: number | null; change?: number | null; quoteKind?: 'snapshot' | 'daily_close' | 'unavailable'; open?: number | null; high?: number | null; low?: number | null; amount?: number | null; updatedAt?: string | null; source?: string | null; isStale?: boolean; staleSeconds?: number | null };
  valuation: { asOf?: string | null; source?: string | null; sourceEventId?: number | null; pe?: number | null; peTtm?: number | null; pb?: number | null; psTtm?: number | null; totalMv?: number | null; turnoverRate?: number | null; volumeRatio?: number | null };
  technical: Record<string, number | string | null | undefined>;
  fundamentals: { period?: string | null; asOf?: string | null; source?: string | null; sourceEventId?: number | null; sources?: Partial<Record<FundamentalSourceKind, FundamentalSource>> | null; indicatorPeriod?: string | null; incomePeriod?: string | null; cashflowPeriod?: string | null; revenueYoyField?: string | null; netProfitYoyField?: string | null; revenue?: number | null; netProfit?: number | null; operatingCashflow?: number | null; revenueYoy?: number | null; netProfitYoy?: number | null; grossMargin?: number | null; netMargin?: number | null; roe?: number | null; debtRatio?: number | null; currentRatio?: number | null; eps?: number | null };
  capital: { chipTradeDate?: string | null; moneyflowTradeDate?: string | null; marginTradeDate?: string | null; northboundTradeDate?: string | null; source?: string | null; winnerRate?: number | null; weightedCost?: number | null; cost50Pct?: number | null; cost85Pct?: number | null; moneyflow: Record<string, unknown>; margin: Record<string, unknown>; northbound: Record<string, unknown>; chipDistribution: Array<{ price?: number; percent?: number }> };
  ownership: { pledge: Record<string, unknown>; shareUnlock: Array<Record<string, unknown>>; holderTrades: Array<Record<string, unknown>>; repurchases: Array<Record<string, unknown>> };
  institution: { researchCount: number; latest: MonitorEvent[]; institutions: CountRow[] };
  company: { profile: Record<string, unknown>; announcementCount: number; announcements: MonitorEvent[] };
  alternative: { essayCount: number; essays: MonitorEvent[]; catalysts: string[]; risks: string[] };
  consensus: {
    brokerReportCount: number;
    brokerSample?: {
      loaded: boolean; totalRecordCount: number | null; inputRecordCount: number; reportCount: number;
      loadedRecordCount?: number; relationExcludedCount?: number; unknownRelationCount?: number;
      validReportCount: number; duplicateRecordCount: number; sampleLimit: number | null; truncated: boolean | null;
      oldestAt?: string | null; asOf?: string | null; aggregation: string;
      institutionCount: number | null; knownInstitutionCount: number; unknownInstitutionReportCount: number;
      missingPeriodReportCount?: number;
      sourceNotes: Array<{ eventId: number; title: string; eventAt?: string | null; orgName?: string | null; stockRelation?: StockResearchRelation | null }>;
    };
    ratings: CountRow[];
    targetPrice: { sampleCount: number; min?: number | null; median?: number | null; max?: number | null;
      documentCount?: number; documentMin?: number | null; documentMedian?: number | null; documentMax?: number | null;
      endpointCount?: number; documentMethod?: string };
    forecasts: Array<{
      period: string; sampleCount: number;
      reportCount?: number; metricSampleCounts?: { eps: number; np: number; pe: number; opRt: number; roe: number };
      epsMedian?: number | null; epsMin?: number | null; epsMax?: number | null;
      peMedian?: number | null; npMedian?: number | null; opRtMedian?: number | null; roeMedian?: number | null;
      sourceEventIds?: number[];
      metricSourceEventIds?: Partial<Record<'eps' | 'np' | 'pe' | 'opRt' | 'roe', number[]>>;
      conflictingMetricCounts?: Partial<Record<'eps' | 'np' | 'pe' | 'opRt' | 'roe', number>>;
    }>;
    essayExpectationCount: number;
    essayExpectations: Array<EssayExpectationEstimate & { text: string }>;
    essayAnalysis: EssayConsensusAnalysis;
    asOf?: string | null; method: string;
  };
  messages: { count: number; items: MonitorEvent[]; channels: CountRow[] };
  stockComments: { count: number; items: MonitorEvent[]; sourceNote: string };
  signals: Array<{ kind: 'catalyst' | 'risk' | 'watch'; title: string; detail: string; eventId?: number | null; eventAt?: string | null; sourceName?: string | null }>;
  coverage: Array<{ name: string; count: number; latestAt?: string | null; available: boolean; freshnessStatus?: 'fresh' | 'stale' | 'empty'; lastSyncAt?: string | null; syncAgeSeconds?: number | null; targetRefreshSeconds?: number | null; sourceKeys?: string[] }>;
  evidence: { eventCount: number; rawEventCount: number; factualCount: number; unverifiedCount: number; sourceCount: number; originalLinkCount: number; originalLinkCoverage: number; channels: CountRow[] };
  timeline: MonitorEvent[];
  overview?: {
    recentEvidence: MonitorEvent[];
    sections: Array<{ key: 'announcements' | 'research' | 'opinions' | 'news'; label: string; items: MonitorEvent[]; latestAt: string | null }>;
    dataAsOf: { market: string | null; valuation: string | null; fundamentals: string | null; capital: string | null; technical: string | null };
    loadedSections: string[];
    evidenceCountsComplete: boolean;
  };
};

export type SuperWatchlistDashboard = {
  version: string; generatedAt: string; days: number; stocks: SuperWatchlistStock[];
  dataPolicy?: { market: string; evidence: string; refresh: string; pageFetchesUpstream: boolean };
  backfillJobs: WatchlistBackfillJob[];
  comparison: Array<Record<string, string | number | null | undefined>>;
  iterations: Array<{ version: string; name: string; result: string }>;
};

export type StockWorkspace = {
  version: string;
  generatedAt: string;
  days: number;
  stock: SuperWatchlistStock;
  view?: 'overview' | 'full';
  agentContext?: {
    analysisContextPackSummary?: string;
    realtimeQuote?: Record<string, unknown>;
    chipDistribution?: Record<string, unknown>;
    newsContext?: string;
    fundamentalContext?: Record<string, unknown>;
    evidenceCount?: number;
    sourceCount?: number;
  };
  dataPolicy: {
    facts: string;
    upstreamFetchOnRead: boolean;
    failureMode: string;
    quotePrecedence: string;
  };
  cache: { hit: boolean; ttlSeconds: number; state?: 'fresh' | 'stale-refreshing' | 'rebuilt' | 'superseded' };
  iterations: Array<{ version: string; name: string; result: string }>;
};

export type ResearchDecisionPacket = {
  symbol: string; name: string; state: '可进入研究' | '需要补证' | '数据不足'; readinessScore: number;
  scoreComponents: Array<{ name: string; score: number; weight: number }>;
  market: SuperWatchlistStock['market']; evidence: SuperWatchlistStock['evidence'];
  latestEvidenceAt?: string | null; changes: MonitorEvent[];
  agreement: { bullishFacts: number; bearishFacts: number; conflict: boolean };
  expectations: { brokerReports: number; essayEstimates: number; asOf?: string | null; method?: string | null };
  invalidationEvidence: SuperWatchlistStock['signals'];
  verificationTasks: Array<{ priority: string; task: string; reason: string }>;
  coverage: SuperWatchlistStock['coverage']; disclaimer: string;
};

export type ResearchCenterOverview = {
  version: string; generatedAt: string;
  iterations: Array<{ version: string; name: string; result: string }>;
  system: { sourceCount: number; storedEventCount: number; freshSourceCount: number; liveMonitorCount: number; attentionSourceCount: number; watchlistCount: number };
  decisionPackets: ResearchDecisionPacket[];
  functions: Array<{ name: string; route: string; purpose: string; data: string[]; output: string }>;
  architecture: Array<{ layer: string; logic: string }>;
  dataSources: SourceBI['sources']; principles: string[];
  decisionUses: Array<{ name: string; value: string }>;
  reflection: Array<{ gap: string; impact: string; upgrade: string }>;
};

export type WatchlistBackfillJob = {
  id: number; symbol: string; stockName?: string | null; days: number;
  status: 'pending' | 'running' | 'completed' | 'partial' | 'failed'; progress: number;
  channels: Record<string, { status: string; created?: number; updated?: number; received?: number; error?: string | null; note?: string }>;
  error?: string | null; requestedAt?: string | null; startedAt?: string | null; completedAt?: string | null;
};

export type AnnouncementCategory = { code: string; name: string };
export type AnnouncementCategoryList = { items: AnnouncementCategory[]; total: number };
export type AnnouncementSyncRequest = {
  startDate: string;
  endDate: string;
  symbols?: string[];
  categories?: string[];
  keyword?: string;
  maxPages?: number;
};

export type CloudKnowledgeStatus = {
  storage: {
    available: boolean;
    enabled: boolean;
    cloudDir: string;
    retention: number;
    snapshotCount: number;
    latest?: { filename: string; createdAt: string; sizeBytes: number; tables: Record<string, number> } | null;
    mode: string;
    multiDeviceWrites: boolean;
  };
  worker: { running: boolean; intervalSeconds: number; lastError?: string | null };
  snapshots: Array<{ filename: string; createdAt: string; sizeBytes: number; present: boolean }>;
};

export type DragonTigerSeat = {
  eventId?: number | null; tradeDate: string; tsCode: string; exalter: string;
  buy?: number | null; buyRate?: number | null; sell?: number | null; sellRate?: number | null;
  netBuy?: number | null; side: string; reason: string;
};

export type DragonTigerRecord = {
  eventId?: number | null; tradeDate: string; tsCode: string; name: string;
  close?: number | null; pctChange?: number | null; turnoverRate?: number | null;
  amount?: number | null; lSell?: number | null; lBuy?: number | null;
  lAmount?: number | null; netAmount?: number | null; netRate?: number | null;
  amountRate?: number | null; floatValues?: number | null; reason: string;
  seats?: DragonTigerSeat[];
};

export type DragonTigerDaily = {
  tradeDate: string; generatedAt: string;
  source: { provider: string; apis: string[]; fetched: boolean; amountUnit: string; updateNote: string };
  summary: {
    rowCount: number; symbolCount: number; seatCount: number;
    positiveCount: number; negativeCount: number;
    netAmount: number; buyAmount: number; sellAmount: number;
  };
  items: DragonTigerRecord[];
};

export type DragonTigerHistory = {
  items: DragonTigerRecord[]; total: number; page: number; pageSize: number;
  startDate: string; endDate: string; cachedTradeDays: number;
  trend: Array<{ tradeDate: string; rows: number; symbols: number; netAmount: number }>;
};

export type DragonTigerSyncResult = {
  startDate: string; endDate: string; tradeDays: number;
  topListCount: number; created: number; updated: number; dates: string[];
};
