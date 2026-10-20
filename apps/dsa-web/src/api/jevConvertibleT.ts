import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';
import type { ConvertibleAvailability, ConvertibleCapabilities, ConvertibleTConfig, ConvertibleTask, TradingResearch } from '../types/jevConvertibleT';

const BASE = '/api/v1/jev-convertible-t';

// Factor identifiers are protocol values, including inside the availability map.
function availabilityFrom(data: unknown): ConvertibleAvailability {
  const result = toCamelCase<ConvertibleAvailability>(data);
  const factors = result.factorAvailability as ConvertibleAvailability['factorAvailability'] & { conversionPremium?: { available: boolean; reason?: string } };
  if (factors?.conversionPremium) {
    result.factorAvailability = { ...factors, conversion_premium: factors.conversionPremium };
  }
  return result;
}


export const jevConvertibleTApi = {
  capabilities: async (signal?: AbortSignal): Promise<ConvertibleCapabilities> => {
    const result = toCamelCase<ConvertibleCapabilities>((await apiClient.get(`${BASE}/capabilities`, { signal })).data);
    return { ...result, dataAvailability: availabilityFrom(result.dataAvailability) };
  },
  availability: async (config: ConvertibleTConfig, signal?: AbortSignal): Promise<ConvertibleAvailability> =>
    availabilityFrom((await apiClient.get(`${BASE}/availability`, { signal, params: {
      dataset_id: config.datasetId || undefined, symbol: config.symbol, start_date: config.startDate, end_date: config.endDate, event_granularity: config.eventGranularity,
    } })).data),
  tasks: async (signal?: AbortSignal): Promise<{ items: ConvertibleTask[] }> =>
    toCamelCase((await apiClient.get(`${BASE}/tasks`, { signal, params: { limit: 20 }, headers: BACKGROUND_ROUTE_HEADERS })).data),
  research: async (signal?: AbortSignal): Promise<TradingResearch> =>
    toCamelCase((await apiClient.get(`${BASE}/research`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  researchRound: async (taskId: string, signal?: AbortSignal): Promise<ConvertibleTask> =>
    toCamelCase((await apiClient.get(`${BASE}/research/rounds/${encodeURIComponent(taskId)}`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  task: async (taskId: string, signal?: AbortSignal): Promise<ConvertibleTask> =>
    toCamelCase((await apiClient.get(`${BASE}/tasks/${encodeURIComponent(taskId)}`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  start: async (config: ConvertibleTConfig, requestId: string, signal?: AbortSignal): Promise<ConvertibleTask> =>
    toCamelCase((await apiClient.post(`${BASE}/tasks`, {
      symbol: config.symbol.trim(), start_date: config.startDate, end_date: config.endDate,
      ...(config.evaluationStart?.trim() ? { evaluation_start: config.evaluationStart.trim() } : {}),
      ...(config.evaluationEnd?.trim() ? { evaluation_end: config.evaluationEnd.trim() } : {}),
      base_position_value: config.basePositionValue, initial_cash: config.initialCash,
      event_granularity: config.eventGranularity, decision_interval_seconds: config.decisionIntervalSeconds,
      lookback_events: config.lookbackEvents, factors: config.factors,
      order_quantity: config.orderQuantity, latency_ms: config.latencyMs, participation_rate: config.participationRate,
      commission_bps: config.commissionBps, min_commission: config.minCommission, slippage_bps: config.slippageBps,
      restore_base_at_close: config.restoreBaseAtClose, confidence_threshold: config.confidenceThreshold,
      max_cost_usd: config.maxCostUsd, request_id: requestId,
      use_model_latency: config.useModelLatency ?? true, order_ttl_ms: config.orderTtlMs ?? 10000, other_fee_bps: config.otherFeeBps ?? 0,
      ...(config.datasetId ? { dataset_id: config.datasetId } : {}),
      ...(config.accountMode ? { account_mode: config.accountMode } : {}),
    }, { signal })).data),
  cancel: async (taskId: string, signal?: AbortSignal): Promise<ConvertibleTask> =>
    toCamelCase((await apiClient.post(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, undefined, { signal })).data),
};
