export type EssayProgress = {
  totalNotes: number;
  queuedNotes: number;
  completed: number;
  pending: number;
  processing: number;
  failed: number;
  mediaOnly?: number;
  aiEligibleNotes?: number;
  coveragePercent: number;
  model: string;
  deepseekConfigured: boolean;
};

export type EssayWorkerStatus = {
  running: boolean;
  batchSize: number;
  concurrency: number;
  pollSeconds: number;
  lastBatchAt?: string | null;
  lastError?: string | null;
};
export type ResearchNoteDetail = {
  topicId: string; groupId: string; groupName: string; title: string;
  content?: string | null; authorId?: string | null; authorName?: string | null;
  topicType: string; textType?: string | null; digested: boolean; sticky: boolean;
  symbols: string[];
  files: Array<{ fileId?: string; name?: string; size?: number; duration?: number; durationSeconds?: number; assetKind?: 'audio' | 'file'; aiEligible?: boolean; viewUrl?: string; downloadUrl?: string; downloadStatus?: string }>;
  images: Array<{ imageId?: string; type?: string; viewUrl?: string; downloadStatus?: string; thumbnail?: { url?: string }; large?: { url?: string } }>;
  assetSummary?: { audioCount?: number; fileCount?: number; imageCount?: number; hasAudio?: boolean; hasFiles?: boolean; hasImages?: boolean; audioNames?: string[]; fileNames?: string[] };
  aiEligible?: boolean;
  counts: Record<string, unknown>; createdAt?: string | null; modifiedAt?: string | null; syncedAt?: string | null;
};
export type EssayHistoricalBacklog = {
  totalNotes: number;
  queuedNotes: number;
  completed: number;
  pending: number;
  processing: number;
  failed: number;
  mediaOnly?: number;
  aiEligibleNotes?: number;
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
    files: Array<{ fileId?: string; name?: string; size?: number; duration?: number; durationSeconds?: number; assetKind?: 'audio' | 'file'; aiEligible?: boolean; viewUrl?: string; downloadUrl?: string; downloadStatus?: string }>;
    images: Array<{ imageId?: string; type?: string; viewUrl?: string; downloadStatus?: string; thumbnail?: { url?: string }; large?: { url?: string } }>;
    assetSummary?: { audioCount?: number; fileCount?: number; imageCount?: number; hasAudio?: boolean; hasFiles?: boolean; hasImages?: boolean; audioNames?: string[]; fileNames?: string[] };
    aiEligible?: boolean;
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

export type EssayAudioFile = {
  assetId: string;
  topicId: string;
  fileId: string;
  name: string;
  size?: number | null;
  durationSeconds?: number | null;
  downloadUrl?: string | null;
  groupName: string;
  authorName?: string | null;
  noteTitle: string;
  createdAt?: string | null;
};

export type EssayAudioFileList = {
  items: EssayAudioFile[];
  total: number;
  page: number;
  pageSize: number;
};

export type EssayAudioBatchTask = {
  taskId: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: 'queued' | 'preparing' | 'downloading' | 'packaging' | 'completed' | 'failed' | 'interrupted';
  progress: number;
  message: string;
  totalFiles: number;
  completedFiles: number;
  currentFilename?: string | null;
  downloadedBytes: number;
  totalBytes: number;
  archiveBytes: number;
  downloadUrl?: string | null;
  downloadName?: string | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
};

export type EssayAudioDownloadProgress = {
  loaded: number;
  total?: number;
  percent?: number;
};

export type EssayAudioAnalysisCapability = {
  configured: boolean;
  transcriptionConfigured: boolean;
  analysisConfigured: boolean;
  transcriptionProvider?: string;
  maxFiles: number;
  maxFileMb: number;
  message: string;
};

export type EssayAudioMemoResult = {
  title: string;
  executiveSummary?: string;
  meetingContext?: string | string[];
  keyConclusions?: string[];
  industryChain?: string[];
  companyMentions?: Array<{ name?: string; tsCode?: string; evidence?: string; view?: string }>;
  financialForecasts?: Array<{ subject?: string; period?: string; metric?: string; value?: string; evidence?: string }>;
  catalysts?: string[];
  risks?: string[];
  disagreements?: string[];
  followUps?: string[];
  transcriptQuality?: string;
  evidenceIndex?: Array<{ claim?: string; sourceFile?: string; timestamp?: string; speaker?: string; category?: string; confidence?: number }>;
  speakerViews?: Array<{ speaker?: string; summary?: string; keyPoints?: string[] }>;
  monitoringItems?: Array<{ item?: string; metric?: string; timeWindow?: string; trigger?: string; evidence?: string }>;
  generatedAt?: string;
  model?: string;
  sourceFiles?: Array<{ filename?: string; topicId?: string; fileId?: string; noteTitle?: string; createdAt?: string }>;
  libraryTopicId?: string | null;
  indexed?: boolean;
};

export type EssayAudioTranscript = {
  fileId: string;
  filename?: string;
  text: string;
  lines: Array<{ timestamp: string; speaker: string; text: string }>;
};

export type EssayAudioAnalysisTask = {
  taskId: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: 'queued' | 'resuming' | 'downloading' | 'transcribing' | 'analyzing' | 'completed' | 'failed' | 'interrupted';
  progress: number;
  message: string;
  title?: string;
  focus?: string;
  hotwords?: string[];
  speakerCount?: number | null;
  retryCount?: number;
  totalFiles: number;
  completedFiles: number;
  currentFilename?: string | null;
  transcriptArtifacts?: Array<{ fileId: string; topicId?: string; filename?: string; lineCount?: number }>;
  indexed?: boolean;
  libraryTopicId?: string | null;
  result?: EssayAudioMemoResult | null;
  downloadUrls?: Partial<Record<'zip' | 'md' | 'docx' | 'json', string>>;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
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
    executiveSummary?: string; marketRegime?: string; marketNarrative?: string;
    keyThemes?: Array<{
      name: string; count: number; direction: string; thesis: string;
      evidence?: string; counterEvidence?: string; evidenceTopicIds?: string[];
    }>;
    stockFocus?: Array<{
      tsCode: string; name: string; mentionCount: number; stance: string; conviction?: string;
      timeHorizon?: string; thesis: string; whyNow?: string; earningsPath?: string; valuationView?: string;
      catalysts: string[]; risks: string[]; validationPoints?: string[]; evidenceTopicIds?: string[];
    }>;
    consensus?: string[]; divergences?: string[]; novelSignals?: string[];
    earningsImplications?: string[]; valuationImplications?: string[]; riskWatch?: string[]; nextDayWatchlist?: string[];
    dataQuality?: { coverage?: string | number; limitations?: string[]; recommendationRule?: string; [key: string]: unknown };
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
  stage: 'sources' | 'themes' | 'stocks' | 'outcomes';
  key: string; label: string; count: number; kind?: 'positive' | 'negative' | 'neutral';
  tsCode?: string; stockName?: string;
};

export type EssayMarketMetric = {
  period: number; sampleCount: number; averageReturn?: number | null; medianReturn?: number | null;
  winRate?: number | null; averageExcessReturn?: number | null; excessWinRate?: number | null;
  confidenceInterval95: [number | null, number | null];
};

export type EssayMarketImpactItem = {
  key: string; tsCode: string; name: string; mentionCount: number; eventDayCount: number;
  coveredEventDays: number; metrics: EssayMarketMetric[];
  leadLag: Array<{ lagSessions: number; correlation?: number | null; sampleCount: number }>;
  attentionComparison: Array<{
    level: string; threshold: number; sampleCount: number;
    averageReturn5D?: number | null; winRate5D?: number | null;
  }>;
  series: Array<{ date: string; close: number; priceReturn: number; dailyReturn: number; mentionCount: number }>;
  insight: string; latestPriceDate?: string | null; latestClose?: number | null; dataSource: string;
};

export type EssayDeepInsights = {
  generatedAt: string; windowDays: number; latestDataAt?: string | null;
  period: {
    horizon: 'short' | 'medium' | 'long' | 'custom'; startDate: string; endDate: string;
    granularity: 'day' | 'week' | 'month'; comparisonDays: number;
  };
  summary: {
    analyzedCount: number; sourceCount: number; themeCount: number; stockCount: number;
    evidenceCoveragePercent: number; highNoveltyCount: number; divergenceCount: number;
  };
  pulse: Array<{ date: string; total: number; bullish: number; bearish: number; neutral: number; mixed: number }>;
  layers: {
    sources: EssayInsightNode[]; themes: EssayInsightNode[]; stocks: EssayInsightNode[]; outcomes: EssayInsightNode[];
    edges: Array<{ fromStage: string; from: string; toStage: string; to: string; count: number }>;
  };
  themeHeatmap: {
    dates: string[];
    items: Array<{
      name: string; total: number; aliases?: Array<{ name: string; count: number }>;
      points: Array<{
        date: string; count: number; dailyTotal?: number;
        sharePercent?: number; concentrationScore?: number;
      }>;
    }>;
    taxonomy?: {
      version: string; rawThemeCount: number; canonicalThemeCount: number;
      mergedThemeCount: number; method: string;
    };
    granularity?: 'day' | 'week' | 'month';
  };
  marketImpact: {
    benchmark: string; entryRule: string; exitRule: string; priceBasis: string; dedupeRule: string; causalityNote: string;
    coverage: {
      candidateStockCount: number; pricedStockCount: number; eventDayCount: number;
      coveredEventDayCount: number; eventCoveragePercent: number; benchmarkAvailable: boolean;
      priceStart?: string | null; priceEnd?: string | null; sources: string[];
    };
    items: EssayMarketImpactItem[];
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
