import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';
import { toCamelCase } from './utils';
import type { JevBacktestConfig, JevBacktestTask, JevCapabilities } from '../types/jevBacktest';

const BASE = '/api/v1/jev-backtest';

// Deliberately uncached: each read belongs to the currently authenticated owner.
// Reads never create tasks or call the paid model.
export const jevBacktestApi = {
  capabilities: async (signal?: AbortSignal): Promise<JevCapabilities> =>
    toCamelCase((await apiClient.get(`${BASE}/capabilities`, { signal })).data),
  tasks: async (signal?: AbortSignal): Promise<{ items: JevBacktestTask[] }> =>
    toCamelCase((await apiClient.get(`${BASE}/tasks`, { params: { limit: 20 }, signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  task: async (taskId: string, signal?: AbortSignal): Promise<JevBacktestTask> =>
    toCamelCase((await apiClient.get(`${BASE}/tasks/${encodeURIComponent(taskId)}`, { signal, headers: BACKGROUND_ROUTE_HEADERS })).data),
  start: async (config: JevBacktestConfig, requestId: string, signal?: AbortSignal): Promise<JevBacktestTask> =>
    toCamelCase((await apiClient.post(`${BASE}/tasks`, {
      data_source: config.dataSource,
      symbol: config.symbol.trim(), start_date: config.startDate, end_date: config.endDate,
      lookback_bars: config.lookbackBars, initial_cash: config.initialCash,
      confidence_threshold: config.confidenceThreshold, commission_bps: config.commissionBps,
      min_commission: config.minCommission, sell_tax_bps: config.sellTaxBps,
      slippage_bps: config.slippageBps, max_holding_bars: config.maxHoldingBars,
      decision_interval: config.decisionInterval, refresh_prices: config.refreshPrices,
      max_cost_usd: config.maxCostUsd, request_id: requestId,
      timeframe: config.timeframe, position_pct: config.positionPct, settlement_mode: config.settlementMode,
      stop_loss_pct: config.stopLossPct, take_profit_pct: config.takeProfitPct,
    }, { signal })).data),
  cancel: async (taskId: string, signal?: AbortSignal): Promise<JevBacktestTask> =>
    toCamelCase((await apiClient.post(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, undefined, { signal })).data),
};
