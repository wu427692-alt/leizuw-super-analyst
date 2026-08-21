import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayQuantCatalog, EssayQuantDashboard, EssayQuantPlan, EssayQuantPrecomputeStatus, EssayQuantRule, EssayQuantRunHistory } from '../types/essayQuant';

const payload = (rule: EssayQuantRule) => ({
  name: rule.name, source_query: rule.sourceQuery, signal_direction: rule.signalDirection,
  lookback_days: rule.lookbackDays, holding_periods: rule.holdingPeriods,
  first_mention_only: rule.firstMentionOnly, first_mention_window_days: rule.firstMentionWindowDays,
  min_importance: rule.minImportance, min_confidence: rule.minConfidence,
  benchmark_code: rule.benchmarkCode, portfolio_size: rule.portfolioSize, enabled: rule.enabled ?? true,
  strategy_type: rule.strategyType, raw_note_policy: rule.rawNotePolicy,
  dedupe_window_days: rule.dedupeWindowDays, transaction_cost_bps: rule.transactionCostBps,
  validation_method: rule.validationMethod,
});

export const essayQuantApi = {
  dashboard: async (): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.get('/api/v1/essay-quant/institution-dashboard')).data),
  precomputeStatus: async (): Promise<EssayQuantPrecomputeStatus> => toCamelCase((await apiClient.get('/api/v1/essay-quant/precompute/status')).data),
  requestPrecompute: async (): Promise<EssayQuantPrecomputeStatus> => toCamelCase((await apiClient.post('/api/v1/essay-quant/precompute/run')).data),
  catalog: async (): Promise<EssayQuantCatalog> => toCamelCase((await apiClient.get('/api/v1/essay-quant/research-catalog')).data),
  runs: async (): Promise<EssayQuantRunHistory> => toCamelCase((await apiClient.get('/api/v1/essay-quant/runs')).data),
  plan: async (prompt: string): Promise<EssayQuantPlan> => toCamelCase((await apiClient.post('/api/v1/essay-quant/natural-language/plan', { prompt })).data),
  executePlan: async (rule: EssayQuantRule): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.post('/api/v1/essay-quant/natural-language/execute', { rule: payload(rule), refresh_prices: true })).data),
  rules: async (): Promise<{ items: EssayQuantRule[]; total: number }> => toCamelCase((await apiClient.get('/api/v1/essay-quant/rules')).data),
  saveRule: async (rule: EssayQuantRule): Promise<EssayQuantRule> => {
    const response = rule.id
      ? await apiClient.put(`/api/v1/essay-quant/rules/${rule.id}`, payload(rule))
      : await apiClient.post('/api/v1/essay-quant/rules', payload(rule));
    return toCamelCase(response.data);
  },
  run: async (rule: EssayQuantRule): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.post('/api/v1/essay-quant/run', {
    ...payload(rule), rule_id: rule.id, refresh_prices: true, max_symbols: 30,
  })).data),
};

function normalizeDashboard(data: unknown): EssayQuantDashboard {
  const normalized = toCamelCase<Record<string, unknown>>(data);
  const summary = (normalized.summary ?? {}) as Record<string, unknown>;
  const rawTrendSignals = Array.isArray(normalized.trendSignals) ? normalized.trendSignals : [];
  const trendSignals = rawTrendSignals.map((item) => {
    const row = item as Record<string, unknown>;
    return { ...row, momentum20d: row.momentum20d ?? row.momentum20D };
  });
  return {
    ...normalized,
    rule: {
      strategyType: 'essay_event', rawNotePolicy: 'exclude', dedupeWindowDays: 3,
      transactionCostBps: 12, validationMethod: 'walk_forward',
      ...((normalized.rule as Record<string, unknown> | undefined) ?? {}),
    },
    summary: {
      ...summary,
      firstMention30dCount: summary.firstMention30dCount ?? summary.firstMention30DCount ?? 0,
    },
    firstMentions30d: (normalized.firstMentions30d ?? normalized.firstMentions30D ?? []) as EssayQuantDashboard['firstMentions30d'],
    trendSignals,
    robustness: (normalized.robustness ?? {
      sampleCount: 0, averageExcessReturn: null, confidenceInterval95: [null, null],
      tStat: null, payoffRatio: null, positiveRate: null, distribution: [], cohorts: [], sensitivity: [],
    }) as EssayQuantDashboard['robustness'],
    factorAnalysis: (normalized.factorAnalysis ?? []) as EssayQuantDashboard['factorAnalysis'],
  } as EssayQuantDashboard;
}
