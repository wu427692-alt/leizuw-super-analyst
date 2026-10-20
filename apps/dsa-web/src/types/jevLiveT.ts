export type LiveTRunStatus = 'starting' | 'running' | 'paused' | 'completed' | 'stopped' | 'failed';

export interface LiveTConfig {
  symbol: string;
  name?: string;
  currency: string;
  settlement: string;
  initialBaseValue: number;
  initialCash: number;
  quotePollIntervalMs?: number;
  decisionIntervalSeconds?: number;
  durationSeconds?: number | null;
  maxModelRequests?: number | null;
  maxCostUsd?: number;
  confidenceSizing?: boolean;
  confidenceThreshold?: number | null;
  lotSize?: number;
  orderQuantity?: number;
}
export interface LiveTQuote {
  marketTs?: string | null;
  receivedAt?: string | null;
  ageMs?: number | null;
  source?: string | null;
  price?: number | null;
  volume?: number | null;
  volumeUnit?: string | null;
  observedUpdateIntervalMs?: number | null;
  stale?: boolean;
  duplicateCount?: number;
}
export interface LiveTAccount {
  initialEquity?: number | null;
  currentEquity?: number | null;
  holdBaselineEquity?: number | null;
  totalPnl?: number | null;
  incrementalPnl?: number | null;
  initialShares?: number | null;
  totalShares?: number | null;
  sellableYesterdayShares?: number | null;
  todayFrozenShares?: number | null;
  cash?: number | null;
  inventoryGapShares?: number | null;
  closedTNetPnl?: number | null;
  costReductionPerInitialShare?: number | null;
  totalFees?: number | null;
  commission?: number | null;
  sellTax?: number | null;
  transferFee?: number | null;
  slippageCost?: number | null;
  actualCostBasis?: number | null;
}
export interface LiveTEquityPoint {
  timestamp: string;
  equity: number;
  holdBaselineEquity: number;
  incrementalPnl?: number | null;
}
export interface LiveTSignal {
  marketTs: string;
  receivedAt?: string;
  action: string;
  confidence?: number | null;
  requestedQuantity?: number | null;
  reason?: string;
  executionStatus?: string;
}
export interface LiveTTrade {
  timestamp: string;
  marketTs?: string;
  action: string;
  shares: number;
  price: number;
  fee?: number | null;
  transferFee?: number | null;
  cashAfter?: number | null;
  sellableAfter?: number | null;
  frozenAfter?: number | null;
  reason?: string;
}
export interface LiveTRunSummary {
  runId: string;
  status: LiveTRunStatus;
  message?: string;
  createdAt: string;
  updatedAt: string;
  config: LiveTConfig;
}
export type LiveTContextStatus = 'fresh' | 'stale' | 'missing' | 'error';
export interface LiveTFactorContext {
  status: LiveTContextStatus;
  source: string;
  sourceTime: string | null;
  availableAt: string | null;
  ageSeconds: number | null;
  values: {
    returnSincePrevClose?: number | null;
    return30S?: number | null;
    return60S?: number | null;
    volumeToMean?: number | null;
    breadthUp?: number | null;
    relativeReturn?: number | null;
    realizedVolatility?: number | null;
    meanSpreadBps?: number | null;
    memberCount?: number | null;
    coverageRatio?: number | null;
    amountGrowth?: number | null;
  };
}
export interface LiveTNewsItem {
  title: string;
  summary: string;
  source: string;
  url: string | null;
  publishedAt: string | null;
  availableAt: string | null;
}
export interface LiveTNewsContext {
  status: LiveTContextStatus;
  source: string;
  fetchedAt: string | null;
  items: LiveTNewsItem[];
}
export interface LiveTContext {
  hs300: LiveTFactorContext;
  zz1000: LiveTFactorContext;
  cpo: LiveTFactorContext;
  news: LiveTNewsContext;
}
export interface LiveTContextDiagnostic {
  label: string;
  reason: string;
  sourceKind: string;
}
export interface LiveTRun extends LiveTRunSummary {
  startedAt?: string | null;
  stoppedAt?: string | null;
  stopReason?: string | null;
  quote?: LiveTQuote | null;
  account?: LiveTAccount | null;
  usage: {
    requests: number;
    cached: number;
    costUsd: number;
    costIsEstimate?: boolean;
    lastLatencyMs?: number | null;
    medianLatencyMs?: number | null;
    observedDecisionIntervalMs?: number | null;
    lastModelRequestAt?: string | null;
    lastModelResponseAt?: string | null;
  };
  equity: LiveTEquityPoint[];
  signals: LiveTSignal[];
  trades: LiveTTrade[];
  warnings: string[];
  methodology: string[];
  context?: LiveTContext | null;
  lastModelContext?: { decisionAt: string; context: LiveTContext } | null;
  contextDiagnostics?: Record<string, LiveTContextDiagnostic> | null;
}
export interface LiveTStatus {
  available: boolean;
  message: string;
  serverTime?: string;
  pollIntervalMs?: number;
  currentRun?: LiveTRun | null;
}

export type LiveTControlPhase = 'stopped' | 'starting' | 'running' | 'market_closed' | 'stopping' | 'budget_exhausted' | 'failed';
export interface LiveTControl {
  canControl: boolean;
  enabled: boolean;
  phase: LiveTControlPhase;
  runId: string | null;
  config: Partial<LiveTConfig> | null;
  usage: { costUsd?: number | null; costIsEstimate?: boolean } | null;
  message: string;
  updatedAt: string | null;
}
