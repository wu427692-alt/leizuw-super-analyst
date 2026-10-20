export type JevTaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type JevAction = 'BUY' | 'SELL' | 'HOLD';

export interface JevBacktestConfig {
  dataSource: 'sina' | 'tushare';
  symbol: string;
  startDate: string;
  endDate: string;
  lookbackBars: number;
  initialCash: number;
  confidenceThreshold: number;
  commissionBps: number;
  minCommission: number;
  sellTaxBps: number;
  slippageBps: number;
  maxHoldingBars: number;
  decisionInterval: number;
  refreshPrices: boolean;
  maxCostUsd: number;
  timeframe: '1min' | '5min' | '15min';
  positionPct: number;
  settlementMode: 't1' | 'research_t0';
  stopLossPct: number;
  takeProfitPct: number;
}

export interface JevCapabilities {
  configured: boolean;
  model: string;
  provider: string;
  timeframe: string;
  timeframes?: string[];
  limits: { maxEvaluationBars: number; maxCostUsd: number };
  defaults: Partial<JevBacktestConfig>;
  dataSources: string[];
  message?: string;
}

export interface JevEquityPoint {
  date: string;
  equity: number;
  drawdownPct: number;
  close: number;
  cash: number;
  positionShares: number;
  realizedPnl?: number;
  unrealizedPnl?: number;
  totalPnl?: number;
}

export interface JevSignal {
  date: string;
  action: JevAction;
  confidence: number | null;
  status: string;
  labels: string[];
  probabilities?: Record<string, number>;
  usage?: Record<string, unknown>;
  executionAction: string;
  executionReason: string;
  fillStatus: string;
  fillDate?: string;
}

export interface JevTrade {
  date: string;
  signalDate: string;
  action: 'BUY' | 'SELL';
  shares: number;
  price: number;
  gross: number;
  commission: number;
  sellTax: number;
  slippageCost: number;
  cashAfter: number;
  positionAfter: number;
  pnl?: number | null;
  reason?: string;
}

export interface JevBacktestSummary {
    totalReturnPct: number | null;
    maxDrawdownPct: number | null;
    winRatePct: number | null;
    tradeCount: number;
    closedTradeCount: number;
    initialCash: number;
    finalEquity: number;
    totalCosts: number;
    cash: number;
    positionShares: number;
    positionMarketValue: number;
    commission: number;
    sellTax: number;
    slippageCost: number;
    modelCalls: number;
    unavailableCount: number;
    uncertainCount: number;
    blockedOrderCount: number;
    ruleExitCount: number;
    warmupSkippedBars: number;
    latestSignal?: JevSignal | null;
}

export interface JevBacktestResult {
  summary: JevBacktestSummary;
  equity: JevEquityPoint[];
  signals: JevSignal[];
  trades: JevTrade[];
  baselines: Record<'buyHold' | 'ma520', { summary: JevBacktestSummary; equity: JevEquityPoint[]; trades: JevTrade[] }>;
  dataQuality: { warnings: string[]; [key: string]: unknown };
  methodology: string[];
  marketData?: { source?: string; frequency?: string; bars: JevPriceBar[] };
}

export interface JevPriceBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface JevBacktestTask {
  taskId: string;
  status: JevTaskStatus;
  progress: number;
  message: string;
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  config: Omit<JevBacktestConfig, 'dataSource'> & { dataSource?: JevBacktestConfig['dataSource'] };
  result?: JevBacktestResult | null;
  preview?: { equity: JevEquityPoint[]; signals: JevSignal[]; trades: JevTrade[]; summary: Partial<JevBacktestSummary>; marketData?: { bars: JevPriceBar[] } } | null;
  error?: string | null;
  usage: { requests: number; cached: number; costUsd: number; costIsEstimate?: boolean };
}
