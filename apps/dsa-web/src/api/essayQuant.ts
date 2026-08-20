import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayQuantDashboard, EssayQuantRule } from '../types/essayQuant';

const payload = (rule: EssayQuantRule) => ({
  name: rule.name, source_query: rule.sourceQuery, signal_direction: rule.signalDirection,
  lookback_days: rule.lookbackDays, holding_periods: rule.holdingPeriods,
  first_mention_only: rule.firstMentionOnly, first_mention_window_days: rule.firstMentionWindowDays,
  min_importance: rule.minImportance, min_confidence: rule.minConfidence,
  benchmark_code: rule.benchmarkCode, portfolio_size: rule.portfolioSize, enabled: rule.enabled ?? true,
});

export const essayQuantApi = {
  dashboard: async (): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.get('/api/v1/essay-quant/dashboard')).data),
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
    summary: {
      ...summary,
      firstMention30dCount: summary.firstMention30dCount ?? summary.firstMention30DCount ?? 0,
    },
    firstMentions30d: (normalized.firstMentions30d ?? normalized.firstMentions30D ?? []) as EssayQuantDashboard['firstMentions30d'],
    trendSignals,
  } as EssayQuantDashboard;
}
