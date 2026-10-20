import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { jevBacktestApi } from '../jevBacktest';
import type { JevBacktestConfig } from '../../types/jevBacktest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() }, BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' } }));

describe('jevBacktestApi wire contract', () => {
  beforeEach(() => vi.resetAllMocks());

  it('performs fresh owner-scoped GETs only when reading history or snapshots', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { task_id: 'task/one', baselines: { ma5_20: { summary: { total_return_pct: 1.2 } } } } });
    const controller = new AbortController();
    await jevBacktestApi.tasks(controller.signal);
    await jevBacktestApi.tasks(controller.signal);
    const result = await jevBacktestApi.task('task/one', controller.signal);
    expect(apiClient.get).toHaveBeenCalledTimes(3);
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenLastCalledWith('/api/v1/jev-backtest/tasks/task%2Fone', expect.objectContaining({ signal: controller.signal }));
    expect(result).toEqual({ taskId: 'task/one', baselines: { ma520: { summary: { totalReturnPct: 1.2 } } } });
  });

  it('serializes adjustable minute conditions and idempotency key without credentials or unrelated feeds', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { task_id: 'new-task' } });
    const config: JevBacktestConfig = { dataSource: 'sina', symbol: ' 600000 ', startDate: '2026-09-17', endDate: '2026-09-18', timeframe: '5min', lookbackBars: 60, initialCash: 100000, confidenceThreshold: .7, commissionBps: 3, minCommission: 5, sellTaxBps: 5, slippageBps: 5, maxHoldingBars: 60, decisionInterval: 2, refreshPrices: false, maxCostUsd: .2, positionPct: .5, settlementMode: 'research_t0', stopLossPct: 5, takeProfitPct: 10 };
    const controller = new AbortController();
    await jevBacktestApi.start(config, 'request-uuid', controller.signal);
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/jev-backtest/tasks', {
      data_source: 'sina',
      symbol: '600000', start_date: '2026-09-17', end_date: '2026-09-18', timeframe: '5min',
      lookback_bars: 60, initial_cash: 100000, confidence_threshold: .7, commission_bps: 3,
      min_commission: 5, sell_tax_bps: 5, slippage_bps: 5, max_holding_bars: 60,
      decision_interval: 2, refresh_prices: false, max_cost_usd: .2, request_id: 'request-uuid',
      position_pct: .5, settlement_mode: 'research_t0', stop_loss_pct: 5, take_profit_pct: 10,
    }, { signal: controller.signal });
  });
});
