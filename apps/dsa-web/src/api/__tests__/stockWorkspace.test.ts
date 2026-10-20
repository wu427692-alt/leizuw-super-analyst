import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
import apiClient from '../index';
import { investmentMonitorApi } from '../investmentMonitor';
import { clearRequestCache, peekCachedQuery } from '../requestCache';

const response = (price: number) => ({ data: { stock: { symbol: '603306.SH', market: { price } } } });
const news = (id: number, overrides: Record<string, unknown> = {}) => ({
  id, event_type: 'news', source_key: 'news.test', source_name: '测试新闻', external_id: String(id),
  event_at: '2000-01-01T10:00:00Z', title: `测试新闻 ${id}`, summary: '测试摘要', url: '',
  metrics: { _evidence: { evidence_level: 'unverified' } }, ...overrides,
});
const withNews = (items: ReturnType<typeof news>[], options: {
  overview?: boolean; generatedAt?: string; symbol?: string; price?: number; state?: string;
} = {}) => ({ data: {
  generated_at: options.generatedAt ?? '2000-01-01T10:00:00Z',
  stock: { symbol: options.symbol ?? '603306.SH', market: { price: options.price ?? 76 },
    messages: { count: 500, items },
    ...(options.overview ? { overview: { sections: [{ key: 'news', items }] } } : {}),
  }, cache: { state: options.state ?? 'rebuilt' },
} });

