import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { jevConvertibleTApi } from '../jevConvertibleT';
import type { ConvertibleTConfig } from '../../types/jevConvertibleT';
vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() }, BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' } }));
const config: ConvertibleTConfig = { symbol: 'NVDA', datasetId: 'nvda-real', accountMode: 'unlevered_margin', currency: 'USD', quantityUnit: 'shares', market: 'NASDAQ', startDate: '2025-09-16', endDate: '2025-09-16', basePositionValue: 200000, initialCash: 200000, eventGranularity: '1s', decisionIntervalSeconds: 60, lookbackEvents: 60, factors: ['ohlcv', 'orderbook'], orderQuantity: 100, latencyMs: 1000, participationRate: .1, commissionBps: 1, minCommission: 0, slippageBps: 2, restoreBaseAtClose: true, confidenceThreshold: .65, maxCostUsd: .2 };
describe('event research API wire contract', () => {
  beforeEach(() => vi.resetAllMocks());
  it('reads dataset-specific availability and research via GET without paid actions', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { rounds: [{ incremental_t_pnl: -10 }], factor_availability: { conversion_premium: { available: false } } } });
    const controller = new AbortController();
    const state = await jevConvertibleTApi.availability(config, controller.signal);
    expect(state.factorAvailability.conversion_premium?.available).toBe(false);
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/jev-convertible-t/availability', expect.objectContaining({ signal: controller.signal, params: expect.objectContaining({ dataset_id: 'nvda-real', event_granularity: '1s' }) }));
    const research = await jevConvertibleTApi.research(controller.signal);
    expect(research.rounds[0].incrementalTPnl).toBe(-10);
    expect(apiClient.post).not.toHaveBeenCalled();
  });
  it('reads explicit published rounds separately from private tasks', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { task_id: 'sample-id', visibility: 'public_sample' } });
    const controller = new AbortController();
    const result = await jevConvertibleTApi.researchRound('sample-id', controller.signal);
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/jev-convertible-t/research/rounds/sample-id', expect.objectContaining({ signal: controller.signal }));
    expect(result.visibility).toBe('public_sample');
    expect(apiClient.post).not.toHaveBeenCalled();
  });
  it('preserves explicit zoned research windows in POST and omits a deliberately empty window', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { task_id: 'test' } });
    await jevConvertibleTApi.start({ ...config, evaluationStart: '2025-09-16T14:00:00-04:00', evaluationEnd: '2025-09-16T15:55:00-04:00' }, 'with-window');
    expect(vi.mocked(apiClient.post).mock.calls[0][1]).toMatchObject({ evaluation_start: '2025-09-16T14:00:00-04:00', evaluation_end: '2025-09-16T15:55:00-04:00' });
    await jevConvertibleTApi.start({ ...config, evaluationStart: '', evaluationEnd: null }, 'whole-day');
    expect(vi.mocked(apiClient.post).mock.calls[1][1]).not.toHaveProperty('evaluation_start');
    expect(vi.mocked(apiClient.post).mock.calls[1][1]).not.toHaveProperty('evaluation_end');
  });
  it('serializes execution settings and idempotency but leaves currency and units to server validation', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { task_id: 'test' } });
    await jevConvertibleTApi.start(config, 'request-id');
    const payload = vi.mocked(apiClient.post).mock.calls[0][1] as Record<string, unknown>;
    expect(payload).toMatchObject({ symbol: 'NVDA', dataset_id: 'nvda-real', account_mode: 'unlevered_margin', base_position_value: 200000, initial_cash: 200000, decision_interval_seconds: 60, event_granularity: '1s', order_quantity: 100, latency_ms: 1000, participation_rate: .1, use_model_latency: true, order_ttl_ms: 10000, other_fee_bps: 0, max_cost_usd: .2, request_id: 'request-id' });
    expect(payload).not.toHaveProperty('currency'); expect(payload).not.toHaveProperty('quantity_unit'); expect(payload).not.toHaveProperty('market');
  });
});
