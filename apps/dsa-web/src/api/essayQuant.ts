import apiClient from './index';
import { toCamelCase } from './utils';
import type { EssayQuantCatalog, EssayQuantDashboard, EssayQuantPlan, EssayQuantPrecomputeStatus, EssayQuantRule, EssayQuantRunHistory, EssayQuantTask, EssayQuantTaskList } from '../types/essayQuant';
import { cachedQuery, invalidateCachedQueries } from './requestCache';

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
  dashboard: async (): Promise<EssayQuantDashboard> => cachedQuery(
    'quant:dashboard',
    async () => normalizeDashboard((await apiClient.get('/api/v1/essay-quant/dashboard')).data),
    { freshMs: 8_000, staleMs: 60_000 },
  ),
  precomputeStatus: async (): Promise<EssayQuantPrecomputeStatus> => cachedQuery(
    'quant:precompute-status',
    async () => toCamelCase((await apiClient.get('/api/v1/essay-quant/precompute/status')).data),
    { freshMs: 5_000, staleMs: 30_000 },
  ),
  requestPrecompute: async (): Promise<EssayQuantPrecomputeStatus> => {
    const result = toCamelCase<EssayQuantPrecomputeStatus>((await apiClient.post('/api/v1/essay-quant/precompute/run')).data);
    invalidateCachedQueries('quant:');
    return result;
  },
  catalog: async (): Promise<EssayQuantCatalog> => cachedQuery(
    'quant:catalog',
    async () => toCamelCase((await apiClient.get('/api/v1/essay-quant/research-catalog')).data),
    { freshMs: 10 * 60_000, staleMs: 24 * 60 * 60_000 },
  ),
  runs: async (): Promise<EssayQuantRunHistory> => cachedQuery(
    'quant:runs',
    async () => toCamelCase((await apiClient.get('/api/v1/essay-quant/runs')).data),
    { freshMs: 5_000, staleMs: 30_000 },
  ),
  tasks: async (): Promise<EssayQuantTaskList> => cachedQuery(
    'quant:tasks',
    async () => toCamelCase((await apiClient.get('/api/v1/essay-quant/tasks')).data),
    { freshMs: 1_000, staleMs: 5_000 },
  ),
  taskStatus: async (taskId: string): Promise<EssayQuantTask> => toCamelCase((await apiClient.get(`/api/v1/essay-quant/tasks/${taskId}`)).data),
  runResult: async (runId: number): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.get(`/api/v1/essay-quant/runs/${runId}`)).data),
  startTask: async (rule: EssayQuantRule): Promise<EssayQuantTask> => {
    const result = toCamelCase<EssayQuantTask>((await apiClient.post('/api/v1/essay-quant/tasks', {
      ...payload(rule), rule_id: rule.id, refresh_prices: false, max_symbols: 30,
    })).data);
    invalidateCachedQueries('quant:tasks');
    invalidateCachedQueries('quant:runs');
    return result;
  },
  plan: async (prompt: string): Promise<EssayQuantPlan> => toCamelCase((await apiClient.post('/api/v1/essay-quant/natural-language/plan', { prompt })).data),
  executePlan: async (rule: EssayQuantRule): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.post('/api/v1/essay-quant/natural-language/execute', { rule: payload(rule), refresh_prices: false }, { timeout: 180000 })).data),
  rules: async (): Promise<{ items: EssayQuantRule[]; total: number }> => cachedQuery(
    'quant:rules',
    async () => toCamelCase((await apiClient.get('/api/v1/essay-quant/rules')).data),
    { freshMs: 10_000, staleMs: 60_000 },
  ),
  saveRule: async (rule: EssayQuantRule): Promise<EssayQuantRule> => {
    const response = rule.id
      ? await apiClient.put(`/api/v1/essay-quant/rules/${rule.id}`, payload(rule))
      : await apiClient.post('/api/v1/essay-quant/rules', payload(rule));
    invalidateCachedQueries('quant:rules');
    return toCamelCase(response.data);
  },
  run: async (rule: EssayQuantRule): Promise<EssayQuantDashboard> => normalizeDashboard((await apiClient.post('/api/v1/essay-quant/run', {
    ...payload(rule), rule_id: rule.id, refresh_prices: false, max_symbols: 30,
  }, { timeout: 180000 })).data),
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
