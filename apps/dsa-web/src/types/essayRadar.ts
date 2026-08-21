export type EssayProgress = {
  totalNotes: number;
  queuedNotes: number;
  completed: number;
  pending: number;
  processing: number;
  failed: number;
  coveragePercent: number;
  model: string;
  deepseekConfigured: boolean;
};

export type EssayWorkerStatus = {
  running: boolean;
  batchSize: number;
  concurrency: number;
  lastBatchAt?: string | null;
  lastError?: string | null;
};
export type ResearchNoteDetail = {
  topicId: string; groupId: string; groupName: string; title: string;
  content?: string | null; authorId?: string | null; authorName?: string | null;
  topicType: string; textType?: string | null; digested: boolean; sticky: boolean;
  symbols: string[];
  files: Array<{ fileId?: string; name?: string; size?: number; viewUrl?: string; downloadStatus?: string }>;
  images: Array<{ imageId?: string; type?: string; viewUrl?: string; downloadStatus?: string; thumbnail?: { url?: string }; large?: { url?: string } }>;
  counts: Record<string, unknown>; createdAt?: string | null; modifiedAt?: string | null; syncedAt?: string | null;
};
export type EssayHistoricalBacklog = {
  totalNotes: number;
  queuedNotes: number;
  completed: number;
  pending: number;
  processing: number;
  failed: number;
  coveragePercent: number;
  unqueued: number;
  earliestUnqueuedAt?: string | null;
  latestUnqueuedAt?: string | null;
  earliestNoteAt?: string | null;
  latestNoteAt?: string | null;
  latestSyncedAt?: string | null;
  groupCount: number;
  notes24h: number;
  notes7d: number;
  notes30d: number;
};
export type EssayCountBackfillResponse = {
  queue: {
    requested: number; selected: number; order: 'newest' | 'oldest';
    earliestSelectedAt?: string | null; latestSelectedAt?: string | null;
    created: number; reset: number; unchanged: number;
  };
  backlog: EssayHistoricalBacklog;
  worker: EssayWorkerStatus;
};
export type EssayDailyReportWorkerStatus = {
  running: boolean; pollSeconds: number; runHourShanghai: number; models: string[];
  lastRunAt?: string | null; lastResult?: Record<string, unknown> | null; lastError?: string | null;
};

export type ZsxqSyncGroup = {
  groupId: string; groupName: string; lastTopicId?: string | null; lastTopicAt?: string | null;
  lastAttemptAt?: string | null; lastSuccessAt?: string | null; lastStatus: string;
  lastError?: string | null; lastReceived: number; lastSaved: number; lastMediaDownloaded: number; totalSaved: number;
};
export type ZsxqSyncStatus = {
  running: boolean; syncing: boolean; available: boolean; pollSeconds: number; lastSyncAt?: string | null;
  lastError?: string | null; lastResult?: Record<string, number> | null; groups: ZsxqSyncGroup[];
  mediaStorage: 'remote_only' | string; mode: string;
  historyBackfill?: {
    running: boolean; lookbackDays?: number | null; analysisEnqueued: boolean;
    startedAt?: string | null; finishedAt?: string | null;
    result?: Record<string, number> | null; error?: string | null;
    phase?: 'idle' | 'waiting_for_incremental_sync' | 'connecting' | 'discovering_groups' | 'fetching' | 'saving' | 'retry_wait' | 'finalizing' | 'completed' | 'incomplete' | 'failed';
    progressPercent?: number; groupsTotal?: number; groupsCompleted?: number;
    currentGroupId?: string | null; currentGroupName?: string | null;
    pagesFetched?: number; received?: number; created?: number; updated?: number; unchanged?: number;
    message?: string | null; oldestAt?: string | null; groupSaved?: number; lastProgressAt?: string | null;
    retryAttempt?: number; retryInSeconds?: number;
  };
};
export type EssayStatus = { progress: EssayProgress; worker: EssayWorkerStatus; mcpSync: ZsxqSyncStatus; dailyReportWorker: EssayDailyReportWorkerStatus };
export type CountRow = { name: string; count: number };

export type StockMention = {
  tsCode: string;
  name: string;
  stance: string;
  confidence: number;
  rationale: string;
};

export type EssayAnalysis = {
  topicId: string;
  status: string;
  summary?: string | null;
  primaryCategory?: string | null;
  sentiment?: string | null;
  timeHorizon?: string | null;
  importanceScore?: number | null;
  confidenceScore?: number | null;
  tags: string[];
  industries: string[];
  themes: string[];
  stockMentions: StockMention[];
  keyPoints: string[];
  catalysts: string[];
  risks: string[];
  evidence: Array<{ claim: string; evidence: string; strength: string }>;
  contradictions: string[];
  falsificationConditions: string[];
  monitoringPoints: string[];
  earningsImpact: string;
  valuationImpact: string;
  sourceQuality: string;
  noveltyScore: number;
  informationType: string;
  errorMessage?: string | null;
  completedAt?: string | null;
  note: {
    title: string;
    content?: string | null;
    groupName: string;
    authorName?: string | null;
    createdAt?: string | null;
    files: Array<{ fileId?: string; name?: string; size?: number; viewUrl?: string; downloadStatus?: string }>;
    images: Array<{ imageId?: string; type?: string; viewUrl?: string; downloadStatus?: string; thumbnail?: { url?: string }; large?: { url?: string } }>;
  };
};

