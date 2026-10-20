export type ConvertibleFactor = 'ohlcv' | 'stock' | 'sector' | 'market' | 'orderbook' | 'conversion_premium';
export type ConvertibleGranularity = 'tick' | '1s';

export interface ConvertibleTConfig {
  symbol: string;
  startDate: string;
  endDate: string;
  evaluationStart?: string | null;
  evaluationEnd?: string | null;
  basePositionValue: number;
  initialCash: number;
  eventGranularity: ConvertibleGranularity;
  decisionIntervalSeconds: number;
  lookbackEvents: number;
  factors: ConvertibleFactor[];
  orderQuantity: number;
  latencyMs: number;
  participationRate: number;
  commissionBps: number;
  minCommission: number;
  slippageBps: number;
  restoreBaseAtClose: boolean;
  confidenceThreshold: number;
  maxCostUsd: number;
  useModelLatency?: boolean;
  orderTtlMs?: number;
  otherFeeBps?: number;
  datasetId?: string | null;
  accountMode?: 'unlevered_margin' | 'cash_t0';
  currency?: string;
  quantityUnit?: string;
  market?: string;
}

export interface ConvertibleDataSource {
  key: string;
  label: string;
  status: string;
  frequency?: string;
  coverageStart?: string | null;
  coverageEnd?: string | null;
  reason?: string;
  count?: number;
  rows?: number;
}

export interface ConvertibleAvailability {
  status: 'available' | 'partial' | 'unavailable' | 'unverified';
  checkedAt?: string | null;
  message: string;
  sources: ConvertibleDataSource[];
  availableGranularities: ConvertibleGranularity[];
  factorAvailability: Partial<Record<ConvertibleFactor, { available: boolean; reason?: string }>>;
  [key: string]: unknown;
}

export interface ConvertibleCapabilities {
  configured: boolean;
  model?: string;
  provider?: string;
  instrument?: { symbol: string; name: string; stockSymbol?: string; stockName?: string; sector?: string };
  instruments?: ResearchInstrument[];
  defaults: Partial<ConvertibleTConfig>;
  dataAvailability: ConvertibleAvailability;
  limits: { maxCostUsd: number; [key: string]: unknown };
}

export interface ResearchInstrument {
  symbol: string;
  name: string;
  currency: string;
  quantityUnit: string;
  market: string;
  lotSize: number;
  datasetId?: string | null;
  defaultStartDate?: string;
  endDate?: string;
  defaultEndDate?: string;
  available?: boolean;
  status?: string;
  accountMode?: 'unlevered_margin' | 'cash_t0';
  simulationAccount?: 'unlevered_margin' | 'cash_t0';
  availableGranularities?: ConvertibleGranularity[];
  granularity?: ConvertibleGranularity;
}

export interface ConvertibleEquityPoint {
  timestamp: string;
  eventIndex?: number;
  equity: number;
  holdBaselineEquity: number;
  cash: number;
  positionQuantity: number;
  price: number;
  realizedPnl?: number | null;
  unrealizedPnl?: number | null;
  totalPnl?: number | null;
  incrementalTPnl?: number | null;
  [key: string]: unknown;
}

export interface ConvertibleTrade {
  timestamp: string;
  eventIndex?: number;
  side: string;
  quantity: number;
  price: number;
  fee: number;
  cashAfter: number;
  positionAfter: number;
  reason?: string;
  [key: string]: unknown;
}

export interface ConvertibleSignal {
  timestamp: string;
  eventIndex?: number;
  action: string;
  confidence?: number | null;
  reason?: string;
  [key: string]: unknown;
}

export interface ConvertibleEvent {
  timestamp: string;
  eventIndex?: number;
  price: number;
  volume?: number | null;
  stockPrice?: number | null;
  sectorValue?: number | null;
  marketValue?: number | null;
  [key: string]: unknown;
}

export interface ConvertibleResult {
  summary: {
    initialBaseValue?: number;
    initialCash?: number;
    initialTotalEquity?: number;
    finalEquity?: number;
    holdBaselineEquity?: number;
    totalPnl?: number;
    incrementalTPnl?: number;
    realizedPnl?: number | null;
    fees?: number;
    tradeCount?: number;
    [key: string]: unknown;
  };
  equity: ConvertibleEquityPoint[];
  trades: ConvertibleTrade[];
  signals: ConvertibleSignal[];
  events: ConvertibleEvent[];
  dataQuality?: { warnings: string[]; [key: string]: unknown };
  methodology?: string[];
}

export interface ConvertibleTask {
  visibility?: 'public_sample';
  taskId: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  message: string;
  createdAt: string;
  config: ConvertibleTConfig;
  result?: ConvertibleResult | null;
  preview?: ConvertibleResult | null;
  error?: string | null;
  usage: { requests: number; cached: number; costUsd: number; costIsEstimate?: boolean };
}

export interface TradingResearch {
  status: 'completed' | 'running' | 'not_started';
  title: string;
  description: string;
  updatedAt?: string;
  rounds: Array<{
    id: string;
    name: string;
    factors: string[];
    split: string;
    period: string;
    symbol: string;
    currency: string;
    incrementalTPnl?: number | null;
    totalPnl?: number | null;
    maxDrawdownPct?: number | null;
    tradeCount?: number | null;
    modelRequests?: number | null;
    modelCostUsd?: number | null;
    medianLatencyMs?: number | null;
    notes?: string | string[];
    taskId?: string;
  }>;
  conclusion: string;
  limitations: string[];
}
