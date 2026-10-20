import { beforeEach, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { jevLiveTApi } from '../jevLiveT';
vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() }, BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' } }));
beforeEach(() => vi.resetAllMocks());
it('preserves observed dispatch timing separately from target and quote cadence', async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ data: { config: { decision_interval_seconds: 1 }, quote: { observed_update_interval_ms: 3000 }, usage: {
    observed_decision_interval_ms: 11250, last_latency_ms: 1240, median_latency_ms: 1360,
    last_model_request_at: '2026-09-21T05:57:04Z', last_model_response_at: '2026-09-21T05:57:05.240Z',
  } } });
  const result = await jevLiveTApi.run('timing');
  expect(result.config.decisionIntervalSeconds).toBe(1);
  expect(result.quote?.observedUpdateIntervalMs).toBe(3000);
  expect(result.usage).toEqual({ observedDecisionIntervalMs: 11250, lastLatencyMs: 1240, medianLatencyMs: 1360,
    lastModelRequestAt: '2026-09-21T05:57:04Z', lastModelResponseAt: '2026-09-21T05:57:05.240Z' });
});
it('uses owner-scoped manual control with only the explicit enabled boolean', async () => {
  const wire = { can_control: true, enabled: true, phase: 'starting', run_id: 'continuous-one', config: { max_cost_usd: 10, duration_seconds: null, max_model_requests: null }, usage: { cost_usd: .008, cost_is_estimate: true }, message: 'starting', updated_at: '2026-09-21T05:00:00Z' };
  vi.mocked(apiClient.get).mockResolvedValue({ data: wire });
  vi.mocked(apiClient.post).mockResolvedValue({ data: wire });
  const controller = new AbortController();
  expect((await jevLiveTApi.control()).canControl).toBe(true);
  const result = await jevLiveTApi.setControl(true, controller.signal);
  expect(apiClient.post).toHaveBeenCalledWith('/api/v1/jev-live-t/control', { enabled: true }, { signal: controller.signal });
  expect(result).toMatchObject({ runId: 'continuous-one', config: { maxCostUsd: 10, durationSeconds: null, maxModelRequests: null }, usage: { costUsd: .008, costIsEstimate: true } });
});
it('preserves confidence sizing configuration and requested quantity without deriving actual fills', async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ data: { config: { confidence_sizing: true, confidence_threshold: .65, lot_size: 100, order_quantity: 1000 }, signals: [{ confidence: .5, requested_quantity: 500 }], trades: [{ shares: 100 }] } });
  const result = await jevLiveTApi.run('sizing');
  expect(result.config).toMatchObject({ confidenceSizing: true, confidenceThreshold: .65, lotSize: 100, orderQuantity: 1000 });
  expect(result.signals[0]).toMatchObject({ confidence: .5, requestedQuantity: 500 });
  expect(result.trades[0].shares).toBe(100);
});
it('observes owner-scoped live snapshots through GET only and preserves T+1 accounting fields', async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ data: { run_id: 'live/id', current_run: { account: { sellable_yesterday_shares: 199900, today_frozen_shares: 100, closed_t_net_pnl: -25, cost_reduction_per_initial_share: -.000125 } } } });
  const request = new AbortController();
  const result = await jevLiveTApi.status(request.signal);
  await jevLiveTApi.runs(request.signal);
  await jevLiveTApi.run('live/id', request.signal);
  expect(result.currentRun?.account).toEqual({ sellableYesterdayShares: 199900, todayFrozenShares: 100, closedTNetPnl: -25, costReductionPerInitialShare: -.000125 });
  expect(apiClient.get).toHaveBeenLastCalledWith('/api/v1/jev-live-t/runs/live%2Fid', expect.objectContaining({ signal: request.signal }));
  expect(apiClient.post).not.toHaveBeenCalled();
  expect(Object.keys(jevLiveTApi).sort()).toEqual(['control', 'run', 'runs', 'setControl', 'status']);
});
it('maps the actual snake-case context protocol including numeric interval keys without changing values', async () => {
  const context = {
    hs300: { status: 'fresh', source: 'sina', source_time: '2026-09-21T05:00:00Z', available_at: '2026-09-21T05:00:01Z', age_seconds: 1, values: { return_since_prev_close: .01, return_30s: -.0002, return_60s: 0 } },
    zz1000: { status: 'missing', source: 'sina', source_time: null, available_at: null, age_seconds: null, values: {} },
    cpo: { status: 'stale', source: 'equal_weight_members', source_time: null, available_at: null, age_seconds: 120, values: { coverage_ratio: .6, member_count: 3 } },
    news: { status: 'fresh', source: 'public', fetched_at: '2026-09-21T05:00:02Z', items: [{ title: '<b>纯文本</b>', summary: '公开摘要', source: '公开源', url: 'https://example.com/news', published_at: '2026-09-21T04:00:00Z', available_at: '2026-09-21T05:00:02Z' }] },
  };
  vi.mocked(apiClient.get).mockResolvedValue({ data: { current_run: { context, last_model_context: { decision_at: '2026-09-21T05:00:03Z', context }, context_diagnostics: { cpo: { label: 'CPO 成分股等权参考', reason: '非官方指数', source_kind: 'equal_weight' } }, config: { max_cost_usd: 10 } } } });
  const result = await jevLiveTApi.status();
  expect(result.currentRun?.context?.hs300.values).toEqual({ returnSincePrevClose: .01, return30S: -.0002, return60S: 0 });
  expect(result.currentRun?.context?.zz1000.values).toEqual({});
  expect(result.currentRun?.lastModelContext?.context.news.items[0]).toMatchObject({ title: '<b>纯文本</b>', publishedAt: '2026-09-21T04:00:00Z', availableAt: '2026-09-21T05:00:02Z' });
  expect(result.currentRun?.contextDiagnostics?.cpo.sourceKind).toBe('equal_weight');
  expect(result.currentRun?.config.maxCostUsd).toBe(10);
});