export type EssayDashboard = {
  days: number;
  generatedAt: string;
  summary: {
    analyzedCount: number;
    averageImportance: number;
    stockCount: number;
    tagCount: number;
    totalTokens: number;
  };
  sentiment: CountRow[];
  categories: CountRow[];
  topTags: CountRow[];
  topIndustries: CountRow[];
  topThemes: CountRow[];
  topStocks: Array<{
    key: string;
    tsCode: string;
    name: string;
    mentionCount: number;
    bullish: number;
    bearish: number;
    neutral: number;
    watching: number;
    averageImportance: number;
  }>;
  highlights: EssayAnalysis[];
};

export type EssayAnalysisList = {
  items: EssayAnalysis[];
  total: number;
  page: number;
  pageSize: number;
  scope?: string;
};

export type EssayWordCloud = {
  period: 'day' | 'week' | 'month'; kind: 'stocks' | 'tags' | 'themes'; stock?: string | null;
  startDate: string; endDate: string; sourceCount: number;
  items: Array<{ name: string; count: number; previousCount: number; change: number; tsCode?: string; bullish?: number; bearish?: number }>;
};

export type EssayDailyReport = {
  id: number; reportDate: string; model: string; status: string; sourceCount: number; totalTokens: number;
  errorMessage?: string | null; completedAt?: string | null;
  report?: {
    executiveSummary?: string; marketRegime?: string;
    keyThemes?: Array<{ name: string; count: number; direction: string; thesis: string }>;
    stockFocus?: Array<{ tsCode: string; name: string; mentionCount: number; stance: string; thesis: string; catalysts: string[]; risks: string[] }>;
    consensus?: string[]; divergences?: string[]; novelSignals?: string[];
    earningsImplications?: string[]; valuationImplications?: string[]; riskWatch?: string[]; nextDayWatchlist?: string[];
  } | null;
};

export type EssayDailyReportList = { items: EssayDailyReport[]; models: string[]; total: number };

export type EssayTrendPoint = {
  date: string; total: number; bullish: number; bearish: number; neutral: number; mixed: number;
  averageImportance: number; averageConfidence: number;
};

export type EssayWatchlistInsight = {
  symbol: string; name: string; mentionCount: number; dayMentions: number; weekMentions: number; monthMentions: number;
  stances: Record<string, number>; averageImportance: number; averageConfidence: number;
  latestAt?: string | null; latestThesis?: string | null; catalysts: string[]; risks: string[];
  trend: Array<{ date: string; total?: number; bullish?: number; bearish?: number; neutral?: number; watching?: number }>;
  latestItems: EssayAnalysis[];
};

export type EssayInsights = {
  generatedAt: string; windowDays: number; latestDataAt?: string | null;
  yesterday: { date: string; analyzedCount: number; highImportanceCount: number; lowConfidenceCount: number; rumorCount: number; evidenceCoveragePercent: number };
  coverage: { analyzedCount: number; evidenceRecords: number; evidenceCoveragePercent: number; configuredModels: string[]; completedReportModels: number };
  trend: EssayTrendPoint[]; sourceQuality: CountRow[]; informationTypes: CountRow[]; sourceMix: CountRow[];
  modelComparison: {
    reportDate?: string | null; reports: EssayDailyReport[];
    consensus: Array<{ text: string; modelCount: number }>;
    divergences: Array<{ text: string; modelCount: number }>;
  };
  watchlist: EssayWatchlistInsight[]; highNoveltySignals: EssayAnalysis[];
};

export type EssayInsightNode = {
  stage: 'sources' | 'themes' | 'stocks' | 'signals';
  key: string; label: string; count: number; kind?: 'catalyst' | 'risk'; tsCode?: string;
};

export type EssayDeepInsights = {
  generatedAt: string; windowDays: number; latestDataAt?: string | null;
  summary: {
    analyzedCount: number; sourceCount: number; themeCount: number; stockCount: number;
    evidenceCoveragePercent: number; highNoveltyCount: number; divergenceCount: number;
  };
  pulse: Array<{ date: string; total: number; bullish: number; bearish: number; neutral: number; mixed: number }>;
  layers: {
    sources: EssayInsightNode[]; themes: EssayInsightNode[]; stocks: EssayInsightNode[]; signals: EssayInsightNode[];
    edges: Array<{ fromStage: string; from: string; toStage: string; to: string; count: number }>;
  };
  themeHeatmap: {
    dates: string[];
    items: Array<{ name: string; total: number; points: Array<{ date: string; count: number }> }>;
  };
  stockMomentum: Array<{
    tsCode: string; name: string; currentCount: number; previousCount: number;
    bullish: number; bearish: number; neutral: number; watching: number;
    latestAt?: string | null; latestThesis?: string | null; change: number;
    changePercent: number; averageImportance: number;
  }>;
  divergence: Array<{
    key: string; tsCode: string; name: string; bullish: number; bearish: number;
    neutral: number; total: number; divergenceScore: number;
  }>;
  verificationQueue: EssayAnalysis[];
  evidenceFunnel: Array<{ name: string; count: number }>;
};