describe('stockWorkspace exact stock and view cache', () => {
  beforeEach(() => { vi.clearAllMocks(); clearRequestCache(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); clearRequestCache(); });

  it('preserves precise per-metric forecast provenance through nested API key conversion', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { stock: { symbol: '603306.SH', consensus: {
      forecasts: [{ period: '2027E', np_median: 20000, source_event_ids: [1, 2, 3],
        metric_source_event_ids: { eps: [1, 2], np: [1, 3], op_rt: [3] },
        conflicting_metric_counts: { np: 1, op_rt: 2 } }],
    } } } });
    const { stock } = await investmentMonitorApi.stockWorkspace('603306.SH');
    expect(stock.consensus.forecasts[0]).toMatchObject({ period: '2027E', npMedian: 20000,
      sourceEventIds: [1, 2, 3], metricSourceEventIds: { eps: [1, 2], np: [1, 3], opRt: [3] },
      conflictingMetricCounts: { np: 1, opRt: 2 } });
  });

  it('preserves the full default and isolates the lightweight overview cache', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(response(76)).mockResolvedValueOnce(response(77));
    const full = await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    const overview = await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/api/v1/investment-monitor/stock-workspace/603306.SH', { params: { days: 183, refresh: false, view: 'full' } });
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/v1/investment-monitor/stock-workspace/603306.SH', { params: { days: 183, refresh: false, view: 'overview' } });
    expect(full.stock.market.price).toBe(76);
    expect(overview.stock.market.price).toBe(77);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('delivers the new response after expiry instead of silently returning the stale value', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(response(76));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    vi.advanceTimersByTime(30_001);
    let resolve!: (value: ReturnType<typeof response>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    let settled = false;
    const refresh = investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview').then(value => { settled = true; return value; });
    await Promise.resolve(); await Promise.resolve();
    expect(settled).toBe(false);
    const shared = investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    resolve(response(78));
    expect((await refresh).stock.market.price).toBe(78);
    expect((await shared).stock.market.price).toBe(78);
  });

  it('propagates an expired request failure so the page can mark its retained data as old', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(response(76));
    await investmentMonitorApi.stockWorkspace('603306.SH');
    vi.advanceTimersByTime(30_001);
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('temporary timeout'));
    await expect(investmentMonitorApi.stockWorkspace('603306.SH')).rejects.toThrow('temporary timeout');
    vi.mocked(apiClient.get).mockResolvedValueOnce(response(77));
    expect((await investmentMonitorApi.stockWorkspace('603306.SH')).stock.market.price).toBe(77);
  });

  it('force overview refresh invalidates detail without substituting its lightweight payload', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(response(76)).mockResolvedValueOnce(response(77))
      .mockResolvedValueOnce(response(78)).mockResolvedValueOnce(response(79));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183, true, 'overview')).stock.market.price).toBe(78);
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.market.price).toBe(79);
    expect(apiClient.get).toHaveBeenCalledTimes(4);
  });

  it('new overview news invalidates only the same stock and days detail cache', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)]))
      .mockResolvedValueOnce(withNews([news(50)], { symbol: '300308.SZ' }))
      .mockResolvedValueOnce(withNews([news(60)]))
      .mockResolvedValueOnce(withNews([news(2), news(1)], { overview: true, generatedAt: '2000-01-01T10:01:00Z' }))
      .mockResolvedValueOnce(withNews([news(2), news(1)]));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await investmentMonitorApi.stockWorkspace('300308.SZ', 183);
    await investmentMonitorApi.stockWorkspace('603306.SH', 365);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.messages.items.map(item => item.id)).toEqual([2, 1]);
    expect((await investmentMonitorApi.stockWorkspace('300308.SZ', 183)).stock.messages.items[0].id).toBe(50);
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 365)).stock.messages.items[0].id).toBe(60);
    expect(apiClient.get).toHaveBeenCalledTimes(5);
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it.each([
    ['source identity', { source_key: 'news.corrected' }],
    ['external identity', { external_id: 'corrected' }],
    ['type', { event_type: 'long_news' }],
    ['source date', { event_at: '2000-01-01T11:00:00Z' }],
    ['title', { title: '更正后的标题' }],
    ['source label', { source_name: '更正后的来源' }],
    ['original link', { url: 'https://example.com/correction' }],
    ['summary', { summary: '更正后的摘要' }],
    ['evidence level', { metrics: { _evidence: { evidence_level: 'fact' } } }],
  ])('invalidates a detail snapshot when the same news record changes its %s', async (_, changes) => {
    const corrected = news(1, changes);
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)]))
      .mockResolvedValueOnce(withNews([corrected], { overview: true }))
      .mockResolvedValueOnce(withNews([corrected]));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect(apiClient.get).toHaveBeenCalledTimes(3);
  });

  it.each(['same-news', 'no-news', 'older-overview', 'other-type'] as const)('retains usable detail cache for %s', async condition => {
    const items = condition === 'same-news' ? [news(1)] : condition === 'no-news' ? []
      : condition === 'other-type' ? [news(2, { event_type: 'essay' })] : [news(2)];
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)], { generatedAt: '2000-01-01T10:01:00Z' }))
      .mockResolvedValueOnce(withNews(items, { overview: true, price: 80,
        generatedAt: condition === 'older-overview' ? '2000-01-01T10:00:00Z' : '2000-01-01T10:02:00Z' }));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.messages.items[0].id).toBe(1);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('compares the same 240 Unicode characters retained in overview without reloading full text', async () => {
    const prefix = '测试🌍'.repeat(80);
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1, { summary: `${prefix}后续全文` })]))
      .mockResolvedValueOnce(withNews([news(1, { summary: prefix })], { overview: true }));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('retries an older in-flight detail once if a new overview arrives before it finishes', async () => {
    let resolveOld!: (value: ReturnType<typeof withNews>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce(withNews([news(2)], { overview: true }))
      .mockResolvedValueOnce(withNews([news(2), news(1)]));
    const detail = investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    const shared = investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    resolveOld(withNews([news(1)]));
    const result = await detail;
    expect(result.stock.messages.items.map(item => item.id)).toEqual([2, 1]);
    expect(await shared).toBe(result);
    expect(await investmentMonitorApi.stockWorkspace('603306.SH', 183)).toBe(result);
    expect(apiClient.get).toHaveBeenCalledTimes(3);
    expect(apiClient.get).toHaveBeenNthCalledWith(3, '/api/v1/investment-monitor/stock-workspace/603306.SH', {
      params: { days: 183, refresh: true, view: 'full' },
    });
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('caches a successful single retry after the server marks full superseded', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)], { state: 'superseded' }))
      .mockResolvedValueOnce(withNews([news(2)]));
    const result = await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect(result.stock.messages.items[0].id).toBe(2);
    expect(await investmentMonitorApi.stockWorkspace('603306.SH', 183)).toBe(result);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(apiClient.get).toHaveBeenLastCalledWith('/api/v1/investment-monitor/stock-workspace/603306.SH', {
      params: { days: 183, refresh: true, view: 'full' },
    });
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('does not retry a normal new detail solely because an already cached older overview differs', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)], { overview: true }))
      .mockResolvedValueOnce(withNews([news(1, { summary: '更新摘要' })], { generatedAt: '2000-01-01T10:01:00Z' }));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    const result = await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    expect(result.stock.messages.items[0].summary).toBe('更新摘要');
    expect(await investmentMonitorApi.stockWorkspace('603306.SH', 183)).toBe(result);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('does not retry or repopulate an old superseded full after logout', async () => {
    let resolveOld!: (value: ReturnType<typeof withNews>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const old = investmentMonitorApi.stockWorkspace('603306.SH', 183);
    clearRequestCache();
    resolveOld(withNews([news(1)], { state: 'superseded' }));
    await old;
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(peekCachedQuery('monitor:stock-workspace:603306.SH:183:full')).toBeUndefined();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('does not retry an old full against a new session overview or replace its full cache', async () => {
    let resolveOld!: (value: ReturnType<typeof withNews>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce(withNews([news(2)], { overview: true }))
      .mockResolvedValueOnce(withNews([news(2)]));
    const old = investmentMonitorApi.stockWorkspace('603306.SH', 183);
    clearRequestCache();
    await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    const current = await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    resolveOld(withNews([news(1)]));
    await old;
    expect(await investmentMonitorApi.stockWorkspace('603306.SH', 183)).toBe(current);
    expect(apiClient.get).toHaveBeenCalledTimes(3);
  });

  it('does not retry a superseded full replaced by a newer force request', async () => {
    let resolveOld!: (value: ReturnType<typeof withNews>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce(withNews([news(2)]));
    const old = investmentMonitorApi.stockWorkspace('603306.SH', 183);
    const current = await investmentMonitorApi.stockWorkspace('603306.SH', 183, true);
    resolveOld(withNews([news(1)], { state: 'superseded' }));
    await old;
    expect(await investmentMonitorApi.stockWorkspace('603306.SH', 183)).toBe(current);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('handles the server superseded signal with one bounded retry, not an endless loading loop', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(withNews([news(1)], { state: 'superseded' }));
    await expect(investmentMonitorApi.stockWorkspace('603306.SH', 183)).rejects.toThrow('研究资料正在更新');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(2)]));
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.messages.items[0].id).toBe(2);
  });

  it('a failed overview refresh keeps the last successful detail usable', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(withNews([news(1)])).mockRejectedValueOnce(new Error('timeout'));
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    await expect(investmentMonitorApi.stockWorkspace('603306.SH', 183, true, 'overview')).rejects.toThrow('timeout');
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.messages.items[0].id).toBe(1);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('an overview completing after logout cannot invalidate a new session detail cache', async () => {
    let resolveOld!: (value: ReturnType<typeof withNews>) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce(withNews([news(2)]));
    const old = investmentMonitorApi.stockWorkspace('603306.SH', 183, true, 'overview');
    clearRequestCache();
    await investmentMonitorApi.stockWorkspace('603306.SH', 183);
    resolveOld(withNews([news(1)], { overview: true }));
    await old;
    expect((await investmentMonitorApi.stockWorkspace('603306.SH', 183)).stock.messages.items[0].id).toBe(2);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('normalizes nested overview evidence without discarding source dates or types', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { stock: {
      symbol: '603306.SH', market: { prev_close: 76, change: -1, quote_kind: 'snapshot' },
      overview: { recent_evidence: [{ event_at: '2026-09-11T10:00:00+08:00', source_name: '公告', event_type: 'announcement' }],
        sections: [{ key: 'announcements', label: '公告', items: [{ source_name: '巨潮' }], latest_at: null }],
        data_as_of: { market: '2026-09-11T10:00:00+08:00', fundamentals: '2026-06-30' },
        loaded_sections: ['overview'], evidence_counts_complete: false },
    } } });
    const { stock } = await investmentMonitorApi.stockWorkspace('603306.SH', 183, false, 'overview');
    expect(stock.market).toMatchObject({ prevClose: 76, change: -1, quoteKind: 'snapshot' });
    expect(stock.overview?.recentEvidence[0]).toMatchObject({ sourceName: '公告', eventAt: '2026-09-11T10:00:00+08:00' });
    expect(stock.overview?.sections[0].items[0].sourceName).toBe('巨潮');
    expect(stock.overview?.dataAsOf.fundamentals).toBe('2026-06-30');
    expect(stock.overview?.evidenceCountsComplete).toBe(false);
  });

  it('preserves real source capital fields and independent dates through the API adapter', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { stock: { symbol: '603306.SH', capital: {
      chip_trade_date: '20260907', moneyflow_trade_date: '20260909', margin_trade_date: '20260908', northbound_trade_date: null,
      winner_rate: 0, weighted_cost: 70, cost_50pct: 0, cost_85pct: 78.25,
      moneyflow: { trade_date: '20260909', net_mf_amount: -1234.5, buy_elg_amount: 100, sell_elg_amount: 1100 },
      margin: { trade_date: '20260908', rzye: 200000000, rzmre: 2000000, rzche: 3000000 }, northbound: {},
    } } } });
    const { stock } = await investmentMonitorApi.stockWorkspace('603306.SH');
    expect(stock.capital).toEqual({
      chipTradeDate: '20260907', moneyflowTradeDate: '20260909', marginTradeDate: '20260908', northboundTradeDate: null,
      winnerRate: 0, weightedCost: 70, cost50Pct: 0, cost85Pct: 78.25,
      moneyflow: { tradeDate: '20260909', netMfAmount: -1234.5, buyElgAmount: 100, sellElgAmount: 1100 },
      margin: { tradeDate: '20260908', rzye: 200000000, rzmre: 2000000, rzche: 3000000 }, northbound: {},
    });
  });

  it('keeps the original research subject and stock-scoped match evidence through API normalization', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { stock: { symbol: '300308.SZ', institution: { latest: [{
      id: 157256, stock_relation: { kind: 'other_company_mention', target_symbol: '300308.SZ', subject_symbol: '300684.SZ',
        subject_name: '中石科技', match_field: 'abstract', match_text: '摘要提及中际旭创。' },
    }] } } } });
    const result = await investmentMonitorApi.stockWorkspace('300308.SZ');
    expect(result.stock.institution.latest[0].stockRelation).toEqual({ kind: 'other_company_mention', targetSymbol: '300308.SZ',
      subjectSymbol: '300684.SZ', subjectName: '中石科技', matchField: 'abstract', matchText: '摘要提及中际旭创。' });
  });
});
