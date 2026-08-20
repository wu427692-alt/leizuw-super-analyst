export type EssayQuantRule = {
  id?: number; name: string; sourceQuery: string; signalDirection: 'bullish' | 'bearish' | 'all';
  lookbackDays: number; holdingPeriods: number[]; firstMentionOnly: boolean;
  firstMentionWindowDays: number; minImportance: number; minConfidence: number;
  benchmarkCode: string; portfolioSize: number; enabled?: boolean;
};

export type QuantMetric = { period: number; sampleCount: number; winRate?: number | null; averageReturn?: number | null; medianReturn?: number | null };
export type QuantEvent = {
  topicId: string; symbol: string; stockName: string; stance: string; eventAt: string; title: string;
  summary?: string; sourceGroup: string; researchGroup: string; importanceScore: number;
  confidenceScore: number; noveltyScore: number; hypeScore: number; firstMention: boolean;
  entryDate?: string | null; entryPrice?: number | null; returns: Record<string, number>;
  excessReturns: Record<string, number>; maturePeriods: number[]; url: string; rationale?: string;
};
export type EssayQuantDashboard = {
  runId?: number; ruleId?: number; generatedAt: string; rule: EssayQuantRule;
  summary: { eventCount: number; matureEventCount: number; coveredStockCount: number; firstMention30dCount: number; metrics: QuantMetric[]; excessMetrics: QuantMetric[] };
  eventCurve: Array<{ day: number; strategy?: number | null; benchmark?: number | null; sampleCount: number }>;
  researchGroupRankings: Array<{ researchGroup: string; eventCount: number; matureCount: number; rankEligible?: boolean; winRate: number; adjustedWinRate: number; averageReturn: number; averageExcessReturn?: number | null; score: number }>;
  firstMentions30d: QuantEvent[];
  hypeAnalysis: Array<{ level: string; eventCount: number; averageReturn?: number | null; winRate?: number | null }>;
  trendSignals: Array<{ symbol: string; stockName: string; researchGroup: string; eventAt: string; signalStrength: number; trend: string; ma5: number; ma20: number; momentum20d: number; trigger: string; url: string }>;
  portfolio: { components: Array<{ symbol: string; stockName: string; researchGroup: string; weight: number; trigger: string }>; curve: Array<{ date: string; value: number }>; annualizedReturn?: number | null; maxDrawdown?: number | null; winRate?: number | null };
  events: QuantEvent[];
  dataQuality: { essaySource: string; priceSource: string; priceBasis: string; priceCutoff?: string | null; entryRule: string; exitRule: string; benchmark: string; survivorshipNote: string; rankingNote?: string; warnings: string[] };
};
