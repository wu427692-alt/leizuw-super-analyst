export type EssayQuantRule = {
  id?: number; name: string; sourceQuery: string; signalDirection: 'bullish' | 'bearish' | 'all';
  lookbackDays: number; holdingPeriods: number[]; firstMentionOnly: boolean;
  firstMentionWindowDays: number; minImportance: number; minConfidence: number;
  benchmarkCode: string; portfolioSize: number; enabled?: boolean;
  strategyType: string; rawNotePolicy: 'exclude' | 'include'; dedupeWindowDays: number;
  transactionCostBps: number; validationMethod: 'walk_forward' | 'time_split' | 'none';
};

export type QuantMetric = { period: number; sampleCount: number; winRate?: number | null; averageReturn?: number | null; medianReturn?: number | null };
export type QuantEvent = {
  topicId: string; symbol: string; stockName: string; stance: string; eventAt: string; title: string;
  summary?: string; sourceGroup: string; researchGroup: string; importanceScore: number;
  confidenceScore: number; noveltyScore: number; hypeScore: number; firstMention: boolean;
  entryDate?: string | null; entryPrice?: number | null; returns: Record<string, number>;
  excessReturns: Record<string, number>; maturePeriods: number[]; url: string; rationale?: string;
  methodScore?: number; trendReady?: boolean; trendAligned?: boolean; preEventMa5?: number | null; preEventMa20?: number | null;
};
export type EssayQuantMethod = {
  key: string; name: string; purpose: string; usedData: string[]; engine: string; output: string;
  template: Partial<EssayQuantRule>;
};
export type EssayQuantMethodAnalysis = {
  strategyType: string; name: string; purpose: string; usedData: string[]; engine: string; output: string;
  selectionRule: string; sourceEventCount: number; selectedEventCount: number;
  diagnostics: Array<{ label: string; value?: string | number | null; note: string }>;
};
export type EssayQuantDashboard = {
  runId?: number; ruleId?: number; snapshotHash?: string; generatedAt: string; rule: EssayQuantRule;
  summary: { eventCount: number; matureEventCount: number; coveredStockCount: number; firstMention30dCount: number; metrics: QuantMetric[]; excessMetrics: QuantMetric[] };
  eventCurve: Array<{ day: number; strategy?: number | null; benchmark?: number | null; sampleCount: number }>;
  researchGroupRankings: Array<{ researchGroup: string; eventCount: number; matureCount: number; rankEligible?: boolean; winRate: number; adjustedWinRate: number; averageReturn: number; averageExcessReturn?: number | null; score: number }>;
  firstMentions30d: QuantEvent[];
  hypeAnalysis: Array<{ level: string; eventCount: number; averageReturn?: number | null; winRate?: number | null }>;
  trendSignals: Array<{ symbol: string; stockName: string; researchGroup: string; eventAt: string; signalStrength: number; trend: string; ma5: number; ma20: number; momentum20d: number; trigger: string; url: string }>;
  portfolio: { components: Array<{ symbol: string; stockName: string; researchGroup: string; weight: number; trigger: string }>; curve: Array<{ date: string; value: number }>; annualizedReturn?: number | null; maxDrawdown?: number | null; winRate?: number | null };
  robustness: { sampleCount: number; averageExcessReturn?: number | null; confidenceInterval95: [number | null, number | null]; tStat?: number | null; payoffRatio?: number | null; positiveRate?: number | null; distribution: Array<{ range: string; midpoint: number; count: number }>; cohorts: Array<{ period: string; sampleCount: number; averageExcessReturn: number; winRate: number }>; sensitivity: Array<{ label: string; transactionCostBps: number; averageExcessReturn: number }>; validation?: { method: string; trainSampleCount: number; testSampleCount: number; trainAverageExcessReturn?: number | null; testAverageExcessReturn?: number | null; splitDate?: string | null; walkForwardFolds: Array<{ fold: number; startAt: string; endAt: string; sampleCount: number; averageExcessReturn: number }> }; outOfSampleNote?: string };
  factorAnalysis: Array<{ factor: string; label: string; highLowSpread?: number | null; buckets: Array<{ bucket: string; sampleCount: number; averageExcessReturn?: number | null; winRate?: number | null }> }>;
  events: QuantEvent[];
  methodAnalysis?: EssayQuantMethodAnalysis;
  dataQuality: { essaySource: string; priceSource: string; priceBasis: string; priceCutoff?: string | null; priceTargetDate?: string | null; priceLatestDate?: string | null; priceOldestSymbolDate?: string | null; currentPriceSymbolCount?: number; stalePriceSymbolCount?: number; unpricedSymbolCount?: number; priceFreshnessRatio?: number; freshnessStatus?: 'fresh' | 'partial' | 'stale'; entryRule: string; exitRule: string; benchmark: string; survivorshipNote: string; rankingNote?: string; warnings: string[]; notesScanned?: number; analyzedNoteCount?: number; rawNoteCount?: number; resolvedNoteCount?: number; unresolvedNoteCount?: number; researchGroupCount?: number; resolvedSymbolCount?: number; pricedSymbolCount?: number; priceRefreshSymbolCount?: number; rawUnanalyzedEventCount?: number; invalidSymbolMentionsFiltered?: number; duplicateEventCount?: number; rawNotePolicy?: string; transactionCostBps?: number; validationMethod?: string };
};

export type EssayQuantCatalog = {
  generatedAt: string;
  assets: Array<{ key: string; name: string; count: number; latestAt?: string | null; usage: string; status: 'ready' | 'empty' | 'not_ready' }>;
  methods: EssayQuantMethod[];
  safeguards: string[];
};

export type EssayQuantRunHistory = { items: Array<{
  id: number; name: string; strategyType: string; eventCount: number; matureEventCount: number;
  priceCutoff?: string | null; primaryAverageExcess?: number | null;
  outOfSampleExcess?: number | null; confidenceInterval?: [number | null, number | null];
  maxDrawdown?: number | null; verdict?: string | null; createdAt?: string | null;
}>; total: number };

export type EssayQuantTask = {
  taskId: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: number;
  message: string;
  name: string;
  strategyType: string;
  resultRunId?: number | null;
  error?: string | null;
  createdAt?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
};

export type EssayQuantTaskList = { items: EssayQuantTask[]; total: number };

export type EssayQuantPlan = {
  prompt: string;
  plan: { title: string; hypothesis: string; universe: string; signalSources: string[]; assumptions: string[]; unsupportedRequests: string[] };
  rule: EssayQuantRule;
  code: string;
  safety: { mode: string; canExecute: boolean; allowedOperations: string[]; blockedOperations: string[]; confirmationRequired: boolean };
};

export type EssayQuantPrecomputeStatus = {
  running: boolean; computing: boolean; dirty: boolean; reason?: string; minIntervalSeconds?: number;
  lastStartedAt?: string | null; lastCompletedAt?: string | null; lastPriceRefreshAt?: string | null;
  lastError?: string | null; lastResult?: { runId?: number; eventCount?: number; matureEventCount?: number; rankedGroupCount?: number; generatedAt?: string; resolvedSymbolCount?: number; pricedSymbolCount?: number; currentPriceSymbolCount?: number; stalePriceSymbolCount?: number; priceFreshnessRatio?: number; priceTargetDate?: string | null } | null;
};
