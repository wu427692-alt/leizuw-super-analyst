import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toCamelCase } from '../../api/utils';
import type { EssayConsensusAnalysis, EssayExpectationEstimate, FundamentalSourceKind, MonitorEvent, SuperWatchlistStock } from '../../types/investmentMonitor';
import SuperWatchlistPage from '../SuperWatchlistPage';

const { mockAdd, mockLoad, mockGetWatchlist, mockEvent, mockRemove, mockQuotes, mockNote, mockRefresh, mockAnalyzeConsensus, mockEssayConsensus } = vi.hoisted(() => ({
  mockAdd: vi.fn(),
  mockLoad: vi.fn(),
  mockGetWatchlist: vi.fn(),
  mockEvent: vi.fn(),
  mockRemove: vi.fn(),
  mockQuotes: new Map<string, Record<string, unknown>>(),
  mockNote: vi.fn(),
  mockRefresh: vi.fn(),
  mockAnalyzeConsensus: vi.fn(),
  mockEssayConsensus: vi.fn(),
}));

vi.mock('../../api/investmentMonitor', () => ({
  investmentMonitorApi: {
    stockWorkspace: mockLoad,
    event: mockEvent,
    backfillWatchlist: vi.fn(),
    refreshSuperWatchlist: mockRefresh,
    analyzeEssayConsensus: mockAnalyzeConsensus,
    essayConsensus: mockEssayConsensus,
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAdd,
    removeFromWatchlist: mockRemove,
  },
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => ({
    index: [
      { canonicalCode: '300476.SZ', displayCode: '300476', nameZh: '胜宏科技', pinyinFull: 'shenghongkeji', pinyinAbbr: 'shkj', aliases: [], market: 'CN', assetType: 'stock', active: true, popularity: 100 },
      { canonicalCode: '603306.SH', displayCode: '603306', nameZh: '华懋科技', pinyinFull: 'huamaokeji', pinyinAbbr: 'hmkj', aliases: [], market: 'CN', assetType: 'stock', active: true, popularity: 100 },
    ],
    loading: false,
    fallback: false,
    error: null,
    loaded: true,
  }),
}));

vi.mock('../../api/essayRadar', () => ({
  essayRadarApi: {
    note: mockNote,
  },
}));

vi.mock('../../hooks/useRealtimeQuotes', () => ({
  useRealtimeQuotes: () => ({
    quotes: mockQuotes,
    keyFor: (value: string) => value.split('.')[0],
  }),
}));

vi.mock('../../components/market', () => ({
  MarketTimeframeChart: ({ symbol, compact = false }: { symbol: string; compact?: boolean }) => <div data-testid="market-chart" data-compact={compact}>{symbol}</div>,
}));

function stock(symbol: string, name: string): SuperWatchlistStock {
  return {
    symbol,
    name,
    history: [],
    market: { price: 10, changePct: 1 },
    valuation: {},
    technical: {},
    fundamentals: {},
    capital: { moneyflow: {}, margin: {}, northbound: {}, chipDistribution: [] },
    ownership: { pledge: {}, shareUnlock: [], holderTrades: [], repurchases: [] },
    institution: { researchCount: 0, latest: [], institutions: [] },
    company: { profile: {}, announcementCount: 0, announcements: [] },
    alternative: { essayCount: 0, essays: [], catalysts: [], risks: [] },
    consensus: {
      brokerReportCount: 0, ratings: [], targetPrice: { sampleCount: 0 }, forecasts: [],
      essayExpectationCount: 0, essayExpectations: [], method: 'test',
      essayAnalysis: {
        status: 'not_started', sourceCount: 0, analyzedCount: 0, pendingCount: 0,
        summary: '', hasExplicitExpectations: false, profitOutlook: '', valuationOutlook: '',
        estimates: [], metricCounts: {}, consensusPoints: [], conflicts: [], caveats: [], sourceNotes: [],
      },
    },
    messages: { count: 0, items: [], channels: [] },
    stockComments: { count: 0, items: [], sourceNote: 'test' },
    signals: [],
    coverage: [],
    evidence: { eventCount: 0, rawEventCount: 0, factualCount: 0, unverifiedCount: 0, sourceCount: 0, originalLinkCount: 0, originalLinkCoverage: 0, channels: [] },
    timeline: [],
  };
}

function workspace(item: SuperWatchlistStock, view: 'overview' | 'full' = 'full') {
  if (view === 'full') return { stock: item, view };
  const sections = [
    { key: 'announcements' as const, label: '公司公告', items: item.company.announcements.slice(0, 3) },
    { key: 'research' as const, label: '券商研究', items: item.institution.latest.slice(0, 3) },
    { key: 'opinions' as const, label: '机构段子', items: item.alternative.essays.slice(0, 3) },
    { key: 'news' as const, label: '相关新闻', items: item.messages.items.filter(event => ['news', 'long_news'].includes(event.eventType)).slice(0, 3) },
  ].map(section => ({ ...section, latestAt: section.items[0]?.eventAt ?? null }));
  // Model the real lightweight contract: detail-only rows must not leak into
  // an overview fixture and accidentally make a lazy-load test pass.
  const empty = stock(item.symbol, item.name);
  return {
    view,
    stock: {
      ...empty, market: item.market, valuation: item.valuation, fundamentals: item.fundamentals,
      overview: {
        recentEvidence: sections.flatMap(section => section.items), sections,
        dataAsOf: { market: item.market.updatedAt ?? null, valuation: null, fundamentals: null, capital: null, technical: null },
        loadedSections: ['overview'], evidenceCountsComplete: false,
      },
    },
  };
}

function replyFor(stocks: SuperWatchlistStock[]) {
  return async (symbol: string, _days: number, _force: boolean, view: 'overview' | 'full' = 'full') => {
    const item = stocks.find(candidate => candidate.symbol === symbol);
    if (!item) throw new Error(`Test fixture missing the requested stock: ${symbol}`);
    return workspace(item, view);
  };
}

function respondWith(...stocks: SuperWatchlistStock[]) {
  mockLoad.mockImplementation(replyFor(stocks));
}

function essay(id: number, title: string): MonitorEvent {
  return {
    id, title, sourceKey: 'zsxq.essays', sourceName: '调研纪要', sourceType: 'mcp',
    externalId: `topic-${id}`, eventType: 'essay', perspective: 'investor',
    summary: `原文摘录 ${id}`, symbols: ['603306.SH'], sentiment: 'neutral',
    importanceScore: 50, confidenceScore: 0.5, tags: [], actors: [],
    metrics: { _evidence: { evidenceLevel: 'unverified' } },
    eventAt: `2026-09-10T${String(8 + id % 10).padStart(2, '0')}:00:00Z`,
  };
}

function expectation(id: number, overrides: Partial<EssayExpectationEstimate> = {}): EssayExpectationEstimate {
  return {
    topicId: `expectation-${id}`, title: `预期原文 ${id}`,
    proposedAt: `2026-09-${String(id).padStart(2, '0')}T08:00:00Z`,
    metric: 'net_profit', period: '2026E', valueText: `净利润 ${id} 亿元`,
    evidence: `原文证据 ${id}`, sourceKind: 'related', ...overrides,
  };
}

function displayedTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

function LocationProbe() { return <output data-testid="location">{useLocation().search}</output>; }

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function financialBriefStock(): SuperWatchlistStock {
  // Exercise the same snake_case conversion as the workspace API. Deliberately
  // separate each source period, including the legacy fallback period.
  return toCamelCase<SuperWatchlistStock>({
    ...stock('603306.SH', '华懋科技'),
    fundamentals: {
      period: '20260930', indicator_period: '2026-06-30', income_period: '2026-03-31', cashflow_period: '2025-12-31',
      revenue: 1_234_000_000, net_profit: 120_000_000, operating_cashflow: 87_650_000,
      revenue_yoy: 12.5, revenue_yoy_field: 'q_sales_yoy', net_profit_yoy: -3.2, net_profit_yoy_field: 'netprofit_yoy',
    },
    valuation: { pe_ttm: 21.75, as_of: '2026-09-11' },
    market: { price: 26, change_pct: 1.2, updated_at: '2026-09-13T02:00:00Z' },
  });
}

describe('SuperWatchlistPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLoad.mockReset();
    mockQuotes.clear();
    mockGetWatchlist.mockResolvedValue(['603306.SH']);
    mockAdd.mockResolvedValue(['603306', '300476']);
    mockRemove.mockResolvedValue(['300476']);
    mockNote.mockImplementation(async (topicId: string) => ({
      topicId, groupId: '1', groupName: '测试星球',
      title: '华懋下半年订单跟踪', content: '华懋订单可能改善。',
      authorName: '研究员', topicType: 'talk', digested: false, sticky: false,
      symbols: ['603306.SH'],
      files: [{ fileId: 'file-1', name: '调研附件.pdf', viewUrl: 'https://example.test/file.pdf' }],
      images: [{ imageId: 'image-1', viewUrl: 'https://example.test/image.jpg' }],
      counts: {},
      createdAt: '2026-08-20T08:00:00Z',
    }));
    mockEvent.mockReset();
    mockRefresh.mockResolvedValue({});
    mockAnalyzeConsensus.mockReset();
    mockEssayConsensus.mockReset();
  });

  afterEach(() => vi.useRealTimers());

  it('reveals the selected material tab inside its own scroll area without shifting the page', async () => {
    respondWith(stock('603306.SH', '华懋科技'));
    const originalBounds = HTMLElement.prototype.getBoundingClientRect;
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('aria-label') === '资料类型') return new DOMRect(10, 400, 200, 38);
      if (this.parentElement?.getAttribute('aria-label') === '资料类型' && this.textContent === '新闻与提示') {
        return new DOMRect(250 - this.parentElement.scrollLeft, 400, 90, 38);
      }
      return originalBounds.call(this);
    });
    try {
      render(<MemoryRouter initialEntries={['/super-watchlist?section=messages']}><SuperWatchlistPage /></MemoryRouter>);
      const nav = await screen.findByRole('navigation', { name: '资料类型' });
      await waitFor(() => expect(nav.scrollLeft).toBe(130));
      expect(within(nav).getByRole('button', { name: '新闻与提示' })).toHaveAttribute('aria-pressed', 'true');
      fireEvent(window, new Event('resize'));
      expect(nav.scrollLeft).toBe(130);
      expect(window.scrollY).toBe(0);
    } finally {
      bounds.mockRestore();
    }
  });

  describe('state recovery regressions', () => {
    it('does not replace a successful addition with an older initial list response', async () => {
      const added = stock('300476.SZ', '胜宏科技');
      const initialRead = deferred<string[]>();
      mockGetWatchlist.mockReturnValueOnce(initialRead.promise).mockResolvedValue([added.symbol]);
      mockAdd.mockResolvedValue([added.symbol]);
      respondWith(added);
      render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);

      const input = screen.getByPlaceholderText('输入股票名称或代码');
      fireEvent.change(input, { target: { value: added.symbol } });
      fireEvent.submit(input.closest('form')!);
      expect(await screen.findByRole('heading', { name: added.name, level: 2 })).toBeVisible();
      await act(async () => initialRead.resolve([]));

      expect(screen.getByRole('heading', { name: added.name, level: 2 })).toBeVisible();
      expect(screen.getByRole('button', { name: `删除自选股 ${added.name}` })).toBeVisible();
      expect(screen.getByTestId('location')).toHaveTextContent(`symbol=${added.symbol}`);
      expect(mockAdd).toHaveBeenCalledExactlyOnceWith(added.symbol);
    });

    it('does not resurrect a removed stock when an older activation refresh finishes', async () => {
      const first = stock('603306.SH', '华懋科技');
      const remaining = stock('300476.SZ', '胜宏科技');
      const oldRead = deferred<string[]>();
      mockGetWatchlist.mockResolvedValueOnce([first.symbol, remaining.symbol])
        .mockReturnValueOnce(oldRead.promise).mockResolvedValue([remaining.symbol]);
      mockRemove.mockResolvedValue([remaining.symbol]);
      respondWith(first, remaining);
      render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByRole('heading', { name: first.name, level: 2 });

      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(mockGetWatchlist).toHaveBeenCalledTimes(2);
      fireEvent.click(screen.getByRole('button', { name: `删除自选股 ${first.name}` }));
      fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
      expect(await screen.findByRole('heading', { name: remaining.name, level: 2 })).toBeVisible();
      await act(async () => oldRead.resolve([first.symbol, remaining.symbol]));

      // Its removed workspace cache has been cleared, so a resurrected rail
      // row may be labelled by code instead of its previously resolved name.
      expect(screen.queryAllByRole('button', { name: new RegExp(first.symbol.replace('.', '\\.')) })).toHaveLength(0);
      expect(screen.getByRole('heading', { name: remaining.name, level: 2 })).toBeVisible();
      expect(screen.getByTestId('location')).toHaveTextContent(`symbol=${remaining.symbol}`);
      expect(mockRemove).toHaveBeenCalledExactlyOnceWith(first.symbol);
    });

    it('serializes a removal behind an outstanding addition', async () => {
      const first = stock('603306.SH', '华懋科技');
      const added = stock('300476.SZ', '胜宏科技');
      const addition = deferred<string[]>();
      mockAdd.mockReturnValueOnce(addition.promise);
      mockRemove.mockResolvedValue([added.symbol]);
      respondWith(first, added);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('heading', { name: first.name, level: 2 });

      const input = screen.getByPlaceholderText('输入股票名称或代码');
      fireEvent.change(input, { target: { value: added.symbol } });
      await act(async () => { fireEvent.submit(input.closest('form')!); });
      fireEvent.click(screen.getByRole('button', { name: `删除自选股 ${first.name}` }));
      const prematureConfirm = screen.queryByRole('button', { name: '确认删除' });
      if (prematureConfirm) await act(async () => { fireEvent.click(prematureConfirm); });
      expect(mockRemove).not.toHaveBeenCalled();

      await act(async () => addition.resolve([first.symbol, added.symbol]));
      if (!screen.queryByRole('button', { name: '确认删除' })) {
        fireEvent.click(screen.getByRole('button', { name: `删除自选股 ${first.name}` }));
      }
      fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
      await waitFor(() => expect(mockRemove).toHaveBeenCalledExactlyOnceWith(first.symbol));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(screen.getByRole('heading', { name: added.name, level: 2 })).toBeVisible();
    });

    it('serializes an addition behind an outstanding removal', async () => {
      const first = stock('603306.SH', '华懋科技');
      const next = stock('300476.SZ', '胜宏科技');
      const removal = deferred<string[]>();
      mockRemove.mockReturnValueOnce(removal.promise);
      mockAdd.mockResolvedValue([next.symbol]);
      respondWith(first, next);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('heading', { name: first.name, level: 2 });
      const input = screen.getByPlaceholderText('输入股票名称或代码');
      // Prepare the value first; the pending confirmation must also guard the
      // form handler, not only visually cover the submit button.
      fireEvent.change(input, { target: { value: next.symbol } });
      fireEvent.click(screen.getByRole('button', { name: `删除自选股 ${first.name}` }));
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认删除' })); });
      await act(async () => { fireEvent.submit(input.closest('form')!); });
      expect(mockAdd).not.toHaveBeenCalled();

      await act(async () => removal.resolve([]));
      fireEvent.submit(input.closest('form')!);
      expect(await screen.findByRole('heading', { name: next.name, level: 2 })).toBeVisible();
      expect(mockAdd).toHaveBeenCalledExactlyOnceWith(next.symbol);
    });

    it('selects the requested existing stock on duplicate add instead of the last list item', async () => {
      const first = stock('603306.SH', '华懋科技');
      const last = stock('300476.SZ', '胜宏科技');
      mockGetWatchlist.mockResolvedValue([first.symbol, last.symbol]);
      mockAdd.mockResolvedValue([first.symbol, last.symbol]);
      respondWith(first, last);
      render(<MemoryRouter initialEntries={[`/super-watchlist?symbol=${last.symbol}`]}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByRole('heading', { name: last.name, level: 2 });

      const input = screen.getByPlaceholderText('输入股票名称或代码');
      fireEvent.change(input, { target: { value: first.name } });
      fireEvent.click(await screen.findByRole('option', { name: /华懋科技.*603306/ }));
      await waitFor(() => expect(mockAdd).toHaveBeenCalledExactlyOnceWith(first.symbol));
      await waitFor(() => expect(input).toHaveValue(''));
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`symbol=${first.symbol}`));
      expect(await screen.findByRole('heading', { name: first.name, level: 2 })).toBeVisible();
    });

    it('selects an existing bare-code stock when autocomplete submits its canonical code', async () => {
      const first = stock('603306', '华懋科技');
      const last = stock('300476', '胜宏科技');
      mockGetWatchlist.mockResolvedValue([first.symbol, last.symbol]);
      mockAdd.mockResolvedValue([first.symbol, last.symbol]);
      respondWith(first, last);
      render(<MemoryRouter initialEntries={[`/super-watchlist?symbol=${last.symbol}`]}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByRole('heading', { name: last.name, level: 2 });

      const input = screen.getByPlaceholderText('输入股票名称或代码');
      fireEvent.change(input, { target: { value: first.name } });
      fireEvent.click(await screen.findByRole('option', { name: /华懋科技.*603306/ }));
      await waitFor(() => expect(mockAdd).toHaveBeenCalledExactlyOnceWith('603306.SH'));
      await waitFor(() => expect(input).toHaveValue(''));
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('symbol=603306'));
      expect(await screen.findByRole('heading', { name: first.name, level: 2 })).toBeVisible();
    });

    it.each([
      { requested: '603306.SH', otherVenue: '603306.SZ' },
      { requested: '603306.SZ', otherVenue: '603306.SH' },
    ])('does not reinterpret $otherVenue as $requested when the add response has no matching identity', async ({ requested, otherVenue }) => {
      // Deliberately inconsistent response: a six-digit match is insufficient
      // when both identities explicitly name different exchanges.
      const wrongVenue = stock(otherVenue, '另一市场测试标的');
      const retained = stock('300476.SZ', '胜宏科技');
      mockGetWatchlist.mockResolvedValue([wrongVenue.symbol, retained.symbol]);
      mockAdd.mockResolvedValue([wrongVenue.symbol, retained.symbol]);
      respondWith(wrongVenue, retained);
      render(<MemoryRouter initialEntries={[`/super-watchlist?symbol=${retained.symbol}`]}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByRole('heading', { name: retained.name, level: 2 });
      const requestCount = mockLoad.mock.calls.length;
      const input = screen.getByPlaceholderText('输入股票名称或代码');
      fireEvent.change(input, { target: { value: requested } });
      await act(async () => { fireEvent.submit(input.closest('form')!); });

      expect(mockAdd).toHaveBeenCalledExactlyOnceWith(requested);
      expect(screen.getByTestId('location')).toHaveTextContent(`symbol=${retained.symbol}`);
      expect(screen.getByRole('heading', { name: retained.name, level: 2 })).toBeVisible();
      expect(mockLoad).toHaveBeenCalledTimes(requestCount);
      expect(mockLoad.mock.calls.some(call => call[0] === otherVenue)).toBe(false);
    });

    it('adopts a newer completed full workspace over a processing overlay and rejects a later stale poll', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis.sourceCount = 1;
      item.consensus.essayAnalysis.updatedAt = '2026-09-13T01:00:00Z';
      respondWith(item);
      const processing: EssayConsensusAnalysis = { ...item.consensus.essayAnalysis, status: 'processing', updatedAt: '2026-09-13T02:00:00Z' };
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: processing });
      mockEssayConsensus.mockResolvedValue({ symbol: item.symbol, consensus: processing });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '分析现有 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })); });

      const completed: SuperWatchlistStock = { ...item, consensus: { ...item.consensus, essayAnalysis: {
        ...processing, status: 'completed', hasReport: true, summary: '工作区已保存的新分析结果',
        updatedAt: '2026-09-13T02:01:00Z', completedAt: '2026-09-13T02:01:00Z',
      } } };
      respondWith(completed);
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(screen.getByText('工作区已保存的新分析结果')).toBeVisible();
      expect(screen.getByRole('button', { name: '更新报告 · 1 篇' })).toBeEnabled();
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(screen.getByText('工作区已保存的新分析结果')).toBeVisible();
      expect(screen.getByRole('button', { name: '更新报告 · 1 篇' })).toBeEnabled();
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
      page.unmount();
    });

    it('does not mistake an older completed full workspace for completion of a newer queued task', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis = { ...item.consensus.essayAnalysis,
        sourceCount: 1, status: 'completed', hasReport: true, summary: '前一次已完成的报告',
        updatedAt: '2026-09-13T01:00:00Z', completedAt: '2026-09-13T01:00:00Z',
      };
      respondWith(item);
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: {
        ...item.consensus.essayAnalysis, status: 'pending', updatedAt: '2026-09-13T02:00:00Z',
      } });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '更新报告 · 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新报告 · 1 篇' })); });
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });

      expect(screen.getByText('等待分析')).toBeVisible();
      expect(screen.getByRole('button', { name: '正在分析小作文' })).toBeDisabled();
      expect(screen.getByText('前一次已完成的报告')).toBeVisible();
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
      expect(mockLoad.mock.calls.filter(call => call[3] === 'full')).toHaveLength(2);
      page.unmount();
    });

    it('ignores an old poll while a newer submission has not returned its queued state yet', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis.sourceCount = 1;
      item.consensus.essayAnalysis.updatedAt = '2026-09-13T01:00:00Z';
      respondWith(item);
      const processing: EssayConsensusAnalysis = {
        ...item.consensus.essayAnalysis, status: 'processing', updatedAt: '2026-09-13T02:00:00Z',
      };
      const completed: EssayConsensusAnalysis = {
        ...processing, status: 'completed', hasReport: true, summary: '第一任务已保存结果',
        updatedAt: '2026-09-13T02:01:00Z', completedAt: '2026-09-13T02:01:00Z',
      };
      const pending: EssayConsensusAnalysis = {
        ...completed, status: 'pending', updatedAt: '2026-09-13T02:02:00Z',
      };
      const oldPoll = deferred<{ symbol: string; consensus: EssayConsensusAnalysis }>();
      const nextSubmission = deferred<{ symbol: string; consensus: EssayConsensusAnalysis }>();
      mockAnalyzeConsensus.mockResolvedValueOnce({ symbol: item.symbol, consensus: processing })
        .mockReturnValueOnce(nextSubmission.promise);
      mockEssayConsensus.mockReturnValueOnce(oldPoll.promise).mockResolvedValue({ symbol: item.symbol, consensus: pending });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '分析现有 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })); });
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(mockEssayConsensus).toHaveBeenCalledTimes(1);

      respondWith({ ...item, consensus: { ...item.consensus, essayAnalysis: completed } });
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新报告 · 1 篇' })); });
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(2);
      await act(async () => oldPoll.resolve({ symbol: item.symbol, consensus: { ...completed, summary: '不该覆盖新提交的旧轮询结果' } }));
      expect(screen.getByRole('button', { name: '正在分析小作文' })).toBeDisabled();
      expect(screen.getByText(/正在提交当前股票/)).toBeVisible();
      expect(screen.queryByText('不该覆盖新提交的旧轮询结果')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '正在分析小作文' }));
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(2);

      await act(async () => nextSubmission.resolve({ symbol: item.symbol, consensus: pending }));
      expect(screen.getByText('等待分析')).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(mockEssayConsensus).toHaveBeenCalledTimes(2);
      expect(screen.getByText('等待分析')).toBeVisible();
      page.unmount();
    });

    it('does not infer completion from an undated legacy workspace while the current task is queued', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis = {
        ...item.consensus.essayAnalysis, status: 'completed', sourceCount: 1, summary: '无任务时间的旧报告',
      };
      respondWith(item);
      const pending: EssayConsensusAnalysis = { ...item.consensus.essayAnalysis, status: 'pending' };
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: pending });
      mockEssayConsensus.mockResolvedValue({ symbol: item.symbol, consensus: pending });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '更新报告 · 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新报告 · 1 篇' })); });
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(screen.getByText('等待分析')).toBeVisible();
      expect(screen.getByRole('button', { name: '正在分析小作文' })).toBeDisabled();
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(mockEssayConsensus).toHaveBeenCalledExactlyOnceWith(item.symbol);
      expect(screen.getByText('等待分析')).toBeVisible();
      expect(screen.queryByText(/^分析完成：/)).not.toBeInTheDocument();
      page.unmount();
    });

    it('keeps polling when a stale completed status response only matches an older full workspace', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis = {
        ...item.consensus.essayAnalysis, sourceCount: 1, status: 'completed', hasReport: true,
        summary: '上一轮已保存报告', updatedAt: '2026-09-13T01:00:00Z', completedAt: '2026-09-13T01:00:00Z',
      };
      respondWith(item);
      const queued: EssayConsensusAnalysis = {
        ...item.consensus.essayAnalysis, status: 'pending', updatedAt: '2026-09-13T02:00:00Z',
      };
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: queued });
      mockEssayConsensus.mockResolvedValueOnce({ symbol: item.symbol, consensus: item.consensus.essayAnalysis })
        .mockResolvedValue({ symbol: item.symbol, consensus: { ...queued, status: 'processing', updatedAt: '2026-09-13T02:00:05Z' } });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '更新报告 · 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新报告 · 1 篇' })); });
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(screen.getByText('等待分析')).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(5_000));

      expect(mockEssayConsensus).toHaveBeenCalledTimes(2);
      expect(screen.getByText('AI 分析中')).toBeVisible();
      expect(screen.getByRole('button', { name: '正在分析小作文' })).toBeDisabled();
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
      page.unmount();
    });

    it('refreshes candidate materials when the saved report updatedAt is unchanged', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis.sourceCount = 1;
      item.consensus.essayAnalysis.updatedAt = '2026-09-13T01:00:00Z';
      respondWith(item);
      const processing: EssayConsensusAnalysis = {
        ...item.consensus.essayAnalysis, status: 'processing', updatedAt: '2026-09-13T02:00:00Z',
      };
      const completed: EssayConsensusAnalysis = {
        ...processing, status: 'completed', hasReport: true, summary: '保持原报告并更新候选材料',
        updatedAt: '2026-09-13T02:01:00Z', completedAt: '2026-09-13T02:01:00Z',
        reportSourceCount: 1, relatedSourceCount: 1, dedicatedSourceCount: 0,
        sourceNotes: [{ topicId: 'used-original', title: '实际分析过的原文', sourceKind: 'related', estimateCount: 0 }],
      };
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: processing });
      mockEssayConsensus.mockResolvedValue({ symbol: item.symbol, consensus: completed });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '分析现有 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })); });
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(screen.getByRole('button', { name: '更新报告 · 1 篇' })).toBeEnabled();

      // Candidate discovery changes this read projection, not the saved task
      // record's updatedAt or the prior report's completedAt/source notes.
      respondWith({ ...item, consensus: { ...item.consensus, essayAnalysis: {
        ...completed, status: 'stale', sourceCount: 2, relatedSourceCount: 2,
        candidateCutoffAt: '2026-09-13T02:03:00Z', candidateSourceNotes: [
          ...completed.sourceNotes,
          { topicId: 'new-candidate', title: '新入库尚未分析原文', sourceKind: 'related', estimateCount: 0 },
        ],
      } } });
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(screen.getByRole('button', { name: '更新报告 · 2 篇' })).toBeEnabled();
      expect(screen.getByText('有新小作文待更新')).toBeVisible();
      expect(screen.getByText('保持原报告并更新候选材料')).toBeVisible();
      expect(screen.getByRole('region', { name: '研究样本范围' })).toHaveTextContent('已读取 1 篇');
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
      page.unmount();
    });

    it.each(['network failure', 'polling timeout'] as const)('recovers saved completion after a %s without submitting another AI task', async (failure) => {
      const item = stock('603306.SH', '华懋科技');
      item.consensus.essayAnalysis.sourceCount = 1;
      item.consensus.essayAnalysis.updatedAt = '2026-09-13T01:00:00Z';
      respondWith(item);
      const processing: EssayConsensusAnalysis = { ...item.consensus.essayAnalysis, status: 'processing', updatedAt: '2026-09-13T02:00:00Z' };
      mockAnalyzeConsensus.mockResolvedValue({ symbol: item.symbol, consensus: processing });
      if (failure === 'network failure') mockEssayConsensus.mockRejectedValue(new Error('status polling unavailable'));
      else mockEssayConsensus.mockResolvedValue({ symbol: item.symbol, consensus: processing });
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('button', { name: '分析现有 1 篇' });
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })); });
      await act(async () => vi.advanceTimersByTimeAsync(failure === 'network failure' ? 5_000 : 180_000));
      expect(mockEssayConsensus).toHaveBeenCalledTimes(failure === 'network failure' ? 1 : 36);
      expect(screen.getByText(failure === 'network failure' ? 'status polling unavailable' : /后台仍在分析/)).toBeVisible();

      respondWith({ ...item, consensus: { ...item.consensus, essayAnalysis: {
        ...processing, status: 'completed', hasReport: true, summary: '轮询停止后实际保存的完成结果',
        updatedAt: '2026-09-13T02:04:00Z', completedAt: '2026-09-13T02:04:00Z',
      } } });
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(screen.getByText('轮询停止后实际保存的完成结果')).toBeVisible();
      expect(screen.getByRole('button', { name: '更新报告 · 1 篇' })).toBeEnabled();
      expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
      page.unmount();
    });
  });

  describe('library controls regressions', () => {
    it('clears query, material and pagination together without requesting more data', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.company.announcements = Array.from({ length: 13 }, (_, index) => ({
        ...essay(700 + index, `已披露公告 ${index + 1}`), eventType: 'company_announcement',
      }));
      item.institution.latest = [{ ...essay(800, '另一类型券商报告'), eventType: 'research_report_pdf', eventAt: '2026-09-13T01:00:00Z' }];
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=institution&material=announcements']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const query = await screen.findByRole('searchbox', { name: '筛选已载入资料' });
      fireEvent.change(query, { target: { value: '已披露公告' } });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      const requestCount = mockLoad.mock.calls.length;
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '清除资料筛选' })); });

      expect(query).toHaveValue('');
      expect(screen.getByRole('combobox', { name: '资料筛选' })).toHaveValue('all');
      expect(screen.getByTestId('location')).not.toHaveTextContent('material=');
      expect(screen.getByText('14 条已载入资料')).toBeVisible();
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 1 / 2 页')).toBeVisible();
      expect(screen.getByText('另一类型券商报告')).toBeVisible();
      expect(screen.queryByRole('button', { name: '清除资料筛选' })).not.toBeInTheDocument();
      expect(mockLoad).toHaveBeenCalledTimes(requestCount);
      expect(mockEvent).not.toHaveBeenCalled();
    });

    it('puts usable pagination before the list and keeps focus in results at the last page', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.alternative.essays = Array.from({ length: 25 }, (_, index) => essay(900 + index, `分页资料 ${index + 1}`));
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=essay']}><SuperWatchlistPage /></MemoryRouter>);
      const results = await screen.findByLabelText('资料列表');
      const pagination = screen.getByRole('navigation', { name: '资料分页' });
      const next = within(pagination).getByRole('button', { name: '下一页' });
      expect(pagination.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(pagination).getByRole('button', { name: '上一页' })).toBeDisabled();
      expect(next).toBeEnabled();
      const requestCount = mockLoad.mock.calls.length;

      next.focus();
      fireEvent.click(next);
      expect(results).toHaveFocus();
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 3 页')).toBeVisible();
      next.focus();
      fireEvent.click(next);
      expect(results).toHaveFocus();
      expect(next).toBeDisabled();
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 3 / 3 页')).toBeVisible();
      expect(within(results).getAllByRole('button', { name: /查看原文/ })).toHaveLength(1);
      fireEvent.click(within(pagination).getByRole('button', { name: '上一页' }));
      expect(results).toHaveFocus();
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 3 页')).toBeVisible();
      expect(mockLoad).toHaveBeenCalledTimes(requestCount);
      expect(mockNote).not.toHaveBeenCalled();
    });

    it('keeps full-library search available while the reading-scope explanation is collapsed', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.alternative.essayCount = 321;
      item.alternative.essays = [essay(990, '已载入的近期原文')];
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=essay']}><SuperWatchlistPage /></MemoryRouter>);
      const scope = (await screen.findByText('读取范围')).closest('details')!;
      const link = screen.getByRole('link', { name: '用“华懋科技”检索全库' });
      expect(scope).not.toHaveAttribute('open');
      expect(within(scope).getByText(/共匹配 321 篇/)).not.toBeVisible();
      expect(link).toBeVisible();
      expect(link.closest('details')).toBeNull();
      expect(link).toHaveAttribute('href', `/essay-radar/feed?query=${encodeURIComponent(item.name)}`);
      const requestCount = mockLoad.mock.calls.length;
      fireEvent.click(within(scope).getByText('读取范围'));
      expect(within(scope).getByText(/共匹配 321 篇/)).toBeVisible();
      fireEvent.click(within(scope).getByText('读取范围'));
      expect(scope).not.toHaveAttribute('open');
      expect(link).toBeVisible();
      expect(mockLoad).toHaveBeenCalledTimes(requestCount);
    });
  });

  describe('research reading position', () => {
    function readingStock(symbol = '603306.SH', name = '华懋科技') {
      const item = stock(symbol, name);
      item.company.announcements = Array.from({ length: 13 }, (_, index) => ({
        ...essay(4100 + index, `${name}已披露公告 ${index + 1}`), eventType: 'company_announcement', symbols: [symbol],
      }));
      item.messages.items = Array.from({ length: 20 }, (_, index) => ({
        ...essay(4200 + index, `${name}新闻记录 ${index + 1}`), eventType: 'news', symbols: [symbol],
      }));
      return item;
    }
    function checkAnnouncementPosition(query = '华懋科技已披露公告') {
      expect(screen.getByRole('button', { name: '公告研报', pressed: true })).toBeVisible();
      expect(screen.getByRole('combobox', { name: '资料筛选' })).toHaveValue('announcements');
      expect(screen.getByRole('searchbox', { name: '筛选已载入资料' })).toHaveValue(query);
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      expect(within(screen.getByLabelText('资料列表')).getAllByRole('button')).toHaveLength(1);
    }
    function HistoryDestination() {
      const navigate = useNavigate();
      return <section><h1>导航目的地样例</h1><button onClick={() => navigate(-1)}>返回原研究位置</button></section>;
    }

    it('keeps the exact material, query and page after visiting finance without re-fetching full data', async () => {
      respondWith(readingStock());
      render(<MemoryRouter initialEntries={['/super-watchlist?section=institution&material=announcements']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const input = await screen.findByRole('searchbox', { name: '筛选已载入资料' });
      fireEvent.change(input, { target: { value: '华懋科技已披露公告' } });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      checkAnnouncementPosition();
      const calls = mockLoad.mock.calls.length;
      const currentUrl = screen.getByTestId('location').textContent;
      // Clicking an already selected parent or child must not reset research.
      fireEvent.click(screen.getByRole('button', { name: '资料库' }));
      fireEvent.click(screen.getByRole('button', { name: '公告研报' }));
      checkAnnouncementPosition();
      expect(screen.getByTestId('location').textContent).toBe(currentUrl);
      fireEvent.click(screen.getByRole('button', { name: '财务与资金' }));
      expect(screen.getByRole('heading', { name: '收入与利润' })).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: '资料库' }));
      checkAnnouncementPosition();
      expect(mockLoad).toHaveBeenCalledTimes(calls);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('remembers independent child sections while an explicit overview entry starts a fresh scope', async () => {
      respondWith(readingStock());
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=institution&material=announcements']}><SuperWatchlistPage /></MemoryRouter>);
      fireEvent.change(await screen.findByRole('searchbox'), { target: { value: '华懋科技已披露公告' } });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      fireEvent.click(screen.getByRole('button', { name: '新闻与提示' }));
      expect(screen.getByRole('searchbox')).toHaveValue('');
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: '新闻记录' } });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      expect(within(screen.getByLabelText('资料列表')).getAllByRole('button')).toHaveLength(8);
      fireEvent.click(screen.getByRole('button', { name: '公告研报' }));
      checkAnnouncementPosition();
      fireEvent.click(screen.getByRole('button', { name: '新闻与提示' }));
      expect(screen.getByRole('searchbox')).toHaveValue('新闻记录');
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      expect(mockLoad).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole('button', { name: '总览' }));
      fireEvent.click(await screen.findByRole('button', { name: '查看公司公告' }));
      expect(await screen.findByRole('searchbox')).toHaveValue('');
      expect(screen.getByRole('combobox', { name: '资料筛选' })).toHaveValue('announcements');
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 1 / 2 页')).toBeVisible();
    });

    it.each(['tasks', 'chat'])('restores a deep-linked reading position after leaving for %s and going back', async destination => {
      respondWith(readingStock());
      const query = encodeURIComponent('华懋科技已披露公告');
      render(<MemoryRouter initialEntries={[`/super-watchlist?symbol=603306.SH&section=institution&material=announcements&query=${query}&page=2`]}>
        <Routes><Route path="/super-watchlist" element={<SuperWatchlistPage />} /><Route path="/tasks" element={<HistoryDestination />} /><Route path="/chat" element={<HistoryDestination />} /></Routes><LocationProbe />
      </MemoryRouter>);
      await screen.findByRole('searchbox');
      checkAnnouncementPosition();
      fireEvent.click(destination === 'tasks' ? screen.getByRole('link', { name: '我的研究任务' }) : screen.getByRole('button', { name: '问 AI 这只股票' }));
      expect(await screen.findByRole('heading', { name: '导航目的地样例' })).toBeVisible();
      expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '返回原研究位置' }));
      await screen.findByRole('searchbox');
      checkAnnouncementPosition();
      expect(new URLSearchParams(screen.getByTestId('location').textContent!).get('query')).toBe('华懋科技已披露公告');
      expect(mockLoad.mock.calls.every(call => call[0] === '603306.SH' && call[3] === 'full')).toBe(true);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('does not apply the previous company keyword or page when switching stock', async () => {
      const first = readingStock(), next = readingStock('300476.SZ', '胜宏科技');
      mockGetWatchlist.mockResolvedValue([first.symbol, next.symbol]);
      respondWith(first, next);
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=institution&material=announcements&query=华懋科技已披露公告&page=2']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByRole('searchbox');
      checkAnnouncementPosition();
      fireEvent.click(screen.getByRole('button', { name: /^胜宏科技.*300476.SZ/ }));
      await screen.findByRole('heading', { name: '胜宏科技' });
      expect(screen.getByRole('searchbox')).toHaveValue('');
      expect(screen.getByRole('combobox', { name: '资料筛选' })).toHaveValue('announcements');
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 1 / 2 页')).toBeVisible();
      expect(screen.getByLabelText('资料列表')).not.toHaveTextContent('华懋科技已披露公告');
      expect(new URLSearchParams(screen.getByTestId('location').textContent!).has('query')).toBe(false);
      expect(new URLSearchParams(screen.getByTestId('location').textContent!).has('page')).toBe(false);
    });

    it('restores browser page history without one back entry per typed character', async () => {
      function BrowserHistory() { const navigate = useNavigate(); return <button onClick={() => navigate(-1)}>测试上一页历史</button>; }
      respondWith(readingStock());
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=institution&material=announcements']}><SuperWatchlistPage /><BrowserHistory /></MemoryRouter>);
      const input = await screen.findByRole('searchbox');
      for (const value of ['华', '华懋', '华懋科技']) fireEvent.change(input, { target: { value } });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: '测试上一页历史' }));
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 1 / 2 页')).toBeVisible();
      expect(input).toHaveValue('华懋科技');
      expect(mockLoad).toHaveBeenCalledTimes(1);
    });
  });

  describe('news and notices', () => {
    function messageStock() {
      const item = stock('603306.SH', '华懋科技');
      const make = (id: number, title: string, eventType: string): MonitorEvent => ({
        ...essay(id, title), eventType, sourceKey: `source.${eventType}`, sourceName: `来源${eventType}`,
        externalId: `message-${id}`, eventAt: `2026-09-10T08:${String(id % 60).padStart(2, '0')}:00Z`,
      });
      const news = Array.from({ length: 20 }, (_, i) => make(3000 + i, `新闻记录${i + 1}`, i % 2 ? 'long_news' : 'news'));
      const opinions = Array.from({ length: 10 }, (_, i) => make(3100 + i, `机构观点${i + 1}`, 'essay'));
      const enterprise = Array.from({ length: 10 }, (_, i) => make(3200 + i, `企业资料${i + 1}`, ['enterprise_registration', 'enterprise_risk', 'enterprise_credit', 'enterprise_ipr', 'enterprise_history'][i % 5]));
      item.messages = { count: 500, items: [...opinions, ...enterprise, ...news], channels: [] };
      return item;
    }
    const typeButton = (name: string) => within(screen.getByRole('group', { name: '消息类型，数量为已载入条数' })).getByRole('button', { name: new RegExp(`^${name}，`) });
    const results = () => screen.getByLabelText('资料列表');

    it('defaults to actual news, paginates only that type and reads a record without another workspace or AI request', async () => {
      const item = messageStock();
      respondWith(item);
      mockEvent.mockImplementation(async (id: number) => item.messages.items.find(event => event.id === id));
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=messages']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      await screen.findByText('20 条已载入资料');
      expect(typeButton('相关新闻')).toHaveAttribute('aria-pressed', 'true');
      expect(typeButton('相关新闻')).toHaveAccessibleName('相关新闻，20条已载入');
      expect(typeButton('机构观点')).toHaveAccessibleName('机构观点，10条已载入');
      expect(typeButton('企业提示')).toHaveAccessibleName('企业提示，10条已载入');
      expect(typeButton('全部消息')).toHaveAccessibleName('全部消息，40条已载入');
      expect(within(results()).getAllByRole('button')).toHaveLength(12);
      expect(results()).not.toHaveTextContent('机构观点');
      expect(results()).not.toHaveTextContent('企业资料');
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      expect(within(results()).getAllByRole('button')).toHaveLength(8);
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      const record = within(results()).getAllByRole('button')[0];
      record.focus();
      fireEvent.click(record);
      await screen.findByRole('dialog');
      await waitFor(() => expect(mockEvent).toHaveBeenCalledTimes(1));
      expect(item.messages.items.find(event => event.id === mockEvent.mock.calls[0][0])?.eventType).toMatch(/^(news|long_news)$/);
      fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
      expect(record).toHaveFocus();
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockNote).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('keeps keywords across type changes, clears only keywords and resets clicked-type pagination', async () => {
      respondWith(messageStock());
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=messages']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const query = await screen.findByRole('searchbox', { name: '筛选已载入资料' });
      fireEvent.click(screen.getByRole('button', { name: '下一页' }));
      fireEvent.click(typeButton('机构观点'));
      expect(screen.getByText('10 条已载入资料')).toBeVisible();
      fireEvent.click(typeButton('相关新闻'));
      expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 1 / 2 页')).toBeVisible();
      fireEvent.change(query, { target: { value: '记录1' } });
      expect(screen.getByText('11 条已载入资料')).toBeVisible();
      fireEvent.click(typeButton('企业提示'));
      expect(query).toHaveValue('记录1');
      expect(screen.getByText('没有匹配的已载入资料')).toBeVisible();
      expect(typeButton('企业提示')).toHaveAccessibleName('企业提示，10条已载入');
      fireEvent.click(screen.getByRole('button', { name: '清除关键词' }));
      expect(query).toHaveValue('');
      expect(typeButton('企业提示')).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByTestId('location')).toHaveTextContent('message=enterprise');
      expect(screen.getByText('10 条已载入资料')).toBeVisible();
      expect(results()).toHaveTextContent('企业资料');
      expect(results()).not.toHaveTextContent('新闻记录');
      expect(mockLoad).toHaveBeenCalledTimes(1);
      expect(mockEvent).not.toHaveBeenCalled();
    });

    it.each(['', '&message=invalid'])('does not substitute opinions or unknown types when news is missing (%s)', async suffix => {
      const item = messageStock();
      item.messages.items = [essay(3901, '某机构的传闻'), { ...essay(3902, '未知类型记录'), eventType: 'enterprise_other' }];
      respondWith(item);
      render(<MemoryRouter initialEntries={[`/super-watchlist?symbol=603306.SH&section=messages${suffix}`]}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByText('暂无已载入新闻');
      expect(results()).not.toHaveTextContent('某机构的传闻');
      expect(results()).not.toHaveTextContent('未知类型记录');
      expect(typeButton('企业提示')).toHaveAccessibleName('企业提示，0条已载入');
      fireEvent.click(typeButton('全部消息'));
      expect(within(results()).getByText('某机构的传闻')).toBeVisible();
      expect(within(results()).getByText('未知类型记录')).toBeVisible();
      expect(within(results()).getByText('未分类消息')).toBeVisible();
      expect(within(results()).getAllByText('待核验')).toHaveLength(2);
      expect(mockLoad).toHaveBeenCalledTimes(1);
    });

    it('opens overview news in the same stock with the news scope instead of a previous enterprise filter', async () => {
      const item = messageStock();
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&message=enterprise']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      fireEvent.click(await screen.findByRole('button', { name: '查看相关新闻' }));
      await screen.findByText('20 条已载入资料');
      expect(typeButton('相关新闻')).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByTestId('location')).not.toHaveTextContent('message=enterprise');
      expect(screen.getByTestId('location')).toHaveTextContent('symbol=603306.SH');
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
    });

    it('honors a direct link and browser history without fetching the full workspace again', async () => {
      function HistoryControls() { const navigate = useNavigate(); return <><button onClick={() => navigate(-1)}>测试后退</button><button onClick={() => navigate(1)}>测试前进</button></>; }
      const item = messageStock();
      // Duplicate record does not inflate loaded counts.
      item.messages.items.push(item.messages.items[0]);
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?symbol=603306.SH&section=messages&message=enterprise']}><SuperWatchlistPage /><LocationProbe /><HistoryControls /></MemoryRouter>);
      await screen.findByText('10 条已载入资料');
      expect(typeButton('企业提示')).toHaveAttribute('aria-pressed', 'true');
      expect(typeButton('全部消息')).toHaveAccessibleName('全部消息，40条已载入');
      fireEvent.click(typeButton('机构观点'));
      expect(results()).toHaveTextContent('机构观点');
      fireEvent.click(screen.getByRole('button', { name: '测试后退' }));
      expect(typeButton('企业提示')).toHaveAttribute('aria-pressed', 'true');
      expect(results()).not.toHaveTextContent('机构观点');
      fireEvent.click(screen.getByRole('button', { name: '测试前进' }));
      expect(typeButton('机构观点')).toHaveAttribute('aria-pressed', 'true');
      expect(mockLoad).toHaveBeenCalledTimes(1);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });
  });

  describe('overview business brief', () => {
    function card(label: string) {
      return within(screen.getByRole('region', { name: '经营与估值摘要' })).getByRole('region', { name: label });
    }

    it('starts with a compact chart and resizes the same chart without reloading the workspace or starting AI', async () => {
      const item = financialBriefStock();
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const enlarge = await screen.findByRole('button', { name: '放大行情图' });
      const chart = screen.getByTestId('market-chart');
      expect(chart).toHaveAttribute('data-compact', 'true');
      expect(enlarge).toHaveAttribute('aria-pressed', 'false');
      fireEvent.click(enlarge);
      expect(screen.getByTestId('market-chart')).toBe(chart);
      expect(chart).toHaveAttribute('data-compact', 'false');
      fireEvent.click(screen.getByRole('button', { name: '还原紧凑图', pressed: true }));
      expect(screen.getByTestId('market-chart')).toBe(chart);
      expect(chart).toHaveAttribute('data-compact', 'true');
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'overview');
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('retains at most two actual records per compact channel and all four scoped full-list entries', async () => {
      const item = financialBriefStock();
      item.company.announcements = [];
      item.institution.latest = [{ ...essay(2901, '只读预览研报'), eventType: 'research_report_pdf' }];
      item.alternative.essays = [essay(2902, '预览观点一'), essay(2903, '预览观点二')];
      item.messages.items = [1, 2, 3].map(index => ({ ...essay(2910 + index, `预览新闻${index}`), eventType: 'news' }));
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const brief = await screen.findByRole('region', { name: '当前股票研究摘要' });
      expect(brief).toHaveTextContent('近183天已入库预览');
      expect(brief).toHaveTextContent('不是全库清单');
      const channel = (name: string) => within(brief).getByRole('region', { name: `${name}预览` });
      expect(channel('公司公告')).toHaveTextContent('当前预览未载入');
      for (const [label, count] of [['公司公告', 0], ['券商研究', 1], ['机构段子', 2], ['相关新闻', 2]] as const) {
        expect(channel(label).querySelectorAll('.super-evidence-row')).toHaveLength(count);
        expect(within(channel(label)).getByRole('button', { name: `查看${label}` })).toBeVisible();
      }
      for (const title of ['只读预览研报', '预览观点一', '预览观点二', '预览新闻1', '预览新闻2']) {
        expect(within(brief).getByRole('button', { name: new RegExp(title) })).toBeVisible();
      }
      expect(brief).not.toHaveTextContent('预览新闻3');
      expect(mockLoad).toHaveBeenCalledTimes(1);
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockNote).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('keeps empty preview channels navigable without implying the database has no documents', async () => {
      respondWith(financialBriefStock());
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const brief = await screen.findByRole('region', { name: '当前股票研究摘要' });
      expect(within(brief).getAllByText('当前预览未载入')).toHaveLength(4);
      expect(brief.querySelectorAll('.super-evidence-row')).toHaveLength(0);
      expect(within(brief).getAllByRole('button', { name: /^查看/ })).toHaveLength(4);
      expect(brief).not.toHaveTextContent(/无公告|无研报|没有新闻|近183天无/);
      expect(mockLoad).toHaveBeenCalledTimes(1);
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('shows four available business facts with separate amount, indicator and valuation dates from the API wire shape', async () => {
      const item = financialBriefStock();
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const summary = await screen.findByRole('region', { name: '经营与估值摘要' });
      expect(summary.compareDocumentPosition(screen.getByTestId('market-chart')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(summary.querySelectorAll('.super-business-metric')).toHaveLength(4);
      const revenue = card('营业收入');
      const profit = card('归母净利润');
      const cashflow = card('经营现金流净额');
      const valuation = card('PE(TTM)');
      expect(revenue.querySelector('strong')).toHaveTextContent(/^12\.34 亿元$/);
      expect(profit.querySelector('strong')).toHaveTextContent(/^1\.2 亿元$/);
      expect(cashflow.querySelector('strong')).toHaveTextContent(/^8,765 万元$/);
      expect(valuation.querySelector('strong')).toHaveTextContent(/^21\.75 倍$/);
      expect(within(revenue).getByText('报告期 2026-03-31')).toBeVisible();
      expect(within(profit).getByText('报告期 2026-03-31')).toBeVisible();
      expect(within(cashflow).getByText('报告期 2025-12-31')).toBeVisible();
      expect(within(valuation).getByText('估值日期 2026-09-11')).toBeVisible();
      expect(revenue.querySelector('.super-business-growth')).toHaveTextContent('单季收入同比');
      expect(revenue.querySelector('.super-business-growth')).toHaveTextContent('+12.50%');
      expect(revenue.querySelector('.super-business-growth')).toHaveTextContent('指标期 2026-06-30');
      expect(profit.querySelector('.super-business-growth')).toHaveTextContent('累计归母利润同比');
      expect(profit.querySelector('.super-business-growth')).toHaveTextContent('-3.20%');
      expect(profit.querySelector('.super-business-growth')).toHaveTextContent('指标期 2026-06-30');
      expect(cashflow).not.toHaveTextContent('2026-06-30');
      expect(valuation).not.toHaveTextContent('2026-03-31');
      expect(summary).not.toHaveTextContent('2026-09-30');
      expect(summary).not.toHaveTextContent(/累计营业收入|累计归母净利润/);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'overview');
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('keeps zero values and units through financial details while missing dates do not borrow other periods', async () => {
      const item = financialBriefStock();
      item.fundamentals = toCamelCase<SuperWatchlistStock['fundamentals']>({
        period: '20260630', indicator_period: null, income_period: null, cashflow_period: null,
        revenue: 0, net_profit: 0, operating_cashflow: 0,
        revenue_yoy: 0, revenue_yoy_field: 'q_sales_yoy', net_profit_yoy: 0, net_profit_yoy_field: 'netprofit_yoy',
      });
      item.valuation = { peTtm: 0, totalMv: 0, asOf: null };
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const summary = await screen.findByRole('region', { name: '经营与估值摘要' });
      for (const label of ['营业收入', '归母净利润', '经营现金流净额']) {
        expect(card(label).querySelector('strong')).toHaveTextContent(/^0 元$/);
        expect(within(card(label)).getByText('报告期 未标注')).toBeVisible();
      }
      expect(card('PE(TTM)').querySelector('strong')).toHaveTextContent(/^0 倍$/);
      expect(within(card('PE(TTM)')).getByText('估值日期 未标注')).toBeVisible();
      for (const label of ['营业收入', '归母净利润']) {
        expect(card(label).querySelector('.super-business-growth')).toHaveTextContent('0.00%');
        expect(card(label).querySelector('.super-business-growth')).toHaveTextContent('指标期 未标注');
      }
      expect(summary).not.toHaveTextContent(/2026-06-30|20260630|2026-09-13/);
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看财务与资金' })); });
      const details = await screen.findByRole('region', { name: '财务与估值详情' });
      for (const label of ['营业收入', '归母净利润', '经营现金流净额', '总市值']) {
        expect(within(details).getByText(label).closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^0 元$/);
      }
      expect(within(details).getByText('PE(TTM)').closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^0 倍$/);
      expect(within(details).getByText(/财务指标期 未标注/)).toBeVisible();
      expect(within(details).getByText(/估值日期 未标注/)).toBeVisible();
      expect(within(details).getByText('部分报表未标注期间，暂不跨表比较利润与现金流。')).toBeVisible();
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('shows missing and non-finite financial values as unknown rather than zero', async () => {
      const item = financialBriefStock();
      item.fundamentals = {
        ...item.fundamentals, revenue: null, netProfit: Number.NaN, operatingCashflow: Number.POSITIVE_INFINITY,
        revenueYoy: null, netProfitYoy: Number.NaN,
      };
      item.valuation.peTtm = null;
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const summary = await screen.findByRole('region', { name: '经营与估值摘要' });
      for (const label of ['营业收入', '归母净利润', '经营现金流净额', 'PE(TTM)']) {
        expect(card(label).querySelector('strong')).toHaveTextContent(/^—$/);
      }
      expect(summary).not.toHaveTextContent(/NaN|Infinity|0\.00%/);
    });

    it('preserves negative profit and cashflow values and units through financial details without trading signals', async () => {
      const item = financialBriefStock();
      item.fundamentals.netProfit = -120_000_000;
      item.fundamentals.operatingCashflow = -21_000_000;
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('region', { name: '经营与估值摘要' });
      expect(card('归母净利润').querySelector('strong')).toHaveTextContent(/^-1\.2 亿元$/);
      expect(card('经营现金流净额').querySelector('strong')).toHaveTextContent(/^-2,100 万元$/);
      for (const label of ['归母净利润', '经营现金流净额']) {
        expect(card(label)).not.toHaveTextContent(/买入|卖出|看多|看空|利好|利空/);
        expect(card(label).querySelector('.is-up, .is-down, .is-inflow, .is-outflow')).toBeNull();
      }
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看财务与资金' })); });
      const details = await screen.findByRole('region', { name: '财务与估值详情' });
      for (const [label, value] of [['营业收入', '12.34 亿元'], ['归母净利润', '-1.2 亿元'], ['经营现金流净额', '-2,100 万元'], ['PE(TTM)', '21.75 倍']]) {
        const metric = within(details).getByText(label).closest('.super-metric')!;
        expect(metric.querySelector('strong')?.textContent).toBe(value);
        expect(metric).not.toHaveTextContent(/买入|卖出|看多|看空|利好|利空/);
        expect(metric.querySelector('.is-up, .is-down, .is-inflow, .is-outflow')).toBeNull();
      }
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it.each([
      { revenueField: 'q_sales_yoy', revenueLabel: '单季收入同比', profitField: 'q_netprofit_yoy', profitLabel: '单季归母利润同比' },
      { revenueField: 'or_yoy', revenueLabel: '累计收入同比', profitField: 'netprofit_yoy', profitLabel: '累计归母利润同比' },
      { revenueField: 'tr_yoy', revenueLabel: '累计总收入同比', profitField: null, profitLabel: '利润同比（来源口径）' },
    ])('keeps $revenueField growth separate from the reported amount period', async ({ revenueField, revenueLabel, profitField, profitLabel }) => {
      const item = financialBriefStock();
      item.fundamentals.revenueYoyField = revenueField;
      item.fundamentals.netProfitYoyField = profitField;
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('region', { name: '经营与估值摘要' });
      expect(card('营业收入').querySelector('.super-business-growth')).toHaveTextContent(revenueLabel);
      expect(card('归母净利润').querySelector('.super-business-growth')).toHaveTextContent(profitLabel);
      for (const label of ['营业收入', '归母净利润']) {
        expect(within(card(label)).getByText('报告期 2026-03-31')).toBeVisible();
        expect(card(label).querySelector('.super-business-growth')).toHaveTextContent('指标期 2026-06-30');
      }
    });

    it.each([
      { button: '查看公司公告', section: 'institution', material: 'announcements', title: '总览公司的公告' },
      { button: '查看券商研究', section: 'institution', material: 'research', title: '总览公司的研报' },
      { button: '查看机构段子', section: 'essay', material: null, title: '总览公司的小作文' },
      { button: '查看相关新闻', section: 'messages', material: null, title: '总览公司的新闻' },
    ])('keeps the $button entry attached to the selected stock and correct detail scope', async ({ button, section, material, title }) => {
      const item = financialBriefStock();
      item.company.announcements = [{ ...essay(2001, '总览公司的公告'), eventType: 'company_announcement' }];
      item.institution.latest = [{ ...essay(2002, '总览公司的研报'), eventType: 'research_report_pdf' }];
      item.alternative.essays = [essay(2003, '总览公司的小作文')];
      item.messages.items = [{ ...essay(2004, '总览公司的新闻'), eventType: 'news' }];
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: button });
      await act(async () => { fireEvent.click(entry); });
      expect(await screen.findByText(title)).toBeVisible();
      const location = new URLSearchParams(screen.getByTestId('location').textContent || '');
      expect(location.get('symbol')).toBe(item.symbol);
      expect(location.get('section')).toBe(section);
      expect(location.get('material')).toBe(material);
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('opens full financial details from the four-card brief without starting analysis', async () => {
      const item = financialBriefStock();
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '查看财务与资金' });
      await act(async () => { fireEvent.click(entry); });
      expect(await screen.findByRole('heading', { name: '收入与利润' })).toBeVisible();
      expect(screen.getByTestId('location')).toHaveTextContent('section=fundamental');
      expect(screen.getByTestId('location')).toHaveTextContent(`symbol=${item.symbol}`);
      expect(screen.getByRole('button', { name: '财务估值', pressed: true })).toBeVisible();
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('does not borrow legacy or income periods for missing indicator dates in the brief, financial details or source notes', async () => {
      const item = financialBriefStock();
      delete item.fundamentals.indicatorPeriod;
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('region', { name: '经营与估值摘要' });
      for (const label of ['营业收入', '归母净利润']) {
        expect(card(label).querySelector('.super-business-growth')).toHaveTextContent('指标期 未标注');
        expect(within(card(label)).getByText('报告期 2026-03-31')).toBeVisible();
      }
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看财务与资金' })); });
      await screen.findByRole('heading', { name: '收入与利润' });
      const details = screen.getByRole('region', { name: '财务与估值详情' });
      const header = within(details).getByRole('heading', { name: '财务指标' }).closest('header')!;
      expect(within(header).getByText(/财务指标期 未标注/)).toBeVisible();
      expect(within(details).getByRole('region', { name: '收入与利润' })).toHaveTextContent('利润表期 2026-03-31');
      expect(within(details).getByRole('region', { name: '经营现金流' })).toHaveTextContent('现金流量表期 2025-12-31');
      expect(header).not.toHaveTextContent(/20260930|2026-09-30|2026-06-30/);
      const notes = screen.getByText('数据来源与使用说明').closest('details')!;
      fireEvent.click(within(notes).getByText('数据来源与使用说明'));
      expect(within(notes).getByText(/财务指标期：未标注/)).toBeVisible();
      expect(notes).not.toHaveTextContent(/财务指标期：(2026-03-31|2026-09-30|20260930)/);
      expect(mockLoad.mock.calls.map(call => call[3])).toEqual(['overview', 'full']);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });
  });

  describe('financial detail usability', () => {
    it.each([
      { kind: 'positive levels', netMargin: 12.5, grossMargin: 32.8, roe: 9.4, debtRatio: 60, currentRatio: 1.25,
        expected: ['12.50%', '32.80%', '9.40%', '60.00%', '1.25 倍'] },
      { kind: 'negative, zero and above-100 levels', netMargin: -2.5, grossMargin: 0, roe: -9.4, debtRatio: 120, currentRatio: 0,
        expected: ['-2.50%', '0.00%', '-9.40%', '120.00%', '0 倍'] },
    ])('shows $kind as financial ratios rather than signed growth rates', async ({ expected, ...values }) => {
      const item = financialBriefStock();
      Object.assign(item.fundamentals, values);
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('heading', { name: '收入与利润' });
      const panel = screen.getByRole('region', { name: '财务与估值详情' });
      expect(Array.from(panel.querySelectorAll('.super-finance-metrics'), group => group.querySelectorAll('.super-metric').length)).toEqual([2, 1, 5, 4]);
      expect(within(panel).getByText('利润表与现金流量表期间不同，不能直接用这两个金额比较现金回收情况。')).toBeVisible();
      for (const [index, label] of ['净利率', '毛利率', 'ROE', '资产负债率', '流动比率'].entries()) {
        const metric = screen.getByText(label, { exact: true }).closest('.super-metric')!;
        expect(metric.querySelector('strong')?.textContent).toBe(expected[index]);
        expect(metric.querySelector('.is-up, .is-down, .is-inflow, .is-outflow')).toBeNull();
      }
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it.each([
      { reason: 'equivalent date formats', incomePeriod: '20260331', cashflowPeriod: '2026-03-31', netProfit: 0 },
      { reason: 'unknown profit', incomePeriod: '20260331', cashflowPeriod: '2025-12-31', netProfit: null },
    ])('does not claim a cross-period amount comparison for $reason', async ({ incomePeriod, cashflowPeriod, netProfit }) => {
      const item = financialBriefStock();
      Object.assign(item.fundamentals, { incomePeriod, cashflowPeriod, netProfit });
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const panel = await screen.findByRole('region', { name: '财务与估值详情' });
      expect(panel.querySelector('.super-finance-period-note')).toBeNull();
      expect(within(panel).getByText('归母净利润').closest('.super-metric')?.querySelector('strong')?.textContent).toBe(netProfit === null ? '—' : '0 元');
      expect(within(panel).getByRole('region', { name: '收入与利润' })).toHaveTextContent('利润表期 2026-03-31');
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
    });

    it.each([{ repayment: 9_000_000, display: '900 万元' }, { repayment: 0, display: '0 元' }])(
      'shows a known financing repayment of $repayment even when buying is unknown', async ({ repayment, display }) => {
        const item = financialBriefStock();
        item.capital.margin = { rzche: repayment };
        item.capital.marginTradeDate = '20260909';
        respondWith(item);
        render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);
        const financing = within(await screen.findByRole('region', { name: '融资交易' }));
        expect(financing.getByText('融资偿还额').closest('.super-metric')?.querySelector('strong')?.textContent).toBe(display);
        for (const label of ['融资净买入', '融资余额', '融资买入额']) {
          expect(financing.getByText(label).closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^—$/);
        }
        expect(financing.getByText('融资日期 2026-09-09')).toBeVisible();
        expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
        expect(mockEvent).not.toHaveBeenCalled();
        expect(mockRefresh).not.toHaveBeenCalled();
        expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      },
    );

    function withFinancialSources(symbol = '603306.SH', name = '华懋科技', financialId = 1201, valuationId = 1202) {
      const item = financialBriefStock();
      return toCamelCase<SuperWatchlistStock>({
        ...item, symbol, name,
        fundamentals: { ...item.fundamentals, source_event_id: financialId },
        valuation: { ...item.valuation, source_event_id: valuationId },
      });
    }

    function sourceRecord(id: number, eventType: 'fundamental_snapshot' | 'market_snapshot', symbol = '603306.SH'): MonitorEvent {
      return {
        ...essay(id, `${symbol} ${eventType} 来源快照`),
        sourceKey: eventType === 'fundamental_snapshot' ? 'tushare.fundamental' : 'tushare.market',
        sourceName: eventType === 'fundamental_snapshot' ? 'Tushare 财务快照' : 'Tushare 日线估值快照',
        sourceType: 'tushare', externalId: `${symbol}:20260911`, eventType,
        symbols: [symbol], summary: '历史快照程序生成摘要，不作为核对字段的证据',
        metrics: eventType === 'fundamental_snapshot' ? {
          indicator: { endDate: '20260630', roe: 9.4, netprofitMargin: 12.5, currentRatio: 1.25 },
          income: { endDate: '20260331', revenue: 1_234_000_000, nIncomeAttrP: 120_000_000 },
          cashflow: { endDate: '20251231', nCashflowAct: 87_650_000 },
        } : {
          tradeDate: '20260911', close: 26, valuationTradeDate: '20260910',
          valuationPeTtm: 21.75, valuationPb: 2.3, valuationPsTtm: 3.5, valuationTotalMv: 123_400,
        },
      };
    }

    function withTableFinancialSources(symbol = '603306.SH', name = '华懋科技') {
      const item = withFinancialSources(symbol, name);
      item.fundamentals = toCamelCase<SuperWatchlistStock['fundamentals']>({
        ...item.fundamentals, source_event_id: 1299, source: '不可借用的通用来源',
        sources: {
          income: { event_id: 1401, source: '利润表独立来源', period: '20260331', announced_at: '20260430', report_type: '1', selection_status: 'selected' },
          cashflow: { event_id: 1402, source: '现金流量表独立来源', period: '20251231', announced_at: '20260328', report_type: '1', selection_status: 'selected' },
          indicator: { event_id: 1403, source: '财务指标独立来源', period: '20260630', announced_at: '20260828', report_type: null, selection_status: 'selected' },
        },
      });
      return item;
    }

    it('uses each table source and its own dates after snake-case conversion without fetching unopened records', async () => {
      const item = withTableFinancialSources();
      Object.assign(item.fundamentals, { incomePeriod: '20000331', cashflowPeriod: '20000331', indicatorPeriod: '20000331' });
      item.timeline = [sourceRecord(1299, 'fundamental_snapshot')];
      mockEvent.mockImplementation(async (id: number) => sourceRecord(id, 'fundamental_snapshot'));
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental&query=财报&page=2']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const panel = await screen.findByRole('region', { name: '财务与估值详情' });
      expect(mockEvent).not.toHaveBeenCalled();
      const location = screen.getByTestId('location').textContent;
      for (const [title, id, period, announcedAt, source, table] of [
        ['收入与利润', 1401, '2026-03-31', '2026-04-30', '利润表独立来源', '利润表'],
        ['经营现金流', 1402, '2025-12-31', '2026-03-28', '现金流量表独立来源', '现金流量表'],
        ['财务指标', 1403, '2026-06-30', '2026-08-28', '财务指标独立来源', '财务指标'],
      ] as const) {
        const block = within(panel).getByRole('region', { name: title });
        expect(block).toHaveTextContent(period);
        expect(block).toHaveTextContent(`公告 ${announcedAt}`);
        expect(block).toHaveTextContent(source);
        expect(block).not.toHaveTextContent(/2000-03-31|不可借用的通用来源/);
        const entry = within(block).getByRole('button', { name: `核对${title}来源` });
        expect(entry.textContent).toBe('核对来源');
        entry.focus();
        await act(async () => { fireEvent.click(entry); });
        const dialog = await screen.findByRole('dialog', { name: sourceRecord(id, 'fundamental_snapshot').title });
        expect(within(dialog).getByRole('heading', { name: `当前核对：${table}` })).toBeVisible();
        expect(dialog.querySelector('.source-record-fields')?.querySelector('h3')).toHaveTextContent(`当前核对：${table}`);
        expect(dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')?.open).toBe(false);
        expect(mockEvent).toHaveBeenLastCalledWith(id);
        fireEvent.click(within(dialog).getByRole('button', { name: '关闭抽屉' }));
        expect(entry).toHaveFocus();
      }
      expect(mockEvent.mock.calls).toEqual([[1401], [1402], [1403]]);
      expect(screen.getByTestId('location').textContent).toBe(location);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockEssayConsensus).not.toHaveBeenCalled();
    });

    it('does not borrow the shared id, source or dates for null and missing table entries', async () => {
      const item = withTableFinancialSources();
      item.fundamentals.sources!.income = { eventId: null, source: null, period: null, announcedAt: null, reportType: null, selectionStatus: 'selected' };
      delete item.fundamentals.sources!.cashflow;
      mockEvent.mockResolvedValue(sourceRecord(1403, 'fundamental_snapshot'));
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const panel = await screen.findByRole('region', { name: '财务与估值详情' });
      for (const title of ['收入与利润', '经营现金流']) {
        const block = within(panel).getByRole('region', { name: title });
        expect(block).toHaveTextContent('未标注');
        expect(block).not.toHaveTextContent(/2026-03-31|2025-12-31|不可借用的通用来源/);
        expect(within(block).queryByRole('button')).not.toBeInTheDocument();
      }
      expect(within(panel).getByText('营业收入').closest('.super-metric')).toHaveTextContent('12.34 亿元');
      expect(mockEvent).not.toHaveBeenCalled();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核对财务指标来源' })); });
      await screen.findByRole('dialog');
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1403);
    });

    it('keeps a missing requested income table explicit in the real reader instead of displaying the saved indicator as its source', async () => {
      const item = withTableFinancialSources();
      const record = sourceRecord(1401, 'fundamental_snapshot');
      delete record.metrics.income;
      mockEvent.mockResolvedValue(record);
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对收入与利润来源' });
      await act(async () => { fireEvent.click(entry); });
      const dialog = await screen.findByRole('dialog', { name: record.title });
      const current = within(dialog).getByRole('region', { name: '当前核对利润表' });
      expect(current).toHaveTextContent('此条记录未保存可展示的利润表字段，不使用其他表替代。');
      expect(current.querySelector('dl')).toBeNull();
      expect(current).not.toHaveTextContent(/20260630|9.4|12.5/);
      const other = dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')!;
      const savedIndicator = within(other).getByRole('heading', { name: '财务指标', hidden: true });
      expect(other).toContainElement(savedIndicator);
      expect(other.open).toBe(false);
      expect(savedIndicator).not.toBeVisible();
      fireEvent.click(within(other).getByText('其他已保存字段（2 组）'));
      expect(other.open).toBe(true);
      expect(savedIndicator).toBeVisible();
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1401);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it.each([
      { kind: 'cashflow' as const, title: '经营现金流', table: '现金流量表', id: 1402 },
      { kind: 'indicator' as const, title: '财务指标', table: '财务指标', id: 1403 },
    ])('opens the $kind conflict candidate in its own table context without loading other candidates', async ({ kind, title, table, id }) => {
      const item = withTableFinancialSources();
      Object.assign(item.fundamentals.sources![kind]!, { eventId: null, selectionStatus: 'conflict', candidateEventIds: [id, 1500] });
      const record = sourceRecord(id, 'fundamental_snapshot');
      mockEvent.mockResolvedValue(record);
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: `${title}：核对版本 1` });
      expect(entry.textContent).toBe('版本 1');
      await act(async () => { fireEvent.click(entry); });
      const dialog = await screen.findByRole('dialog', { name: record.title });
      expect(within(dialog).getByRole('heading', { name: `当前核对：${table}` })).toBeVisible();
      expect(dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')?.open).toBe(false);
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(id);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it.each([null, {}])('does not re-enable legacy source links when the new sources value is %s', async sources => {
      const item = withTableFinancialSources();
      item.fundamentals.sources = sources;
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('region', { name: '财务与估值详情' });
      expect(screen.queryByRole('button', { name: /核对(收入与利润|经营现金流|财务指标)来源/ })).not.toBeInTheDocument();
      expect(mockEvent).not.toHaveBeenCalled();
    });

    it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, '1401', true])(
      'does not use unsafe per-table source id %s or replace it with the shared source', async invalid => {
        const item = withTableFinancialSources();
        for (const source of Object.values(item.fundamentals.sources!)) Object.assign(source, { eventId: invalid });
        respondWith(item);
        render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
        await screen.findByRole('region', { name: '财务与估值详情' });
        expect(screen.queryByRole('button', { name: /核对(收入与利润|经营现金流|财务指标)来源/ })).not.toBeInTheDocument();
        expect(mockEvent).not.toHaveBeenCalled();
      },
    );

    it('marks conflicting tables as unknown and opens only their bounded safe candidate versions on demand', async () => {
      const item = withTableFinancialSources();
      for (const source of Object.values(item.fundamentals.sources!)) source.selectionStatus = 'conflict';
      Object.assign(item.fundamentals.sources!.income!, { candidateEventIds: [0, -1, 1.5, '1401', true, 1401, 1401, 1402, 1403, 1404, 1405, 1406], candidateCount: 6 });
      item.fundamentals.operatingCashflow = 0;
      mockEvent.mockImplementation(async (id: number) => sourceRecord(id, 'fundamental_snapshot'));
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const panel = await screen.findByRole('region', { name: '财务与估值详情' });
      for (const title of ['收入与利润', '经营现金流', '财务指标']) {
        const block = within(panel).getByRole('region', { name: title });
        expect(block).toHaveTextContent('同期间存在不同版本，待核对');
        for (const value of block.querySelectorAll('.super-metric > strong')) expect(value).toHaveTextContent(/^—$/);
        expect(within(block).queryByRole('button', { name: `核对${title}来源` })).not.toBeInTheDocument();
      }
      expect(panel.querySelector('.super-finance-period-note')).toBeNull();
      const versions = within(panel).getByRole('group', { name: '收入与利润来源版本' });
      expect(within(versions).getAllByRole('button')).toHaveLength(5);
      expect(panel).toHaveTextContent('共 6 个版本，展示 5 个');
      expect(mockEvent).not.toHaveBeenCalled();
      for (const [version, id] of [[1, 1401], [5, 1405]]) {
        await act(async () => { fireEvent.click(within(versions).getByRole('button', { name: `收入与利润：核对版本 ${version}` })); });
        const dialog = await screen.findByRole('dialog', { name: sourceRecord(id, 'fundamental_snapshot').title });
        expect(within(dialog).getByRole('heading', { name: '当前核对：利润表' })).toBeVisible();
        expect(dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')?.open).toBe(false);
        fireEvent.click(within(dialog).getByRole('button', { name: '关闭抽屉' }));
      }
      expect(mockEvent.mock.calls).toEqual([[1401], [1405]]);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockEssayConsensus).not.toHaveBeenCalled();
    });

    it('keeps conflicting amounts and growth unknown in overview without loading full data', async () => {
      const item = withTableFinancialSources();
      for (const source of Object.values(item.fundamentals.sources!)) source.selectionStatus = 'conflict';
      respondWith(item);
      render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
      const brief = await screen.findByRole('region', { name: '经营与估值摘要' });
      for (const label of ['营业收入', '归母净利润', '经营现金流净额']) {
        const card = within(brief).getByRole('region', { name: label });
        expect(card.querySelector('strong')).toHaveTextContent(/^—$/);
        expect(card).toHaveTextContent('同期间存在不同版本，待核对');
      }
      for (const label of ['营业收入', '归母净利润']) {
        const growth = within(brief).getByRole('region', { name: label }).querySelector('.super-business-growth')!;
        expect(growth.querySelector('b')).toHaveTextContent(/^—$/);
        expect(growth).toHaveTextContent('指标期 2026-06-30 · 不同版本待核对');
      }
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'overview');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it.each([
      { kind: 'income' as const, title: '收入与利润', id: 1401 },
      { kind: 'cashflow' as const, title: '经营现金流', id: 1402 },
      { kind: 'indicator' as const, title: '财务指标', id: 1403 },
    ])('closes the $kind reader when its table source changes while the legacy id is unchanged', async ({ kind, title, id }) => {
      const initial = withTableFinancialSources();
      const updated = withTableFinancialSources();
      updated.fundamentals.sources![kind]!.eventId = 1501;
      const oldRead = deferred<MonitorEvent>();
      mockEvent.mockReturnValueOnce(oldRead.promise).mockResolvedValue(sourceRecord(1501, 'fundamental_snapshot'));
      respondWith(initial);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: `核对${title}来源` });
      await act(async () => { fireEvent.click(entry); });
      await screen.findByRole('dialog');
      respondWith(updated);
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      await act(async () => { oldRead.resolve(sourceRecord(id, 'fundamental_snapshot')); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: `核对${title}来源` })); });
      await screen.findByRole('dialog', { name: sourceRecord(1501, 'fundamental_snapshot').title });
      expect(mockEvent.mock.calls).toEqual([[id], [1501]]);
      expect(mockLoad).toHaveBeenCalledTimes(2);
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('closes a loaded conflict reader when the candidate ids change without selecting a new event', async () => {
      const initial = withTableFinancialSources();
      Object.assign(initial.fundamentals.sources!.income!, { eventId: null, selectionStatus: 'conflict', candidateEventIds: [1401, 1402] });
      const updated = withTableFinancialSources();
      Object.assign(updated.fundamentals.sources!.income!, { eventId: null, selectionStatus: 'conflict', candidateEventIds: [1501, 1502] });
      mockEvent.mockResolvedValue(sourceRecord(1401, 'fundamental_snapshot'));
      respondWith(initial);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '收入与利润：核对版本 1' });
      await act(async () => { fireEvent.click(entry); });
      await screen.findByRole('dialog', { name: sourceRecord(1401, 'fundamental_snapshot').title });
      respondWith(updated);
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1401);
      expect(mockLoad).toHaveBeenCalledTimes(2);
    });

    it('closes a legacy reader when sources becomes present but null without changing the shared id', async () => {
      const initial = withFinancialSources();
      const updated = withFinancialSources();
      updated.fundamentals.sources = null;
      mockEvent.mockResolvedValue(sourceRecord(1201, 'fundamental_snapshot'));
      respondWith(initial);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对收入与利润来源' });
      await act(async () => { fireEvent.click(entry); });
      await screen.findByRole('dialog', { name: sourceRecord(1201, 'fundamental_snapshot').title });
      respondWith(updated);
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /核对(收入与利润|经营现金流|财务指标)来源/ })).not.toBeInTheDocument();
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1201);
    });

    it('keeps the table reader for unchanged provenance even when source object keys are reordered', async () => {
      const initial = withTableFinancialSources();
      const updated = withTableFinancialSources();
      const income = updated.fundamentals.sources!.income!;
      updated.fundamentals.sources = {
        indicator: updated.fundamentals.sources!.indicator,
        cashflow: updated.fundamentals.sources!.cashflow,
        income: Object.fromEntries(Object.entries(income).reverse()) as typeof income,
      };
      mockEvent.mockResolvedValue(sourceRecord(1401, 'fundamental_snapshot'));
      respondWith(initial);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对收入与利润来源' });
      await act(async () => { fireEvent.click(entry); });
      const dialog = await screen.findByRole('dialog', { name: sourceRecord(1401, 'fundamental_snapshot').title });
      respondWith(updated);
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(screen.getByRole('dialog', { name: sourceRecord(1401, 'fundamental_snapshot').title })).toBe(dialog);
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1401);
    });

    it.each([
      { button: '核对收入与利润来源', eventId: 1201, eventType: 'fundamental_snapshot' as const, expectedValue: '1234000000', expectedDate: '20260331' },
      { button: '核对经营现金流来源', eventId: 1201, eventType: 'fundamental_snapshot' as const, expectedValue: '87650000', expectedDate: '20251231' },
      { button: '核对财务指标来源', eventId: 1201, eventType: 'fundamental_snapshot' as const, expectedValue: '12.5', expectedDate: '20260630' },
      { button: '核对估值来源', eventId: 1202, eventType: 'market_snapshot' as const, expectedValue: '21.75', expectedDate: '20260910' },
    ])('reads the selected snapshot only when $button is opened and renders its saved fields', async ({ button, eventId, eventType, expectedValue, expectedDate }) => {
      const item = withFinancialSources();
      // An unrelated timeline entry must not be substituted for the source id.
      item.timeline = [sourceRecord(1299, eventType)];
      const record = sourceRecord(eventId, eventType);
      mockEvent.mockResolvedValue(record);
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: button });
      expect(entry.textContent).toBe('核对来源');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      entry.focus();
      await act(async () => { fireEvent.click(entry); });
      const dialog = await screen.findByRole('dialog', { name: record.title });
      expect(dialog.querySelector('.source-record-fields')).not.toBeNull();
      expect(dialog).toHaveTextContent(expectedValue);
      expect(dialog).toHaveTextContent(expectedDate);
      expect(dialog).toHaveTextContent('来源字段记录');
      expect(dialog).not.toHaveTextContent(record.summary!);
      if (eventType === 'fundamental_snapshot') {
        const table = button === '核对收入与利润来源' ? '利润表' : button === '核对经营现金流来源' ? '现金流量表' : '财务指标';
        expect(within(dialog).getByRole('heading', { name: `当前核对：${table}` })).toBeVisible();
        const other = dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')!;
        expect(other.open).toBe(false);
        fireEvent.click(within(other).getByText('其他已保存字段（2 组）'));
        expect(other.open).toBe(true);
        expect(within(dialog).getByText('销售净利率（%）').closest('div')).toHaveTextContent('12.5');
        expect(within(dialog).getByText('流动比率（倍）').closest('div')).toHaveTextContent('1.25');
      } else {
        expect(dialog.querySelector('.source-record-financial-context')).toBeNull();
        expect(within(dialog).getByRole('heading', { name: '估值记录' }).closest('section')).toHaveTextContent('21.75 倍');
      }
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(eventId);
      expect(screen.getByTestId('location')).toHaveTextContent('section=fundamental');
      fireEvent.click(within(dialog).getByRole('button', { name: '关闭抽屉' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(entry).toHaveFocus();
      expect(mockLoad).toHaveBeenCalledTimes(1);
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('keeps legacy financial data usable without inventing a source entry from its timeline', async () => {
      const item = financialBriefStock();
      item.timeline = [sourceRecord(1201, 'fundamental_snapshot'), sourceRecord(1202, 'market_snapshot')];
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('heading', { name: '收入与利润' });
      expect(screen.getByText('营业收入').closest('.super-metric')).toHaveTextContent('12.34 亿元');
      expect(screen.queryByRole('button', { name: /核对(收入与利润|经营现金流|财务指标)来源/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '核对估值来源' })).not.toBeInTheDocument();
      expect(mockEvent).not.toHaveBeenCalled();
    });

    it.each([null, 0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, '1201', true])(
      'does not turn invalid financial source id %s into a source request', async invalid => {
        const item = withFinancialSources();
        Object.assign(item.fundamentals, { sourceEventId: invalid });
        Object.assign(item.valuation, { sourceEventId: invalid });
        respondWith(item);
        render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
        await screen.findByRole('heading', { name: '收入与利润' });
        expect(screen.queryByRole('button', { name: /核对(收入与利润|经营现金流|财务指标)来源/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '核对估值来源' })).not.toBeInTheDocument();
        expect(mockEvent).not.toHaveBeenCalled();
      },
    );

    it.each(['pending', 'loaded'])('closes a %s financial source drawer on history stock switching without reviving the old response', async state => {
      const first = withTableFinancialSources();
      const next = withTableFinancialSources('300476.SZ', '胜宏科技');
      for (const kind of ['income', 'cashflow', 'indicator'] as FundamentalSourceKind[]) next.fundamentals.sources![kind]!.eventId = 1501;
      mockGetWatchlist.mockResolvedValue([first.symbol, next.symbol]);
      respondWith(first, next);
      const oldRecord = sourceRecord(1401, 'fundamental_snapshot');
      const nextRecord = sourceRecord(1501, 'fundamental_snapshot', next.symbol);
      const oldRead = deferred<MonitorEvent>();
      mockEvent.mockReturnValueOnce(oldRead.promise).mockResolvedValue(nextRecord);
      let previousStock!: () => void;
      function HistoryProbe() {
        const navigate = useNavigate();
        useEffect(() => { previousStock = () => { void navigate(-1); }; }, [navigate]);
        return null;
      }
      render(<MemoryRouter initialEntries={[
        `/super-watchlist?section=fundamental&symbol=${next.symbol}`,
        `/super-watchlist?section=fundamental&symbol=${first.symbol}`,
      ]} initialIndex={1}><SuperWatchlistPage /><HistoryProbe /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对收入与利润来源' });
      await act(async () => { fireEvent.click(entry); });
      expect(await screen.findByRole('dialog')).toBeVisible();
      if (state === 'loaded') await act(async () => { oldRead.resolve(oldRecord); });
      await act(async () => { previousStock(); });
      expect(await screen.findByRole('heading', { name: next.name, level: 2 })).toBeVisible();
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      if (state === 'pending') await act(async () => { oldRead.resolve(oldRecord); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.queryByText(oldRecord.title)).not.toBeInTheDocument();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核对收入与利润来源' })); });
      expect(await screen.findByRole('dialog', { name: nextRecord.title })).toBeVisible();
      expect(mockEvent.mock.calls).toEqual([[1401], [1501]]);
      expect(mockLoad.mock.calls.map(call => [call[0], call[3]])).toEqual([[first.symbol, 'full'], [next.symbol, 'full']]);
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('closes the source drawer when the same stock workspace selects a new source id and ignores the late old read', async () => {
      const initial = withFinancialSources();
      const updated = withFinancialSources(initial.symbol, initial.name, 1301, 1202);
      updated.fundamentals.netProfit = 200_000_000;
      const oldRecord = sourceRecord(1201, 'fundamental_snapshot');
      const newRecord = { ...sourceRecord(1301, 'fundamental_snapshot'), title: '更新后的财务来源快照' };
      newRecord.metrics.income = { endDate: '20260331', revenue: 1_234_000_000, nIncomeAttrP: 200_000_000 };
      const oldRead = deferred<MonitorEvent>();
      mockEvent.mockReturnValueOnce(oldRead.promise).mockResolvedValue(newRecord);
      respondWith(initial);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对收入与利润来源' });
      await act(async () => { fireEvent.click(entry); });
      expect(await screen.findByRole('dialog')).toBeVisible();
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1201);

      respondWith(updated);
      // Returning to a visible browser tab refreshes the current workspace;
      // it does not click a background control hidden by the modal.
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(mockLoad.mock.calls.map(call => [call[0], call[3]])).toEqual([[initial.symbol, 'full'], [initial.symbol, 'full']]);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.getByText('归母净利润').closest('.super-metric')).toHaveTextContent('2 亿元');
      await act(async () => { oldRead.resolve(oldRecord); });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.queryByText(oldRecord.title)).not.toBeInTheDocument();

      await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核对收入与利润来源' })); });
      expect(await screen.findByRole('dialog', { name: newRecord.title })).toHaveTextContent('200000000');
      expect(mockEvent.mock.calls).toEqual([[1201], [1301]]);
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('keeps the source drawer and its single read when only the current quote updates', async () => {
      const item = withFinancialSources();
      const record = sourceRecord(1202, 'market_snapshot');
      mockEvent.mockResolvedValue(record);
      respondWith(item);
      const view = render(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      const entry = await screen.findByRole('button', { name: '核对估值来源' });
      const priceBand = screen.getByRole('heading', { name: item.name, level: 2 }).closest('section')!;
      await act(async () => { fireEvent.click(entry); });
      const dialog = await screen.findByRole('dialog', { name: record.title });
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1202);

      mockQuotes.set('603306', {
        stockCode: item.symbol, currentPrice: 31.72, changePercent: 2.1,
        updateTime: '2026-09-14T02:00:00Z', source: 'test.shared_quote',
      });
      view.rerender(<MemoryRouter initialEntries={['/super-watchlist?section=fundamental']}><SuperWatchlistPage /></MemoryRouter>);
      expect(within(priceBand).getByText('31.72')).toBeInTheDocument();
      expect(screen.getByRole('dialog', { name: record.title })).toBe(dialog);
      expect(dialog).toBeVisible();
      expect(mockEvent).toHaveBeenCalledExactlyOnceWith(1202);
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockRefresh).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });
  });

  it('replaces an exhausted first load with an inline retry instead of an endless skeleton', async () => {
    respondWith(stock('603306.SH', '华懋科技'));
    mockLoad.mockRejectedValueOnce(new Error('test upstream timeout'));
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    const retry = await screen.findByRole('button', { name: '重试当前股票' });
    expect(screen.queryByLabelText(/正在加载 .* 数据/)).not.toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByRole('heading', { name: '华懋科技' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试当前股票' })).not.toBeInTheDocument();
  });

  it('keeps a late failure scoped to its stock when the user switches away', async () => {
    mockGetWatchlist.mockResolvedValue(['603306.SH', '300476.SZ']);
    let rejectFirst!: (error: Error) => void;
    mockLoad.mockImplementation((symbol: string) => symbol === '603306.SH'
      ? new Promise((_resolve, reject) => { rejectFirst = reject; })
      : Promise.resolve(workspace(stock('300476.SZ', '胜宏科技'), 'overview')));
    render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    const nextStock = await screen.findByRole('button', { name: /^胜宏科技.*300476.SZ/ });
    // MemoryRouter commits navigation in a React transition. Flush that user
    // interaction without settling the deliberately pending first request.
    await act(async () => { fireEvent.click(nextStock); });
    expect(await screen.findByRole('heading', { name: '胜宏科技' })).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('symbol=300476.SZ');
    await act(async () => rejectFirst(new Error('另一只股票超时')));
    expect(screen.queryByText('另一只股票超时')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试当前股票' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^华懋科技.*603306.SH/ }));
    // Switching back retries that stock rather than leaving the other stock's UI in error.
    await waitFor(() => expect(mockLoad.mock.calls.filter(call => call[0] === '603306.SH')).toHaveLength(2));
  });

  it('restores the requested section and makes its evidence visible before an optional chart', async () => {
    respondWith(stock('603306.SH', '华懋科技'));
    render(<MemoryRouter initialEntries={['/super-watchlist?section=essay']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: '小作文', pressed: true })).toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledWith('603306.SH', 183, false, 'full');
    expect(mockLoad.mock.calls.some(call => call[3] === 'overview')).toBe(false);
    expect(screen.getByRole('button', { name: '资料库', pressed: true })).toBeInTheDocument();
    expect(screen.queryByTestId('market-chart')).not.toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('section=essay');
    fireEvent.click(screen.getByRole('button', { name: '展开行情图' }));
    expect(screen.getByTestId('market-chart')).toHaveTextContent('603306.SH');
    fireEvent.click(screen.getByRole('button', { name: '财务与资金' }));
    fireEvent.click(screen.getByRole('button', { name: '资金筹码' }));
    expect(screen.getByTestId('location')).toHaveTextContent('section=capital');
    expect(screen.queryByTestId('market-chart')).not.toBeInTheDocument();
  });

  it('offers list retry rather than claiming a failed list is an empty watchlist', async () => {
    mockGetWatchlist.mockRejectedValueOnce(new Error('list timeout')).mockResolvedValue(['603306.SH']);
    respondWith(stock('603306.SH', '华懋科技'));
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '重试列表' }));
    expect(await screen.findByRole('heading', { name: '华懋科技' })).toBeInTheDocument();
  });

  it('adds a watchlist stock by selecting its Chinese name', async () => {
    respondWith(stock('603306.SH', '华懋科技'), stock('603306', '华懋科技'), stock('300476', '胜宏科技'));

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    const input = await screen.findByPlaceholderText('输入股票名称或代码');
    fireEvent.change(input, { target: { value: '胜宏科技' } });
    const suggestion = await screen.findByRole('option', { name: /胜宏科技.*300476/ });
    fireEvent.click(suggestion);

    await waitFor(() => expect(mockAdd).toHaveBeenCalledWith('300476.SZ'));
    await waitFor(() => expect(input).toHaveValue(''));
  });

  it('uses the realtime quote for the large price instead of the historical dashboard snapshot', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price: 70.11, changePct: -9.18, updatedAt: '2026-08-19T00:00:00' };
    mockQuotes.set('603306', {
      stockCode: '603306', currentPrice: 74.28, changePercent: 5.95,
      open: 71.98, high: 75.16, low: 70.66, amount: 1_153_199_161,
      updateTime: '2026-08-20T15:55:35',
    });
    respondWith(item);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    expect((await screen.findAllByText('74.28')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('70.11')).not.toBeInTheDocument();
    expect(await screen.findByText('行情快照 · 2026-08-20 15:55')).toBeInTheDocument();
  });

  it.each([null, 0, -10, NaN, 10])('does not claim a latest quote or show a percentage for unavailable price data: %s', async (price) => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price, changePct: 1, updatedAt: '2026-09-11T09:50:00', quoteKind: 'unavailable' };
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    const heading = await screen.findByRole('heading', { name: '华懋科技' });
    const band = heading.closest('section')!;
    expect(within(band).getByText('行情尚未取得')).toBeInTheDocument();
    expect(band.querySelector('.super-stock-price')).not.toHaveClass('is-up');
    expect(band.querySelector('.super-stock-price')).toHaveTextContent('——');
    expect(screen.queryByText('+1.00%')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '总览', pressed: true })).toBeEnabled();
  });

  it('keeps an undated price neutral and distinguishes stale snapshots from verified closing records', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price: 76.02, changePct: -0.14, updatedAt: 'bad timestamp' };
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    expect(await screen.findByText('报价时间未标注')).toBeInTheDocument();
    expect(screen.getAllByText('76.02')).toHaveLength(2);
    expect(screen.queryByText('-0.14%')).not.toBeInTheDocument();
    item.market = { ...item.market, updatedAt: '2026-09-11T01:50:35Z', isStale: true };
    fireEvent.click(screen.getByRole('button', { name: '刷新共享行情并唤醒到期数据源' }));
    expect(await screen.findByText('历史行情快照 · 2026-09-11 09:50')).toBeInTheDocument();
    item.market = { ...item.market, updatedAt: '20260911', quoteKind: 'daily_close' };
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新共享行情并唤醒到期数据源' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '刷新共享行情并唤醒到期数据源' }));
    expect(await screen.findByText('收盘行情 · 2026-09-11')).toBeInTheDocument();
  });

  it('does not let a stale realtime row overwrite a newer dashboard fact', async () => {
    mockGetWatchlist.mockResolvedValue(['300308.SZ']);
    const item = stock('300308.SZ', '中际旭创');
    item.market = { price: 851.31, changePct: -9.72, updatedAt: '2026-08-24T13:51:33' };
    mockQuotes.set('300308', {
      stockCode: '300308', currentPrice: 943, changePercent: 4.29,
      updateTime: '2026-08-21T15:00:00', isStale: true,
    });
    respondWith(item);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    expect((await screen.findAllByText('851.31')).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('943')).not.toBeInTheDocument();
    expect(screen.getAllByText('-9.72%').length).toBeGreaterThanOrEqual(1);
    expect(mockLoad).toHaveBeenCalledWith('300308.SZ', 183, false, 'overview');
    expect(screen.getByRole('button', { name: /^中际旭创.*300308.SZ/, pressed: true })).toHaveTextContent('851.31');
  });

  it('rejects a different stock response rather than attaching it to the requested slot', async () => {
    mockLoad.mockResolvedValue(workspace(stock('300308.SZ', '中际旭创'), 'overview'));
    render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    expect(await screen.findByText('返回的股票与当前选择不一致，请重试。')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '中际旭创' })).not.toBeInTheDocument();
    // Workspace failure and URL normalization are independent updates; the
    // failure text does not mean the router transition has committed yet.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('symbol=603306.SH'));
    expect(screen.getByRole('button', { name: '重试当前股票' })).toBeInTheDocument();
  });

  it('does not use a wrongly keyed quote belonging to a different company', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price: 76.02, changePct: -0.14, updatedAt: '2026-09-11T09:50:00' };
    mockQuotes.set('603306', {
      stockCode: '300308', stockName: '中际旭创', currentPrice: 851.31, changePercent: -9.72,
      updateTime: '2026-09-11T10:00:00',
    });
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    const heading = await screen.findByRole('heading', { name: '华懋科技' });
    expect(within(heading.closest('section')!).getByText('76.02')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^华懋科技.*603306.SH/, pressed: true })).toHaveTextContent('76.02');
    expect(screen.queryByText('851.31')).not.toBeInTheDocument();
  });

  it('keeps header and sidebar on the newest same-day quote when switching overview and full detail', async () => {
    const recent = stock('603306.SH', '华懋科技');
    recent.market = { price: 76.02, changePct: -0.14, updatedAt: '2026-09-11T10:30:00', source: 'tencent.snapshot' };
    const older = stock('603306.SH', '华懋科技');
    older.market = { price: 74.31, changePct: -2.39, updatedAt: '2026-09-11T09:45:00', source: 'tencent.snapshot' };
    older.fundamentals = { revenue: 3_000_000_000 };
    mockQuotes.set('603306', {
      stockCode: '603306', currentPrice: 75, changePercent: -1.48,
      updateTime: '2026-09-11T10:00:00', isStale: false,
    });
    mockLoad.mockImplementation(async (_symbol, _days, _force, view) => workspace(view === 'full' ? older : recent, view));
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '华懋科技' });
    fireEvent.click(screen.getByRole('button', { name: '财务与资金' }));
    expect(await screen.findByText('30 亿元')).toBeInTheDocument();
    const band = screen.getByRole('heading', { name: '华懋科技' }).closest('section')!;
    expect(within(band).getByText('76.02')).toBeInTheDocument();
    expect(within(band).getByText('行情快照 · 2026-09-11 10:30')).toHaveAttribute('title', '北京时间；数据源时间 2026-09-11T10:30:00');
    expect(screen.getByRole('button', { name: /^华懋科技.*603306.SH/, pressed: true })).toHaveTextContent('76.02');
    expect(screen.queryByText('74.31')).not.toBeInTheDocument();
    expect(screen.queryByText('75')).not.toBeInTheDocument();
  });

  it('starts with four primary sections and defers full research until the user asks for it', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.alternative = { essayCount: 1, essays: [essay(71, '只在完整资料加载后的详细原文')], catalysts: [], risks: [] };
    let finishFull!: (value: ReturnType<typeof workspace>) => void;
    mockLoad.mockImplementation((_symbol, _days, _force, view) => view === 'full'
      ? new Promise(resolve => { finishFull = resolve; })
      : Promise.resolve(workspace(item, 'overview')));
    render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    const nav = await screen.findByRole('navigation', { name: '个股信息栏目' });
    expect(within(nav).getAllByRole('button').map(button => button.textContent)).toEqual(['总览', '财务与资金', '一致预期', '资料库']);
    expect(mockLoad.mock.calls.every(call => call[3] === 'overview')).toBe(true);
    expect(screen.queryByRole('navigation', { name: '资料类型' })).not.toBeInTheDocument();
    fireEvent.click(within(nav).getByRole('button', { name: '资料库' }));
    expect(await screen.findByText('正在读取资料库，行情和其他栏目仍可使用。')).toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledWith('603306.SH', 183, false, 'full');
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    expect(screen.queryByText('0 条已载入资料')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '小作文' }));
    expect(mockLoad.mock.calls.filter(call => call[3] === 'full')).toHaveLength(1);
    await act(async () => finishFull(workspace(item, 'full')));
    expect(await screen.findByRole('button', { name: /只在完整资料加载后的详细原文.*查看原文/ })).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('section=essay');
  });

  it('replaces an afternoon overview with a newer completed close marked stale in the full view', async () => {
    const intraday = stock('603306.SH', '华懋科技');
    intraday.market = { price: 74.31, changePct: -2.39, updatedAt: '2026-09-11T14:30:00', source: 'tencent.snapshot' };
    const close = stock('603306.SH', '华懋科技');
    close.market = { price: 76.02, changePct: -0.14, updatedAt: '2026-09-11T15:00:00', source: 'tushare.daily', quoteKind: 'daily_close', isStale: true };
    close.fundamentals = { revenue: 3_000_000_000 };
    mockLoad.mockImplementation(async (_symbol, _days, _force, view) => workspace(view === 'full' ? close : intraday, view));
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '华懋科技' });
    expect(screen.getAllByText('74.31').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '财务与资金' }));
    await screen.findByText('30 亿元');
    const band = screen.getByRole('heading', { name: '华懋科技' }).closest('section')!;
    expect(within(band).getByText('76.02')).toBeInTheDocument();
    expect(within(band).getByText('收盘行情 · 2026-09-11 15:00')).toHaveAttribute('title', '北京时间；数据源时间 2026-09-11T15:00:00');
    expect(screen.getByRole('button', { name: /^华懋科技.*603306.SH/, pressed: true })).toHaveTextContent('76.02');
    expect(screen.queryByText('74.31')).not.toBeInTheDocument();
  });

  it('keeps quotes visible after a detail failure and retries without pretending the library is empty', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price: 76.02, changePct: -0.14, updatedAt: '2026-09-11T09:50:00' };
    item.alternative = { essayCount: 1, essays: [essay(72, '重试后可见的机构原文')], catalysts: [], risks: [] };
    mockLoad.mockImplementation((_symbol, _days, _force, view) => view === 'full'
      ? Promise.reject(new Error('detail timeout')) : Promise.resolve(workspace(item, 'overview')));
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
    await screen.findByRole('button', { name: '重试当前股票' });
    expect(screen.getByRole('heading', { name: '华懋科技' })).toBeInTheDocument();
    expect(screen.getAllByText('76.02')).toHaveLength(2);
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    expect(screen.queryByText('0 条已载入资料')).not.toBeInTheDocument();
    respondWith(item);
    fireEvent.click(screen.getByRole('button', { name: '重试当前股票' }));
    expect(await screen.findByRole('button', { name: /重试后可见的机构原文.*查看原文/ })).toBeInTheDocument();
    expect(mockLoad).toHaveBeenLastCalledWith('603306.SH', 183, true, 'full');
  });

  it('keeps an already loaded overview when a refresh request fails', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.market = { price: 76.02, changePct: -0.14, updatedAt: '2026-09-11T09:50:00' };
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '华懋科技' });
    mockLoad.mockRejectedValueOnce(new Error('refresh timeout'));
    fireEvent.click(screen.getByRole('button', { name: '刷新共享行情并唤醒到期数据源' }));
    expect(await screen.findByRole('button', { name: '重试当前股票' })).toBeInTheDocument();
    expect(screen.getAllByText('76.02')).toHaveLength(2);
    expect(screen.getByLabelText('当前股票研究摘要')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('paginates and filters loaded source material without launching another backend task', async () => {
    const item = stock('603306.SH', '华懋科技');
    const documents = Array.from({ length: 16 }, (_, index) => ({
      ...essay(100 + index, `专题资料${String(index + 1).padStart(2, '0')}`),
      eventAt: `2026-08-${String(index + 1).padStart(2, '0')}T08:00:00Z`,
    }));
    item.alternative = { essayCount: 16, essays: documents, catalysts: [], risks: [] };
    // The same source appears in multiple server sections. The library should
    // not turn the same evidence into extra records when it merges them.
    item.timeline = documents.slice(0, 2);
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
    expect(await screen.findByText('16 条已载入资料')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /专题资料.*查看原文/ })).toHaveLength(12);
    expect(screen.getByRole('navigation', { name: '资料分页' })).toHaveTextContent('第 1 / 2 页');
    const calls = mockLoad.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getAllByRole('button', { name: /专题资料.*查看原文/ })).toHaveLength(4);
    expect(screen.getByRole('button', { name: /专题资料01.*查看原文/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '筛选已载入资料' }), { target: { value: '专题资料16' } });
    expect(screen.getByText('1 条已载入资料')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /专题资料.*查看原文/ })).toHaveLength(1);
    expect(screen.queryByRole('navigation', { name: '资料分页' })).not.toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledTimes(calls);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('keeps public forum posts out of the default research library while preserving their own detail tab', async () => {
    const item = stock('603306.SH', '华懋科技');
    const research = essay(811, '公司订单与盈利研究');
    const comment: MonitorEvent = {
      ...essay(812, '股友讨论不应挤占研究资料'), eventType: 'stock_forum_post',
      sourceKey: 'eastmoney.guba_posts', sourceName: '东方财富股吧公开股评',
      externalId: 'guba:812', summary: '公开股评正文，仅为股友观点。',
      metrics: { author: '测试股友', replyCount: 3, _evidence: { evidenceLevel: 'unverified' } },
    };
    // Forum records arrive in shared timeline/message feeds as well as the
    // dedicated channel. Filtering only stockComments would not fix the leak.
    item.timeline = [comment, research];
    item.messages = { count: 1, items: [comment], channels: [] };
    item.alternative = { essayCount: 1, essays: [research], catalysts: [], risks: [] };
    item.stockComments = { count: 1, items: [comment], sourceNote: '真实公开帖子，未经核验。' };
    respondWith(item);
    mockEvent.mockResolvedValue(comment);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
    expect(await screen.findByRole('button', { name: '研究资料', pressed: true })).toBeInTheDocument();
    expect(await screen.findByText('1 条已载入资料')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /公司订单与盈利研究.*查看原文/ })).toBeInTheDocument();
    expect(screen.queryByText(comment.title)).not.toBeInTheDocument();
    expect(mockEvent).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '股评监控' }));
    const openComment = await screen.findByRole('button', { name: /股友讨论不应挤占研究资料.*3评 · 详情/ });
    expect(screen.queryByText(research.title)).not.toBeInTheDocument();
    fireEvent.click(openComment);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByText(comment.summary!)).toBeInTheDocument();
    expect(mockEvent).toHaveBeenCalledWith(comment.id);
  });

  describe('research library separates data snapshots', () => {
    it('keeps older research and meaningful events on the first page despite fourteen newer opening and closing snapshots', async () => {
      const item = stock('603306.SH', '华懋科技');
      const snapshots: MonitorEvent[] = Array.from({ length: 14 }, (_, index) => ({
        ...essay(4100 + index, `每日${index % 2 ? '收盘' : '开盘'}数据 ${index + 1}`),
        sourceKey: 'tushare.market', sourceName: '行情来源',
        externalId: `market:${index}`, eventType: index % 2 ? 'market_snapshot' : 'market_open_snapshot',
        eventAt: `2026-09-${String(1 + Math.floor(index / 2)).padStart(2, '0')}T${index % 2 ? '07' : '01'}:30:00Z`,
        metrics: {},
      }));
      const research = { ...essay(4120, '应先读的公司长期研究'), eventType: 'research_report_pdf', eventAt: '2026-08-20T08:00:00Z' };
      const announcement = { ...essay(4121, '公司正式披露事项'), eventType: 'company_announcement', eventAt: '2026-08-21T08:00:00Z' };
      const enterprise = { ...essay(4122, '企业新增经营提示'), eventType: 'enterprise_risk', eventAt: '2026-08-22T08:00:00Z' };
      const unknown = { ...essay(4123, '尚未分类的重要原始记录'), eventType: 'vendor_new_event', eventAt: '2026-08-23T08:00:00Z' };
      item.timeline = [...snapshots, research, announcement, enterprise, unknown];
      item.institution.latest = [research];
      item.company.announcements = [announcement];
      item.messages.items = [enterprise];
      respondWith(item);
      mockEvent.mockResolvedValue(snapshots[13]);
      render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);

      fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
      const filter = await screen.findByRole('combobox', { name: '资料筛选' });
      expect(filter).toHaveValue('all');
      expect(screen.getByText('4 条已载入资料')).toBeVisible();
      for (const event of [research, announcement, enterprise, unknown]) expect(screen.getByText(event.title)).toBeVisible();
      expect(screen.queryByText(/^每日(?:开盘|收盘)数据/)).not.toBeInTheDocument();
      expect(screen.queryByRole('navigation', { name: '资料分页' })).not.toBeInTheDocument();
      expect(screen.queryByRole('navigation', { name: '资料底部分页' })).not.toBeInTheDocument();
      expect(mockEvent).not.toHaveBeenCalled();
      const workspaceCalls = mockLoad.mock.calls.length;

      fireEvent.change(filter, { target: { value: 'snapshots' } });
      expect(screen.getByText('14 条已载入资料')).toBeVisible();
      expect(screen.getByTestId('location')).toHaveTextContent('material=snapshots');
      expect(screen.queryByText(research.title)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /每日收盘数据 14.*查看资料/ }));
      const dialog = await screen.findByRole('dialog', { name: snapshots[13].title });
      await waitFor(() => expect(mockEvent).toHaveBeenCalledExactlyOnceWith(snapshots[13].id));
      fireEvent.click(within(dialog).getByRole('button', { name: '关闭抽屉' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(filter).toHaveValue('snapshots');
      expect(mockLoad).toHaveBeenCalledTimes(workspaceCalls);
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
      expect(mockRefresh).not.toHaveBeenCalled();
    });

    it('recognizes source metadata and explicit legacy snapshot types without guessing unknown event types from their names', async () => {
      const item = stock('603306.SH', '华懋科技');
      const legacyTypes = ['realtime_quote', 'market_open_snapshot', 'market_snapshot', 'fundamental_snapshot', 'capital_chip_snapshot', 'ownership_snapshot', 'technical_factor', 'stock_comment_snapshot'];
      const legacy = legacyTypes.map((eventType, index) => ({ ...essay(4150 + index, `旧来源快照 ${eventType}`), eventType, metrics: {} }));
      const marked = { ...essay(4160, '来源明确标记的衍生数据'), eventType: 'vendor_calculated_state', metrics: { _evidence: { contentNature: 'derived_summary' } } };
      const unknown = { ...essay(4161, '新类型仍需由用户阅读'), eventType: 'vendor_unknown_snapshot', metrics: {} };
      item.timeline = [...legacy, marked, unknown];
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=evidence']}><SuperWatchlistPage /></MemoryRouter>);

      const filter = await screen.findByRole('combobox', { name: '资料筛选' });
      expect(screen.getByText('1 条已载入资料')).toBeVisible();
      expect(screen.getByText(unknown.title)).toBeVisible();
      expect(screen.queryByText(marked.title)).not.toBeInTheDocument();
      for (const event of legacy) expect(screen.queryByText(event.title)).not.toBeInTheDocument();
      fireEvent.change(filter, { target: { value: 'snapshots' } });
      expect(screen.getByText('9 条已载入资料')).toBeVisible();
      expect(screen.getByText(marked.title)).toBeVisible();
      for (const event of legacy) expect(screen.getByText(event.title)).toBeVisible();
      expect(screen.queryByText(unknown.title)).not.toBeInTheDocument();
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('does not offer an empty snapshot filter unless the URL explicitly selects it', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.alternative.essays = [essay(4170, '有原文但没有数据快照')];
      respondWith(item);
      const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=evidence']}><SuperWatchlistPage /></MemoryRouter>);
      await screen.findByRole('combobox', { name: '资料筛选' });
      expect(screen.queryByRole('option', { name: '数据快照' })).not.toBeInTheDocument();
      page.unmount();

      render(<MemoryRouter initialEntries={['/super-watchlist?section=evidence&material=snapshots']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
      expect(await screen.findByRole('combobox', { name: '资料筛选' })).toHaveValue('snapshots');
      expect(screen.getByRole('option', { name: '数据快照' })).toBeInTheDocument();
      expect(screen.getByText('0 条已载入资料')).toBeVisible();
      expect(screen.queryByText(item.alternative.essays[0].title)).not.toBeInTheDocument();
      expect(screen.getByTestId('location')).toHaveTextContent('material=snapshots');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });

    it('keeps snapshot-only data discoverable without presenting it as research', async () => {
      const item = stock('603306.SH', '华懋科技');
      item.timeline = [{ ...essay(4180, '只有这一份行情记录'), eventType: 'market_snapshot', metrics: {} }];
      respondWith(item);
      render(<MemoryRouter initialEntries={['/super-watchlist?section=evidence']}><SuperWatchlistPage /></MemoryRouter>);
      const filter = await screen.findByRole('combobox', { name: '资料筛选' });
      expect(screen.getByText('0 条已载入资料')).toBeVisible();
      expect(screen.queryByText(item.timeline[0].title)).not.toBeInTheDocument();
      expect(screen.getByRole('option', { name: '数据快照' })).toBeInTheDocument();
      fireEvent.change(filter, { target: { value: 'snapshots' } });
      expect(screen.getByText('1 条已载入资料')).toBeVisible();
      expect(screen.getByText(item.timeline[0].title)).toBeVisible();
      expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
      expect(mockEvent).not.toHaveBeenCalled();
      expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    });
  });

  it('continues from bottom pagination through source reading and back to page one without reloading the workspace', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.institution.latest = Array.from({ length: 13 }, (_, index) => ({
      ...essay(4200 + index, `连续阅读研报 ${index + 1}`), eventType: 'research_report_pdf',
      eventAt: `2026-09-${String(index + 1).padStart(2, '0')}T08:00:00Z`,
    }));
    respondWith(item);
    mockEvent.mockResolvedValue({ ...item.institution.latest[0], summary: '第2页来源的完整摘要' });
    render(<MemoryRouter initialEntries={['/super-watchlist?section=evidence']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    const results = await screen.findByLabelText('资料列表');
    const bottom = screen.getByRole('navigation', { name: '资料底部分页' });
    const top = screen.getByRole('navigation', { name: '资料分页' });
    expect(results.compareDocumentPosition(bottom) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(bottom).getByRole('button', { name: '底部上一页' })).toBeDisabled();
    fireEvent.click(within(bottom).getByRole('button', { name: '底部下一页' }));
    expect(results).toHaveFocus();
    expect(top).toHaveTextContent('第 2 / 2 页');
    expect(bottom).toHaveTextContent('第 2 / 2 页');
    expect(screen.getByTestId('location')).toHaveTextContent('page=2');
    expect(within(bottom).getByRole('button', { name: '底部下一页' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /连续阅读研报 1.*查看资料/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('第2页来源的完整摘要')).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭抽屉' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(bottom).toHaveTextContent('第 2 / 2 页');
    fireEvent.click(within(bottom).getByRole('button', { name: '底部上一页' }));
    expect(results).toHaveFocus();
    expect(top).toHaveTextContent('第 1 / 2 页');
    expect(bottom).toHaveTextContent('第 1 / 2 页');
    expect(screen.getByTestId('location')).not.toHaveTextContent('page=');
    expect(screen.getByText('连续阅读研报 13')).toBeVisible();
    expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'full');
    expect(mockEvent).toHaveBeenCalledExactlyOnceWith(item.institution.latest[0].id);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    expect(mockRefresh).not.toHaveBeenCalled();
  });

  it('names unloaded watchlist stocks from the exact index without fetching their workspaces or treating code-only quote names as names', async () => {
    const item = stock('603306.SH', '603306');
    const pending = deferred<ReturnType<typeof workspace>>();
    mockGetWatchlist.mockResolvedValue([item.symbol, '300476.SZ']);
    mockLoad.mockReturnValue(pending.promise);
    mockQuotes.set('300476', { stockCode: '300476.SZ', stockName: '300476', currentPrice: 0 });
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: /^胜宏科技.*300476.SZ/, pressed: false })).toBeVisible();
    expect(screen.getByRole('button', { name: /^华懋科技.*603306.SH/, pressed: true })).toBeVisible();
    expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'overview');
    await act(async () => { pending.resolve(workspace(item, 'overview')); });
    expect(screen.getByRole('button', { name: /^华懋科技.*603306.SH/, pressed: true })).toBeVisible();
    expect(mockLoad.mock.calls.some(call => call[0] === '300476.SZ')).toBe(false);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('prefers a real cached company name over quote and index names without matching another exchange by bare code', async () => {
    const item = stock('603306.SH', '公司已更新名称');
    mockGetWatchlist.mockResolvedValue([item.symbol, '300476.SH']);
    mockQuotes.set('603306', { stockCode: item.symbol, stockName: '行情中的旧名称', currentPrice: 0 });
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: /^公司已更新名称.*603306.SH/, pressed: true })).toBeVisible();
    expect(screen.getByRole('button', { name: /^300476.SH/, pressed: false })).toBeVisible();
    expect(screen.queryByRole('button', { name: /^胜宏科技/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^行情中的旧名称/ })).not.toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledExactlyOnceWith(item.symbol, 183, false, 'overview');
  });

  it('uses the current company name as the supported full-library query, including after stock switching', async () => {
    const huamao = stock('603306.SH', '华懋科技');
    const shenghong = stock('300476.SZ', '胜宏科技');
    huamao.alternative.essayCount = 321;
    huamao.alternative.essays = [essay(821, '华懋近期研究原文')];
    mockGetWatchlist.mockResolvedValue([huamao.symbol, shenghong.symbol]);
    respondWith(huamao, shenghong);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=essay']}><Routes>
      <Route path="/super-watchlist" element={<SuperWatchlistPage />} />
      <Route path="/essay-radar/feed" element={<h1>全库检索目的地</h1>} />
    </Routes><LocationProbe /></MemoryRouter>);

    const link = await screen.findByRole('link', { name: '用“华懋科技”检索全库' });
    const destination = new URL(link.getAttribute('href')!, 'http://localhost');
    expect(destination.pathname).toBe('/essay-radar/feed');
    expect(destination.searchParams.get('query')).toBe('华懋科技');
    expect(destination.searchParams.has('stock')).toBe(false);
    expect(screen.getByText(/共匹配 321 篇，当前列表为近期已载入记录/)).toBeInTheDocument();
    expect(screen.getByText('1 条已载入资料')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '检索华懋科技的机构段子与录音' })).toHaveAttribute('href', `/essay-radar/feed?query=${encodeURIComponent(huamao.name)}`);

    fireEvent.click(screen.getByRole('button', { name: /^胜宏科技.*300476.SZ/ }));
    const switchedLink = await screen.findByRole('link', { name: '用“胜宏科技”检索全库' });
    expect(switchedLink).toHaveAttribute('href', `/essay-radar/feed?query=${encodeURIComponent(shenghong.name)}`);
    expect(screen.queryByRole('link', { name: '用“华懋科技”检索全库' })).not.toBeInTheDocument();
    fireEvent.click(switchedLink);
    expect(await screen.findByRole('heading', { name: '全库检索目的地' })).toBeVisible();
    expect(screen.getByTestId('location')).toHaveTextContent(`query=${encodeURIComponent(shenghong.name)}`);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('requires confirmation, removes one stock, and keeps the remaining stock visible', async () => {
    const initial = [stock('603306', '华懋科技'), stock('300476', '胜宏科技')];
    mockGetWatchlist.mockResolvedValue(['603306', '300476']);
    respondWith(...initial);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: '删除自选股 华懋科技' }));
    expect(screen.getByText(/已入库的历史行情、公告和研究资料会保留/)).toBeInTheDocument();
    expect(mockRemove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith('603306'));
    await waitFor(() => expect(screen.queryByText('华懋科技')).not.toBeInTheDocument());
    expect((await screen.findAllByText('胜宏科技')).length).toBeGreaterThan(0);
  });

  it('opens a keyword-matched essay inside the current page', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.alternative = {
      essayCount: 1, catalysts: [], risks: [], essays: [{
        id: 88, sourceKey: 'zsxq.essays', sourceName: '知识星球小作文（待核验）',
        sourceType: 'mcp', externalId: 'topic-88', eventType: 'essay', perspective: 'investor',
        title: '华懋下半年订单跟踪', summary: '华懋订单可能改善。',
        url: 'https://wx.zsxq.com/group/1/topic/88', symbols: ['603306.SH'], sentiment: 'bullish',
        importanceScore: 70, confidenceScore: 0.6, tags: [], actors: [], metrics: {},
        eventAt: '2026-08-20T08:00:00Z',
      }],
    };
    respondWith(item);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
    fireEvent.click(screen.getByRole('button', { name: '小作文' }));

    const original = await screen.findByRole('button', { name: /华懋下半年订单跟踪.*查看原文/ });
    expect(screen.queryByRole('link', { name: /华懋下半年订单跟踪.*查看原文/ })).not.toBeInTheDocument();
    fireEvent.click(original);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByText('华懋订单可能改善。')).toBeInTheDocument();
    expect(mockNote).toHaveBeenCalledWith('topic-88');
    expect(screen.getByRole('img', { name: '小作文图片 1' })).toHaveAttribute('src', 'https://example.test/image.jpg');
    expect(screen.getByRole('link', { name: /调研附件.pdf/ })).toHaveAttribute('href', 'https://example.test/file.pdf');
    expect(screen.getByRole('link', { name: /在知识星球打开/ })).toHaveAttribute(
      'href',
      'https://wx.zsxq.com/group/1/topic/88',
    );
  });

  it('reads a report in place and exposes the original PDF without leaving the stock or relabelling a summary as full text', async () => {
    const item = stock('603306.SH', '华懋科技');
    const report: MonitorEvent = {
      ...essay(890, '华懋科技半年报点评'), eventType: 'research_report_pdf',
      sourceName: '券商研究来源', sourceKey: 'tushare.research_pdf', summary: '列表里较短的摘录',
      url: 'https://example.test/original.pdf',
    };
    item.institution.latest = [report];
    respondWith(item);
    mockEvent.mockResolvedValue({ ...report, summary: '读取详情后保留的研报摘要', metrics: { instCsname: '测试券商', name: '华懋科技', tsCode: '603306.SH' } });
    render(<MemoryRouter initialEntries={['/super-watchlist?section=institution&symbol=603306.SH']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /华懋科技半年报点评.*查看资料/ }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText('读取详情后保留的研报摘要')).toBeVisible();
    expect(dialog.getByText('研报摘要与记录')).toBeVisible();
    expect(dialog.getByText('这里展示已入库的摘要与资料字段，不是完整 PDF 正文。')).toBeVisible();
    expect(dialog.getByRole('link', { name: '打开原始 PDF' })).toHaveAttribute('href', report.url);
    expect(screen.getByTestId('location')).toHaveTextContent('section=institution&symbol=603306.SH');
    expect(mockEvent).toHaveBeenCalledExactlyOnceWith(890);
    fireEvent.click(dialog.getByRole('button', { name: '关闭抽屉' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /华懋科技半年报点评.*查看资料/ })).toBeVisible();
  });

  it('shows genuine public stock comments in an in-page detail drawer', async () => {
    const item = stock('603306.SH', '华懋科技');
    const comment = {
      id: 901, sourceKey: 'eastmoney.guba_posts', sourceName: '东方财富股吧公开股评',
      sourceType: 'html', externalId: 'guba:1762131880', eventType: 'stock_forum_post', perspective: 'investor' as const,
      title: '华懋订单讨论', summary: '公开帖子正文摘录：订单边际改善仍需核验。',
      url: 'https://mguba.eastmoney.com/mguba/article/0/1762131880', symbols: ['603306.SH'], sentiment: 'neutral' as const,
      importanceScore: 48, confidenceScore: 0.35, tags: ['东方财富股吧', '待核验'], actors: ['股友甲', '东方财富股吧'],
      metrics: { author: '股友甲', views: 12000, replyCount: 15, likeCount: 3, imageUrls: ['https://example.test/post.jpg'], _evidence: { evidenceLevel: 'unverified' } },
      eventAt: '2026-08-20T10:07:00Z',
    };
    item.stockComments = { count: 1, items: [comment], sourceNote: '只展示真实公开帖子。' };
    respondWith(item);
    mockEvent.mockResolvedValue(comment);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '资料库' }));
    fireEvent.click(screen.getByRole('button', { name: '股评监控' }));

    const detailButton = await screen.findByRole('button', { name: /华懋订单讨论.*15评 · 详情/ });
    expect(screen.queryByRole('link', { name: /华懋订单讨论/ })).not.toBeInTheDocument();
    fireEvent.click(detailButton);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByText('公开帖子正文摘录：订单边际改善仍需核验。')).toBeInTheDocument();
    expect(screen.getAllByText('股友甲').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('12,000')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '股评图片 1' })).toHaveAttribute('src', 'https://example.test/post.jpg');
    expect(screen.getByRole('link', { name: /查看来源原帖/ })).toHaveAttribute('href', comment.url);
    expect(mockEvent).toHaveBeenCalledWith(901);
  });

  it('does not invent zero replies for a missing public-comment count but preserves an actual zero', async () => {
    const item = stock('603306.SH', '华懋科技');
    const missing: MonitorEvent = {
      ...essay(902, '未提供数量的帖子'), eventType: 'stock_forum_post',
      sourceKey: 'eastmoney.guba_posts', sourceName: '公开股评来源', metrics: { author: '样例股友' },
    };
    const zero: MonitorEvent = {
      ...missing, id: 903, externalId: 'guba:903', title: '明确没有回复的帖子', metrics: { author: '样例股友', replyCount: 0 },
    };
    item.stockComments = { count: 2, items: [missing, zero], sourceNote: '按接口实际返回数量展示。' };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=comments&symbol=603306.SH']}><SuperWatchlistPage /></MemoryRouter>);

    const unknownCount = await screen.findByRole('button', { name: /未提供数量的帖子.*详情/ });
    expect(unknownCount).not.toHaveTextContent(/\d+评/);
    const zeroCount = screen.getByRole('button', { name: /明确没有回复的帖子.*0评 · 详情/ });
    expect(zeroCount).toHaveTextContent('0评 · 详情');
    expect(mockEvent).not.toHaveBeenCalled();
  });

  it('renders the time-aware 20-related plus 5-dedicated research workspace', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.alternative = {
      essayCount: 1, catalysts: [], risks: [], essays: [{
        id: 502, sourceKey: 'zsxq.essays', sourceName: '知识星球小作文（待核验）',
        sourceType: 'mcp', externalId: 'topic-price', eventType: 'essay', perspective: 'investor',
        title: '华懋目标价跟踪', summary: '目标价看到 100 元。', url: 'https://wx.zsxq.com/group/1/topic/topic-price',
        symbols: ['603306.SH'], sentiment: 'bullish', importanceScore: 70, confidenceScore: 0.6,
        tags: [], actors: [], metrics: {}, eventAt: '2026-08-20T08:00:00Z',
      }],
    };
    item.consensus = {
      ...item.consensus,
      brokerReportCount: 12,
      targetPrice: { sampleCount: 5, min: 88, median: 96, max: 108 },
      essayExpectationCount: 2,
      essayAnalysis: {
        status: 'completed', sourceCount: 25, relatedSourceCount: 20, dedicatedSourceCount: 5, analyzedCount: 25, pendingCount: 0,
        hasReport: true, reportSourceCount: 25, reportSourcesComplete: false,
        analysisCutoffAt: '2026-08-20T08:00:00Z',
        summary: '近期材料同时给出利润与估值推测。', hasExplicitExpectations: true,
        profitOutlook: '部分材料推测 2026 年净利润约 8 亿元。',
        valuationOutlook: '有材料给出 100 元目标价和 300 亿元目标市值。',
        estimates: [
          { topicId: 'topic-profit', proposedAt: '2026-08-18T08:00:00Z', sourceKind: 'related', subject: '华懋科技', subjectRelation: 'target_stock', metric: 'net_profit', period: '2026E', valueText: '净利润约 8 亿元', evidence: '预计全年净利润约 8 亿元', confidence: 0.91 },
          { eventId: 999, topicId: 'topic-price', title: '华懋目标价跟踪', proposedAt: '2026-08-20T08:00:00Z', sourceKind: 'dedicated', subject: '富创优越', subjectRelation: 'acquisition_target', metric: 'target_price', period: '未来12个月', valueText: '目标价 100 元', evidence: '目标价看到 100 元', confidence: 0.84 },
        ],
        metricCounts: { net_profit: 1, target_price: 1 },
        consensusPoints: ['盈利方向偏积极'], conflicts: [], timeObservations: ['8月18日利润预测早于8月20日目标价观点'],
        verificationConditions: [{ condition: '等待年度业绩披露', window: '2026年报', impact: '核验利润预测', expiryAt: '2027年4月' }],
        caveats: ['均为未经核验的小作文推测'],
        sourceNotes: [
          { topicId: 'topic-price', eventId: 999, title: '华懋目标价跟踪', eventAt: '2026-08-20T08:00:00Z', authorName: '研究员', sourceKind: 'dedicated', estimateCount: 1 },
          { topicId: 'topic-profit', title: '华懋利润跟踪', eventAt: '2026-08-18T08:00:00Z', authorName: '分析师', sourceKind: 'related', estimateCount: 1 },
        ],
      },
    };
    respondWith(item);
    mockNote.mockResolvedValue({
      topicId: 'topic-price', groupId: '1', groupName: '测试星球', title: '华懋目标价跟踪',
      content: '原文明确写到目标价看到 100 元。', authorName: '研究员', topicType: 'talk',
      digested: false, sticky: false, symbols: ['603306.SH'], files: [], images: [], counts: {},
      createdAt: '2026-08-20T08:00:00Z',
    });

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '一致预期' }));

    expect(await screen.findByRole('heading', { name: '一致预期', level: 3 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '检索原文' })).toHaveAttribute('href', `/essay-radar/feed?query=${encodeURIComponent(item.name)}`);
    expect(screen.getByRole('button', { name: '更新报告 · 25 篇' })).toBeInTheDocument();
    expect(screen.getByLabelText('研究样本范围')).toHaveTextContent('本次可分析 · 相关20');
    expect(screen.getByLabelText('研究样本范围')).toHaveTextContent('本次可分析 · 单股5');
    const timelineToggle = screen.getByText('按提出时间查看预期变化 · 2 条');
    const sourceToggle = screen.getByText('报告引用原文 · 2 篇');
    expect(timelineToggle.closest('details')).not.toHaveAttribute('open');
    expect(sourceToggle.closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('预测时间线')).not.toBeVisible();
    fireEvent.click(timelineToggle);
    fireEvent.click(sourceToggle);
    expect(timelineToggle.closest('details')).toHaveAttribute('open');
    expect(sourceToggle.closest('details')).toHaveAttribute('open');
    expect(screen.getByText('预测时间线')).toBeVisible();
    expect(screen.getByRole('button', { name: /单股专属.*华懋目标价跟踪/ })).toBeVisible();
    expect(screen.getAllByText('未来12个月').length).toBeGreaterThan(0);
    expect(screen.getAllByText('净利润约 8 亿元').length).toBeGreaterThan(0);
    expect(screen.getAllByText('目标价 100 元').length).toBeGreaterThan(0);
    expect(screen.getByText('富创优越').closest('td')).toHaveTextContent('收购标的');
    expect(screen.getByText('8月18日利润预测早于8月20日目标价观点')).toBeInTheDocument();
    expect(screen.getByText('等待年度业绩披露')).toBeInTheDocument();
    expect(screen.getByText('均为未经核验的小作文推测')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查看原文.*目标价 100 元/ }));
    expect(await screen.findByText('原文明确写到目标价看到 100 元。')).toBeInTheDocument();
    expect(mockNote).toHaveBeenCalledWith('topic-price');
  });

  it('paginates the actual expectation rows in groups of six and resets the page when filtering metrics', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.estimates = [2, 13, 1, 10, 6, 8, 3, 12, 5, 11, 4, 9, 7].map(id => expectation(id, id > 8
      ? { metric: 'target_price', valueText: `目标价 ${id} 元` }
      : {}));
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const matrix = within(await screen.findByRole('region', { name: '预期证据' }));
    const rowTitles = () => matrix.getAllByRole('button', { name: /^查看原文/ })
      .map(row => row.getAttribute('aria-label')?.match(/预期原文 (\d+) /)?.[1]);
    expect(rowTitles()).toEqual(['13', '12', '11', '10', '9', '8']);
    expect(matrix.getByText('13 条 · 每页 6 条')).toBeVisible();
    expect(matrix.getByRole('button', { name: '上一页' })).toBeDisabled();
    fireEvent.click(matrix.getByRole('button', { name: '下一页' }));
    expect(rowTitles()).toEqual(['7', '6', '5', '4', '3', '2']);
    expect(matrix.getByText('第 2 / 3 页')).toBeVisible();
    fireEvent.click(matrix.getByRole('button', { name: '下一页' }));
    expect(rowTitles()).toEqual(['1']);
    expect(matrix.getByRole('button', { name: '下一页' })).toBeDisabled();

    fireEvent.change(matrix.getByRole('combobox', { name: '筛选预期指标' }), { target: { value: 'net_profit' } });
    expect(rowTitles()).toEqual(['8', '7', '6', '5', '4', '3']);
    expect(matrix.getByText('8 条 · 每页 6 条')).toBeVisible();
    expect(matrix.getByText('第 1 / 2 页')).toBeVisible();
    expect(matrix.getByRole('button', { name: '上一页' })).toBeDisabled();
    expect(matrix.queryByRole('button', { name: /查看原文.*目标价/ })).not.toBeInTheDocument();
    fireEvent.click(matrix.getByRole('button', { name: '下一页' }));
    expect(rowTitles()).toEqual(['2', '1']);
    fireEvent.change(matrix.getByRole('combobox', { name: '筛选预期指标' }), { target: { value: 'all' } });
    expect(rowTitles()).toEqual(['13', '12', '11', '10', '9', '8']);
    expect(matrix.getByText('第 1 / 3 页')).toBeVisible();
    fireEvent.change(matrix.getByRole('combobox', { name: '筛选预期指标' }), { target: { value: 'target_price' } });
    expect(rowTitles()).toEqual(['13', '12', '11', '10', '9']);
    expect(matrix.queryByRole('navigation', { name: '预期证据分页' })).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('orders expectations by actual proposed instants across time zones and falls back to the original event time', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.estimates = [
      expectation(1, { proposedAt: '2026-09-11T10:00:00Z' }),
      expectation(4, { proposedAt: '2026-09-10T08:00:00Z', eventAt: '2026-09-12T08:00:00Z' }),
      expectation(3, { proposedAt: null, eventAt: '2026-09-11T08:30:00Z' }),
      expectation(2, { proposedAt: '2026-09-11T17:00:00+08:00' }),
    ];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const matrix = within(await screen.findByRole('region', { name: '预期证据' }));
    const rows = matrix.getAllByRole('button', { name: /^查看原文/ });
    expect(rows.map(row => row.getAttribute('aria-label'))).toEqual([
      '查看原文 预期原文 1 净利润 1 亿元',
      '查看原文 预期原文 2 净利润 2 亿元',
      '查看原文 预期原文 3 净利润 3 亿元',
      '查看原文 预期原文 4 净利润 4 亿元',
    ]);
    expect(within(rows[2]).getAllByRole('cell')[0]).toHaveTextContent(displayedTime('2026-09-11T08:30:00Z'));
    expect(within(rows[3]).getAllByRole('cell')[0]).toHaveTextContent(displayedTime('2026-09-10T08:00:00Z'));
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it.each([{ key: 'Enter', code: 'Enter' }, { key: ' ', code: 'Space' }])('opens an expectation original with the $code key without starting analysis', async ({ key, code }) => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.estimates = [expectation(1)];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const matrix = within(await screen.findByRole('region', { name: '预期证据' }));
    const row = matrix.getByRole('button', { name: '查看原文 预期原文 1 净利润 1 亿元' });
    expect(row).toHaveAttribute('tabindex', '0');
    row.focus();
    expect(row).toHaveFocus();
    fireEvent.keyDown(row, { key: 'ArrowDown', code: 'ArrowDown' });
    expect(mockNote).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(row, { key, code, cancelable: true })).toBe(false);
    expect(await screen.findByRole('dialog')).toBeVisible();
    expect(await screen.findByText('华懋订单可能改善。')).toBeVisible();
    expect(mockNote).toHaveBeenCalledExactlyOnceWith('expectation-1');
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('converts broker net profit from ten-thousand yuan to hundred-million yuan without treating missing values as zero', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.forecasts = [
      { period: '2026E', sampleCount: 3, epsMedian: 1.23, npMedian: 123456, peMedian: 20, roeMedian: 15 },
      { period: '2027E', sampleCount: 2, npMedian: null },
      { period: '2028E', sampleCount: 1 },
      { period: '2029E', sampleCount: 1, npMedian: 0 },
    ];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const heading = await screen.findByRole('heading', { name: '券商预测样本' });
    const broker = within(heading.closest('section')!);
    expect(broker.queryByText('原文目标价下限（元）')).not.toBeInTheDocument();
    expect(broker.queryByText('目标价端点中位（元）', { selector: 'p' })).not.toBeInTheDocument();
    expect(broker.queryByText('原文目标价上限（元）')).not.toBeInTheDocument();
    const table = within(broker.getByRole('table'));
    expect(table.getByRole('columnheader', { name: '净利润（亿元）' })).toBeVisible();
    const cellsFor = (period: string) => within(table.getByRole('cell', { name: period }).closest('tr')!)
      .getAllByRole('cell').map(cell => cell.textContent);
    expect(cellsFor('2026E')).toEqual(['2026E', '3', '1.23', '12.35', '20', '15']);
    expect(cellsFor('2027E')).toEqual(['2027E', '2', '—', '—', '—', '—']);
    expect(cellsFor('2028E')).toEqual(['2028E', '1', '—', '—', '—', '—']);
    expect(cellsFor('2029E')).toEqual(['2029E', '1', '—', '0', '—', '—']);
  });

  it('keeps a stale report and its sources distinct from the latest candidate sample and cutoff', async () => {
    const item = stock('603306.SH', '华懋科技');
    const analysisCutoffAt = '2026-09-01T08:00:00Z';
    const completedAt = '2026-09-02T10:00:00Z';
    const candidateCutoffAt = '2026-09-11T12:00:00Z';
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis,
      status: 'stale', hasReport: true, reportSourceCount: 3, reportSourcesComplete: false,
      sourceCount: 25, relatedSourceCount: 20, dedicatedSourceCount: 5, analyzedCount: 25,
      analysisCutoffAt, completedAt, candidateCutoffAt,
      summary: '上次已完成的分析结论', estimates: [expectation(1)],
      sourceNotes: [
        { topicId: 'report-related', title: '旧报告相关来源', sourceKind: 'related', estimateCount: 1 },
        { topicId: 'report-dedicated', title: '旧报告专属来源', sourceKind: 'dedicated', estimateCount: 0 },
      ],
      candidateSourceNotes: [
        { topicId: 'new-related', title: '待分析的新相关原文', sourceKind: 'related', estimateCount: 0 },
        { topicId: 'new-dedicated', title: '待分析的新专属原文', sourceKind: 'dedicated', estimateCount: 0 },
      ],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    expect(await screen.findByText('有新小作文待更新')).toBeVisible();
    const scope = within(screen.getByLabelText('研究样本范围'));
    expect(scope.getByText('本次可分析 · 相关').parentElement).toHaveTextContent('20');
    expect(scope.getByText('本次可分析 · 单股').parentElement).toHaveTextContent('5');
    const generated = scope.getByText('报告生成').parentElement;
    expect(generated).toHaveTextContent(displayedTime(completedAt));
    expect(generated).toHaveTextContent('已读取 3 篇 · 1 条预期');
    expect(generated).not.toHaveTextContent('已读取 25 篇');
    expect(generated).not.toHaveTextContent(displayedTime(candidateCutoffAt));
    const provenance = screen.getByText(/以下保留上次 AI 分析/);
    expect(provenance).toHaveTextContent(`报告材料截至 ${displayedTime(analysisCutoffAt)}。`);
    expect(provenance).toHaveTextContent(`最新候选材料 ${displayedTime(candidateCutoffAt)}。`);
    expect(provenance).not.toHaveTextContent(`报告材料截至 ${displayedTime(candidateCutoffAt)}`);
    fireEvent.click(screen.getByText('报告引用原文 · 2 篇'));
    expect(screen.getByText('历史输入清单不完整，仅展示可追溯的引用。 点击在页内阅读。')).toBeVisible();
    expect(screen.getByRole('button', { name: /旧报告相关来源/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /旧报告专属来源/ })).toBeVisible();
    expect(screen.queryByText('待分析的新相关原文')).not.toBeInTheDocument();
    expect(screen.queryByText('待分析的新专属原文')).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it.each([
    { reportSourceCount: 0, label: '已读取 0 篇' },
    { reportSourceCount: null, label: '历史样本数未记录' },
  ])('does not borrow candidate counts when the saved report sample count is $reportSourceCount', async ({ reportSourceCount, label }) => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis,
      status: 'stale', hasReport: true, reportSourceCount, analyzedCount: 25, sourceCount: 25,
      summary: '历史结论仍然保留',
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const scope = within(await screen.findByLabelText('研究样本范围'));
    expect(scope.getByText('报告生成').parentElement).toHaveTextContent(label);
    expect(scope.getByText('报告生成').parentElement).not.toHaveTextContent('已读取 25 篇');
  });

  it('does not present a legacy API candidate cutoff or source list as saved report provenance', async () => {
    const item = stock('603306.SH', '华懋科技');
    const candidateCutoffAt = '2026-09-11T12:00:00Z';
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis,
      status: 'stale', sourceCount: 25, analyzedCount: 7,
      summary: '旧接口保留的历史结论', estimates: [expectation(1)],
      // Legacy responses used this field for the newest candidate, not the saved report input.
      analysisCutoffAt: candidateCutoffAt, candidateCutoffAt,
      sourceNotes: [{ topicId: 'legacy-candidate', title: '旧接口当前候选', sourceKind: 'related', estimateCount: 0 }],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const scope = within(await screen.findByLabelText('研究样本范围'));
    const generated = scope.getByText('报告生成').parentElement;
    expect(generated?.querySelector('strong')).toHaveTextContent('—');
    expect(generated).toHaveTextContent('已读取 7 篇 · 1 条预期');
    expect(generated).not.toHaveTextContent(displayedTime(candidateCutoffAt));
    const provenance = screen.getByText(/以下保留上次 AI 分析/);
    expect(provenance).toHaveTextContent('历史报告完整材料截止尚未记录。');
    expect(provenance).not.toHaveTextContent('报告材料截至');
    expect(provenance).not.toHaveTextContent('最新候选材料');
    expect(provenance).not.toHaveTextContent(displayedTime(candidateCutoffAt));
    expect(screen.queryByText(/报告引用原文/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('当前候选原文 · 1 篇'));
    expect(screen.getByText('旧接口返回候选清单，不将它冒充历史报告的实际引用。 点击在页内阅读。')).toBeVisible();
    expect(screen.getByRole('button', { name: /旧接口当前候选/ })).toBeVisible();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('does not claim an existing report when only unanalyzed candidates are available', async () => {
    const item = stock('603306.SH', '华懋科技');
    const candidateCutoffAt = '2026-09-11T12:00:00Z';
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis,
      hasReport: false, reportSourceCount: null, sourceCount: 2, candidateCutoffAt,
      candidateSourceNotes: [
        { topicId: 'new-related', title: '待分析相关原文', sourceKind: 'related', estimateCount: 0 },
        { topicId: 'new-dedicated', title: '待分析专属原文', sourceKind: 'dedicated', estimateCount: 0 },
      ],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const scope = within(await screen.findByLabelText('研究样本范围'));
    expect(scope.getByText('本次可分析 · 相关').parentElement?.querySelector('strong')).toHaveTextContent('1');
    expect(scope.getByText('本次可分析 · 单股').parentElement?.querySelector('strong')).toHaveTextContent('1');
    const generated = scope.getByText('报告生成').parentElement;
    expect(generated).toHaveTextContent('尚未生成专项报告');
    expect(generated?.querySelector('strong')).toHaveTextContent('—');
    expect(generated).not.toHaveTextContent(displayedTime(candidateCutoffAt));
    expect(screen.queryByText(/以下保留上次 AI 分析/)).not.toBeInTheDocument();
    expect(screen.queryByText(/报告材料截至/)).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('distinguishes broker coverage, document-weighted targets and per-metric samples with paged evidence', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.brokerReportCount = 800;
    item.consensus.brokerSample = {
      loaded: true, totalRecordCount: 800, inputRecordCount: 20, reportCount: 13, validReportCount: 3,
      loadedRecordCount: 26, relationExcludedCount: 6, unknownRelationCount: 3,
      duplicateRecordCount: 7, sampleLimit: 200, truncated: true, aggregation: 'report_weighted',
      institutionCount: null, knownInstitutionCount: 2, unknownInstitutionReportCount: 11,
      missingPeriodReportCount: 1, oldestAt: '2026-08-01T08:00:00Z', asOf: '2026-09-01T08:00:00Z',
      sourceNotes: Array.from({ length: 13 }, (_, index) => ({ eventId: 500 + index, title: `计算来源 ${index + 1}`, orgName: index === 0 ? '测试券商' : null })),
    };
    item.consensus.targetPrice = { sampleCount: 6, min: 100, median: 125, max: 200, documentMedian: 135, documentCount: 3 };
    item.consensus.forecasts = [{ period: '2027E', sampleCount: 3, reportCount: 4, npMedian: 100000,
      epsMedian: 1.5, metricSampleCounts: { eps: 3, np: 2, pe: 0, opRt: 0, roe: 0 } }];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    const scope = within(await screen.findByLabelText('研究样本范围'));
    expect(scope.getByText('本次研报样本').parentElement).toHaveTextContent('13');
    expect(scope.getByText('本次研报样本').parentElement).not.toHaveTextContent('800');
    const broker = within(screen.getByRole('heading', { name: '券商预测样本' }).closest('section')!);
    expect(broker.getAllByText('135')).toHaveLength(1);
    expect(broker.queryByText('125')).not.toBeInTheDocument();
    expect(broker.getByText(/可确认 2 家机构；另有 11 份未标注机构/)).toBeVisible();
    expect(broker.getByText(/本次读取 26 条，排除 6 条他股或标的冲突记录后，去重后 13 份研报/)).toBeVisible();
    expect(broker.getByText(/3 份旧记录尚不能确认标的归属，仍纳入样本/)).toBeVisible();
    expect(broker.getByText(/1 份含未标注预测期的记录/)).toBeVisible();
    expect(broker.getByText(/每篇区间先取中点/)).toBeVisible();
    const row = within(broker.getByRole('cell', { name: '2027E' }).closest('tr')!);
    expect(row.getAllByRole('cell').map(cell => cell.textContent)).toEqual(['2027E', '4', '1.53 份有效样本', '102 份有效样本', '—0 份有效样本', '—0 份有效样本']);
    fireEvent.click(broker.getByText('查看计算来源 · 13 份'));
    const sourceList = within(broker.getByRole('list', { name: '券商计算来源列表' }));
    expect(sourceList.getAllByRole('button')).toHaveLength(12);
    const firstSource = broker.getByText('计算来源 1').closest('button');
    expect(firstSource).not.toHaveAttribute('href');
    expect(firstSource).toHaveTextContent('测试券商');
    const pager = within(broker.getByRole('navigation', { name: '券商来源分页' }));
    fireEvent.click(pager.getByRole('button', { name: '下一页' }));
    expect(sourceList.getAllByRole('button')).toHaveLength(1);
    expect(sourceList.getByRole('button')).toHaveTextContent('计算来源 13');
    expect(broker.getByRole('list', { name: '券商计算来源列表' })).toHaveFocus();
    expect(mockEvent).not.toHaveBeenCalled();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('opens only the contributing documents for the selected forecast metric and period, without leaving the stock', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.brokerSample = {
      loaded: true, totalRecordCount: 3, inputRecordCount: 3, reportCount: 3, validReportCount: 3,
      duplicateRecordCount: 0, sampleLimit: 200, truncated: false, aggregation: 'report_weighted',
      institutionCount: 1, knownInstitutionCount: 1, unknownInstitutionReportCount: 0,
      sourceNotes: [501, 502, 503].map(eventId => ({ eventId, title: `研报 ${eventId}`, orgName: '测试证券' })),
    };
    item.consensus.forecasts = [
      { period: '2027E', sampleCount: 2, npMedian: 20000, epsMedian: 0, peMedian: null,
        metricSourceEventIds: { np: [501, 503], eps: [501, 502], pe: [] }, conflictingMetricCounts: { np: 1 } },
      { period: '2028E', sampleCount: 1, npMedian: -10000, metricSourceEventIds: { np: [502] } },
    ];
    mockEvent.mockResolvedValue({ ...essay(503, '精确来源研报'), eventType: 'institution_forecast',
      metrics: { forecasts: [{ quarter: '2027E', np: 30000, eps: 3, pe: 20 }] } });
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus&symbol=603306.SH']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    const value = await screen.findByRole('button', { name: '2027E 净利润 计算来源：2亿元' });
    expect(value).toHaveTextContent('2');
    expect(screen.queryByText('原文目标价下限（元）')).not.toBeInTheDocument();
    expect(screen.getByText('剔除 1 份冲突样本')).toBeVisible();
    expect(screen.queryByRole('button', { name: '2027E PE 计算来源' })).not.toBeInTheDocument();
    fireEvent.click(value);
    const region = screen.getByLabelText('当前券商计算来源');
    expect(region).toHaveFocus();
    let sources = within(screen.getByRole('list', { name: '券商计算来源列表' }));
    expect(sources.getAllByRole('button').map(button => button.textContent)).toEqual([
      expect.stringContaining('研报 501'), expect.stringContaining('研报 503'),
    ]);
    expect(mockEvent).not.toHaveBeenCalled();
    const opener = sources.getByRole('button', { name: /研报 503/ });
    opener.focus(); fireEvent.click(opener);
    const reader = within(await screen.findByRole('dialog'));
    expect(await reader.findByText('精确来源研报')).toBeVisible();
    expect(mockEvent).toHaveBeenCalledExactlyOnceWith(503);
    expect(screen.getByTestId('location')).toHaveTextContent('section=consensus&symbol=603306.SH');
    fireEvent.click(reader.getByRole('button', { name: '关闭抽屉' }));
    await waitFor(() => expect(opener).toHaveFocus());
    fireEvent.click(screen.getByRole('button', { name: '2027E EPS 计算来源：0元' }));
    sources = within(screen.getByRole('list', { name: '券商计算来源列表' }));
    expect(sources.queryByRole('button', { name: /研报 503/ })).not.toBeInTheDocument();
    expect(sources.getByRole('button', { name: /研报 502/ })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '2028E 净利润 计算来源：-1亿元' }));
    expect(screen.getByRole('list', { name: '券商计算来源列表' })).toHaveTextContent('研报 502');
    expect(screen.getByRole('list', { name: '券商计算来源列表' })).not.toHaveTextContent('研报 501');
    fireEvent.click(screen.getByRole('button', { name: '查看全部来源' }));
    expect(screen.getByText('查看计算来源 · 3 份').closest('details')).toHaveAttribute('open');
    expect(within(screen.getByRole('list', { name: '券商计算来源列表' })).getAllByRole('button')).toHaveLength(3);
    expect(mockLoad).toHaveBeenCalledTimes(1);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('does not substitute the period-wide document list when a metric has no saved attribution', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.forecasts = [{ period: '2027E', sampleCount: 1, npMedian: 10000, sourceEventIds: [501],
      epsMedian: 0, metricSourceEventIds: { eps: [0, -1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1] } }];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    const broker = within((await screen.findByRole('heading', { name: '券商预测样本' })).closest('section')!);
    expect(broker.getByRole('cell', { name: '2027E' })).toBeVisible();
    expect(broker.queryByRole('button', { name: /计算来源/ })).not.toBeInTheDocument();
    expect(mockEvent).not.toHaveBeenCalled();
  });

  it('can read a valid contributor ID even when the summary omitted its title, and deduplicates IDs', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.forecasts = [{ period: '2027E', sampleCount: 1, npMedian: 0, metricSourceEventIds: { np: [505, 505] } }];
    mockEvent.mockRejectedValue({ response: { status: 403 } });
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '2027E 净利润 计算来源：0亿元' }));
    expect(screen.getByText('2027E · 净利润的计算来源 · 1 份')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /研报来源记录/ }));
    expect(await screen.findByText('当前账号无法读取该资料，请重新登录或确认访问权限。')).toBeVisible();
    expect(mockEvent).toHaveBeenCalledExactlyOnceWith(505);
    expect(screen.queryByText('原文摘录 505')).not.toBeInTheDocument();
  });

  it('sends the selected company as explicit route context when opening research', async () => {
    respondWith(stock('603306.SH', '华懋科技'));
    render(<MemoryRouter initialEntries={['/super-watchlist']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '问 AI 这只股票' }));
    const query = new URLSearchParams(screen.getByTestId('location').textContent!);
    expect(query.get('stock')).toBe('603306.SH');
    expect(query.get('name')).toBe('华懋科技');
    expect(query.get('prompt')).toContain('比较已披露事实和市场预期');
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('closes a broker reader when refreshed metric attribution changes and ignores its late response', async () => {
    const initial = stock('603306.SH', '华懋科技');
    initial.consensus.forecasts = [{ period: '2027E', sampleCount: 1, npMedian: 10000, metricSourceEventIds: { np: [501] } }];
    const updated = { ...initial, consensus: { ...initial.consensus, forecasts: [
      { period: '2027E', sampleCount: 1, npMedian: 20000, metricSourceEventIds: { np: [502] } },
    ] } };
    const oldRead = deferred<MonitorEvent>();
    mockEvent.mockReturnValueOnce(oldRead.promise).mockResolvedValueOnce({ ...essay(502, '更新来源'), eventType: 'institution_forecast' });
    respondWith(initial);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '2027E 净利润 计算来源：1亿元' }));
    fireEvent.click(screen.getByRole('button', { name: /研报来源记录/ }));
    expect(await screen.findByRole('dialog')).toBeVisible();
    respondWith(updated);
    await act(async () => { fireEvent(document, new Event('visibilitychange')); });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2027E 净利润 计算来源：2亿元' })).toHaveAttribute('aria-pressed', 'true');
    await act(async () => { oldRead.resolve({ ...essay(501, '旧来源不再有效'), eventType: 'institution_forecast' }); });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('旧来源不再有效')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /研报来源记录/ }));
    expect(await screen.findByRole('dialog', { name: '更新来源' })).toBeVisible();
    expect(mockEvent.mock.calls).toEqual([[501], [502]]);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('does not resurrect an obsolete endpoint median when the new document result is null', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.targetPrice = { sampleCount: 2, median: 125, documentMedian: null, documentCount: 0 };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    const broker = within((await screen.findByRole('heading', { name: '券商预测样本' })).closest('section')!);
    expect(broker.queryByText('125')).not.toBeInTheDocument();
    expect(broker.getByText('当前响应暂无可用券商预测；与小作文分析独立，可在资料中核对已入库研报。')).toBeVisible();
    expect(broker.queryByText('文档目标价中位（元）', { selector: 'p' })).not.toBeInTheDocument();
    expect(broker.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows full research dates and original units without inventing a unit or time', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.estimates = [
      expectation(1, { proposedAt: '2025-09-01T16:30:00Z', valueText: '收入 120', unit: '万美元' }),
      expectation(2, { proposedAt: '2025-09-01', valueText: '利润 18', unit: null }),
      expectation(3, { proposedAt: 'invalid', eventAt: '2025-08-31T09:00:00Z' }),
    ];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    const matrix = within(await screen.findByRole('region', { name: '预期证据' }));
    expect(matrix.getByText('2025/09/02 00:30')).toBeVisible();
    expect(matrix.getByText('2025-09-01')).toBeVisible();
    expect(matrix.getByText('2025/08/31 17:00')).toBeVisible();
    expect(matrix.getByText(/原文单位：万美元/)).toBeVisible();
    expect(matrix.getAllByText(/单位未注明，不换算/)).toHaveLength(2);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('keeps supplemental AI observations collapsed and hides empty observation cards', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.conflicts = ['测试材料中的口径差异'];
    item.consensus.essayAnalysis.consensusPoints = [];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    const toggle = await screen.findByText('口径冲突');
    expect(toggle.closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('测试材料中的口径差异')).not.toBeVisible();
    fireEvent.click(toggle);
    expect(screen.getByText('测试材料中的口径差异')).toBeVisible();
    expect(screen.queryByText('共同指向')).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('does not submit a duplicate analysis while the saved consensus task is pending', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.status = 'pending';
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const pending = await screen.findByRole('button', { name: '正在分析小作文' });
    expect(pending).toBeDisabled();
    expect(screen.getByText('等待分析')).toBeVisible();
    fireEvent.click(pending);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('keeps concurrent consensus tasks and their results attached to their own stocks', async () => {
    const huamao = stock('603306.SH', '华懋科技');
    const shenghong = stock('300476.SZ', '胜宏科技');
    huamao.consensus.essayAnalysis.sourceCount = 1;
    shenghong.consensus.essayAnalysis.sourceCount = 1;
    mockGetWatchlist.mockResolvedValue([huamao.symbol, shenghong.symbol]);
    respondWith(huamao, shenghong);
    mockAnalyzeConsensus.mockImplementation(async (symbol: string) => ({
      consensus: { ...stock(symbol, '').consensus.essayAnalysis, sourceCount: 1, status: 'pending' },
    }));
    mockEssayConsensus.mockImplementation(async (symbol: string) => ({
      consensus: { ...stock(symbol, '').consensus.essayAnalysis, sourceCount: 1, status: 'completed', summary: `${symbol} 独立任务结论` },
    }));
    const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    await screen.findByRole('button', { name: '分析现有 1 篇' });
    vi.useFakeTimers();

    await act(async () => fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })));
    expect(screen.getByRole('button', { name: '正在分析小作文' })).toBeDisabled();
    expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: /^胜宏科技.*300476.SZ/ })));
    expect(screen.getByRole('heading', { name: '胜宏科技', level: 2 })).toBeVisible();
    expect(screen.getByRole('button', { name: '分析现有 1 篇' })).toBeEnabled();
    expect(screen.queryByText(/正在提交当前股票/)).not.toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })));
    await act(async () => vi.advanceTimersByTimeAsync(5_000));

    expect(mockAnalyzeConsensus.mock.calls).toEqual([[huamao.symbol], [shenghong.symbol]]);
    expect(mockEssayConsensus.mock.calls).toEqual([[huamao.symbol], [shenghong.symbol]]);
    expect(screen.getByText(`${shenghong.symbol} 独立任务结论`)).toBeVisible();
    expect(screen.queryByText(`${huamao.symbol} 独立任务结论`)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更新报告 · 1 篇' })).toBeEnabled();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: /^华懋科技/ })));
    expect(screen.getByRole('heading', { name: '华懋科技', level: 2 })).toBeVisible();
    expect(screen.getByText(`${huamao.symbol} 独立任务结论`)).toBeVisible();
    expect(screen.queryByText(`${shenghong.symbol} 独立任务结论`)).not.toBeInTheDocument();
    page.unmount();
  });

  it('shows the year and exchange timezone without inventing a publication hour for date-only announcements', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.company.announcements = [
      { ...essay(690, '仅有披露日期的公告'), eventType: 'company_announcement', eventAt: '2025-09-01T00:00:00Z', metrics: { timePrecision: 'date', disclosureDate: '2025-09-01' } },
      { ...essay(691, '有精确时间的公告'), eventType: 'company_announcement', eventAt: '2025-09-01T16:30:00Z' },
    ];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=institution&material=announcements']}><SuperWatchlistPage /></MemoryRouter>);
    const dateOnly = await screen.findByRole('button', { name: /仅有披露日期的公告/ });
    expect(dateOnly.querySelector('time')).toHaveTextContent(/^2025-09-01$/);
    expect(screen.getByRole('button', { name: /有精确时间的公告/ }).querySelector('time')).toHaveTextContent('2025/09/02 00:30');
  });

  it('opens announcements and research with distinct filters without another backend fetch per filter', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.company.announcements = [{ ...essay(700, '去年公司半年报公告'), eventType: 'company_announcement', eventAt: '2025-09-01T08:00:00Z' }];
    item.institution.latest = Array.from({ length: 13 }, (_, index) => ({
      ...essay(710 + index, `较新的券商研究 ${index}`), eventType: 'research_report_pdf',
    }));
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '查看公司公告' }));
    let filter = await screen.findByRole('combobox', { name: '资料筛选' });
    expect(filter).toHaveValue('announcements');
    expect(screen.getByTestId('location')).toHaveTextContent('material=announcements');
    expect(screen.getByText('去年公司半年报公告')).toBeVisible();
    expect(screen.queryByText(/^较新的券商研究/)).not.toBeInTheDocument();
    expect(screen.getByText('1 条已载入资料')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '总览' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看券商研究' }));
    filter = await screen.findByRole('combobox', { name: '资料筛选' });
    expect(filter).toHaveValue('research');
    expect(screen.getByTestId('location')).toHaveTextContent('material=research');
    expect(screen.queryByText('去年公司半年报公告')).not.toBeInTheDocument();
    expect(screen.getByText('13 条已载入资料')).toBeVisible();
    const calls = mockLoad.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
    fireEvent.change(filter, { target: { value: 'announcements' } });
    expect(screen.getByText('去年公司半年报公告')).toBeVisible();
    expect(screen.queryByRole('navigation', { name: '资料分页' })).not.toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledTimes(calls);
  });

  it('restores a linked material filter and combines it with keyword search', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.company.announcements = [{ ...essay(730, '半年报公司公告'), eventType: 'company_announcement' }];
    item.institution.latest = [{ ...essay(731, '半年报券商点评'), eventType: 'research_report_pdf' }];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=institution&material=announcements']}><SuperWatchlistPage /></MemoryRouter>);
    const filter = await screen.findByRole('combobox', { name: '资料筛选' });
    expect(filter).toHaveValue('announcements');
    const search = screen.getByRole('searchbox', { name: '筛选已载入资料' });
    fireEvent.change(search, { target: { value: '点评' } });
    expect(screen.getByText('没有匹配的已载入资料')).toBeVisible();
    fireEvent.change(filter, { target: { value: 'research' } });
    expect(search).toHaveValue('点评');
    expect(screen.getByText('半年报券商点评')).toBeVisible();
    expect(screen.queryByText('半年报公司公告')).not.toBeInTheDocument();
  });

  it('puts direct company research before newer related reports in the overview', async () => {
    const item = stock('300308.SZ', '中际旭创');
    mockGetWatchlist.mockResolvedValue([item.symbol]);
    item.institution.latest = [
      { ...essay(401, '中石科技液冷研究'), eventType: 'research_report_pdf', url: 'https://example.org/other.pdf', stockRelation: { kind: 'other_company_mention', targetSymbol: item.symbol, subjectSymbol: '300684.SZ', subjectName: '中石科技', matchField: 'abstract', matchText: '原始摘要中提及中际旭创。' } },
      { ...essay(402, '光通信行业研究'), eventType: 'research_report_pdf', stockRelation: { kind: 'industry_related', targetSymbol: item.symbol } },
      { ...essay(403, '中际旭创半年报点评'), eventType: 'research_report_pdf', url: 'https://example.org/direct.pdf', stockRelation: { kind: 'company_direct', targetSymbol: item.symbol, subjectSymbol: item.symbol } },
    ];
    respondWith(item);
    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    const first = await screen.findByRole('button', { name: /中际旭创半年报点评/ });
    const group = first.closest('section')!;
    expect(within(group).getAllByRole('button', { name: /查看资料/ }).map(row => row.querySelector('strong')?.textContent)).toEqual(['中际旭创半年报点评', '中石科技液冷研究']);
    expect(within(group).getByText('本公司研究')).toBeVisible();
    expect(within(group).getByText('研报主体：中石科技 · 300684.SZ')).toBeVisible();
    expect(within(group).getByText('其他公司提及')).toBeVisible();
    expect(within(group).getByText('原始摘要中提及中际旭创。')).not.toBeVisible();
    fireEvent.click(within(group).getByText('摘要匹配依据'));
    expect(within(group).getByText('原始摘要中提及中际旭创。')).toBeVisible();
    expect(within(group).queryByText('光通信行业研究')).not.toBeInTheDocument();
  });

  it('filters loaded reports by relationship with pagination reset and an explicit legacy unknown state', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.institution.latest = Array.from({ length: 13 }, (_, index) => ({
      ...essay(index + 501, `本公司报告 ${index + 1}`), eventType: 'research_report_pdf',
      stockRelation: { kind: 'company_direct', targetSymbol: item.symbol, subjectSymbol: item.symbol },
    }));
    item.institution.latest.push(
      { ...essay(600, '相关公司研究'), eventType: 'research_report_pdf', stockRelation: { kind: 'other_company_mention', targetSymbol: item.symbol, subjectSymbol: '300684.SZ' } },
      { ...essay(601, '汽车行业研究'), eventType: 'research_report_pdf', stockRelation: { kind: 'industry_related', targetSymbol: item.symbol } },
      { ...essay(602, '旧版未记录关联'), eventType: 'research_report_pdf' },
      { ...essay(603, '返回标的不匹配'), eventType: 'research_report_pdf', stockRelation: { kind: 'company_direct', targetSymbol: '300308.SZ', subjectName: '不应复用的公司名' } },
    );
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=institution']}><SuperWatchlistPage /></MemoryRouter>);
    const filter = await screen.findByRole('combobox', { name: '资料筛选' });
    fireEvent.change(filter, { target: { value: 'company_direct' } });
    expect(screen.getByText('13 条已载入资料')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(within(screen.getByRole('navigation', { name: '资料分页' })).getByText('第 2 / 2 页')).toBeVisible();
    fireEvent.change(filter, { target: { value: 'other_company_mention' } });
    expect(screen.getByText('1 条已载入资料')).toBeVisible();
    expect(screen.getByText('相关公司研究')).toBeVisible();
    expect(screen.getByText('研报主体：300684.SZ')).toBeVisible();
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument();
    fireEvent.change(filter, { target: { value: 'industry_related' } });
    expect(screen.getByText('汽车行业研究')).toBeVisible();
    fireEvent.change(filter, { target: { value: 'unknown' } });
    expect(screen.getByText('2 条已载入资料')).toBeVisible();
    expect(screen.getByText('旧版未记录关联')).toBeVisible();
    expect(screen.getByText('返回标的不匹配')).toBeVisible();
    expect(screen.queryByText('不应复用的公司名')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '筛选已载入资料' }), { target: { value: '不存在' } });
    expect(screen.getByText('没有匹配的已载入资料')).toBeVisible();
    expect(screen.getByText('可修改关键词或资料筛选；此筛选不代表全库检索结果。')).toBeVisible();
  });

  it('stops consensus status polling after leaving the page without cancelling the server task', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.sourceCount = 1;
    respondWith(item);
    mockAnalyzeConsensus.mockResolvedValue({ consensus: { ...item.consensus.essayAnalysis, status: 'pending' } });
    mockEssayConsensus.mockResolvedValue({ consensus: { ...item.consensus.essayAnalysis, status: 'processing' } });
    const page = render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);
    await screen.findByRole('button', { name: '分析现有 1 篇' });
    vi.useFakeTimers();

    await act(async () => fireEvent.click(screen.getByRole('button', { name: '分析现有 1 篇' })));
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(mockEssayConsensus).toHaveBeenCalledTimes(1);
    expect(mockEssayConsensus).toHaveBeenCalledWith(item.symbol);
    page.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(mockEssayConsensus).toHaveBeenCalledTimes(1);
    expect(mockAnalyzeConsensus).toHaveBeenCalledTimes(1);
  });

  it('disables an empty essay analysis without hiding independent broker predictions', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.hasReport = false;
    item.consensus.essayAnalysis.candidateSourceNotes = [];
    item.consensus.forecasts = [{ period: '2027E', sampleCount: 2, epsMedian: 2.34, npMedian: 80000 }];
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const run = await screen.findByRole('button', { name: '暂无可分析小作文' });
    expect(run).toBeDisabled();
    fireEvent.click(run);
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
    expect(screen.getByText('尚未分析').parentElement).toHaveTextContent('小作文：尚未分析');
    expect(screen.getByRole('region', { name: '小作文分析状态' })).toHaveTextContent('不会提交空分析任务');
    expect(screen.queryByText('综合结论')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '预期证据' })).not.toBeInTheDocument();
    expect(screen.queryByText('利润与经营预期')).not.toBeInTheDocument();
    expect(screen.queryByText('估值与市值推测')).not.toBeInTheDocument();
    expect(screen.queryByText(/按提出时间查看预期变化/)).not.toBeInTheDocument();
    expect(screen.queryByText(/当前候选原文/)).not.toBeInTheDocument();
    const broker = within(screen.getByRole('heading', { name: '券商预测样本' }).closest('section')!);
    expect(broker.getByRole('cell', { name: '2027E' })).toBeVisible();
    expect(broker.getByRole('cell', { name: '2.34' })).toBeVisible();
    expect(broker.getByRole('cell', { name: '8' })).toBeVisible();
  });

  it('uses a compact initial state when neither essay results nor broker predictions are returned', async () => {
    respondWith(stock('603306.SH', '华懋科技'));
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    expect(await screen.findByRole('heading', { name: '尚未生成小作文报告' })).toBeVisible();
    expect(screen.getByText('当前响应暂无可用券商预测；与小作文分析独立，可在资料中核对已入库研报。')).toBeVisible();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText('原文目标价下限（元）')).not.toBeInTheDocument();
    expect(screen.queryByText('原文目标价上限（元）')).not.toBeInTheDocument();
    expect(screen.queryByText('综合结论')).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('labels a first analysis by the actual candidate count and keeps candidate originals readable', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis, hasReport: false, sourceCount: 2,
      candidateSourceNotes: [
        { topicId: 'first-related', title: '首次候选相关原文', sourceKind: 'related', estimateCount: 0 },
        { topicId: 'first-dedicated', title: '首次候选专属原文', sourceKind: 'dedicated', estimateCount: 0 },
      ],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    expect(await screen.findByRole('button', { name: '分析现有 2 篇' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /更新报告|重新分析/ })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '小作文分析状态' })).toHaveTextContent('当前有 2 篇候选小作文');
    expect(screen.queryByRole('region', { name: '预期证据' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('当前候选原文 · 2 篇'));
    fireEvent.click(screen.getByRole('button', { name: /首次候选专属原文/ }));
    expect(await screen.findByRole('dialog')).toBeVisible();
    expect(mockNote).toHaveBeenCalledExactlyOnceWith('first-dedicated');
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('keeps a failed first analysis actionable and exposes its failure without empty report cards', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis, hasReport: false, sourceCount: 1, status: 'failed',
      error: '测试错误：上游未返回完整内容',
      candidateSourceNotes: [{ topicId: 'retry-original', title: '可以重试的候选原文', sourceKind: 'related', estimateCount: 0 }],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    expect(await screen.findByRole('button', { name: '重试分析 · 1 篇' })).toBeEnabled();
    expect(screen.getByText('小作文分析失败：测试错误：上游未返回完整内容')).toBeVisible();
    expect(screen.getByRole('heading', { name: '本次小作文分析未完成' })).toBeVisible();
    expect(screen.queryByText('综合结论')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '预期证据' })).not.toBeInTheDocument();
    expect(screen.getByText('当前候选原文 · 1 篇')).toBeVisible();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('does not treat historical report sources as new candidates and retains the old report after a failed update', async () => {
    const item = stock('603306.SH', '华懋科技');
    const completedAt = '2026-08-10T08:00:00Z';
    item.consensus.essayAnalysis = {
      ...item.consensus.essayAnalysis, hasReport: true, status: 'failed', sourceCount: 0,
      relatedSourceCount: 0, dedicatedSourceCount: 0, reportSourceCount: 1, analyzedCount: 1,
      summary: '窗口变化后仍保留的历史结论', completedAt, analysisCutoffAt: '2026-08-09T08:00:00Z',
      error: '测试错误：更新分析失败', candidateSourceNotes: [], reportSourcesComplete: true,
      estimates: [expectation(1, { topicId: 'old-report-original', title: '历史已分析原文' })],
      sourceNotes: [{ topicId: 'old-report-original', title: '历史已分析原文', sourceKind: 'related', estimateCount: 1, eventAt: '2026-08-09T08:00:00Z' }],
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    const run = await screen.findByRole('button', { name: '暂无可分析小作文' });
    expect(run).toBeDisabled();
    fireEvent.click(run);
    expect(screen.getByText('窗口变化后仍保留的历史结论')).toBeVisible();
    expect(screen.getByRole('region', { name: '预期证据' })).toBeVisible();
    expect(screen.getByText('小作文分析失败：测试错误：更新分析失败')).toBeVisible();
    expect(screen.getByText('当前没有可供重新分析的小作文，以下历史报告和可追溯原文仍可查看。')).toBeVisible();
    expect(screen.getByLabelText('研究样本范围')).toHaveTextContent(displayedTime(completedAt));
    fireEvent.click(screen.getByText('报告引用原文 · 1 篇'));
    fireEvent.click(screen.getByRole('button', { name: /相关样本.*历史已分析原文/ }));
    expect(await screen.findByRole('dialog')).toBeVisible();
    expect(mockNote).toHaveBeenCalledExactlyOnceWith('old-report-original');
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('preserves legacy report text even when no candidate, summary or numeric expectation is available', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.consensus.essayAnalysis.status = 'completed';
    item.consensus.essayAnalysis.profitOutlook = '旧报告保留的经营预期，不含明确数值';
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=consensus']}><SuperWatchlistPage /></MemoryRouter>);

    expect(await screen.findByText('旧报告保留的经营预期，不含明确数值')).toBeVisible();
    expect(screen.getByRole('button', { name: '暂无可分析小作文' })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: '尚未生成小作文报告' })).not.toBeInTheDocument();
    expect(screen.getByText('报告未提取到明确预期')).toBeVisible();
    expect(screen.queryByText(/按提出时间查看预期变化/)).not.toBeInTheDocument();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('keeps chip, technical and moneyflow dates distinct in the capital view', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = {
      ...item.capital, chipTradeDate: '20260828', source: 'Tushare 筹码统计',
      winnerRate: 12.5, weightedCost: 73.4,
      moneyflow: { tradeDate: '20260910', netMfAmount: 2_000 },
    };
    item.technical = { tradeDate: '20260911', rsi6: 51.2 };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const chipHeader = (await screen.findByRole('heading', { name: '筹码分布' })).closest('header')!;
    expect(chipHeader).toHaveTextContent('筹码日期 2026-08-28 · Tushare 筹码统计');
    expect(chipHeader).not.toHaveTextContent('2026-09-11');
    expect(chipHeader).not.toHaveTextContent('2026-09-10');
    const technicalToggle = screen.getByText('价格状态与技术指标');
    expect(technicalToggle.closest('details')).not.toHaveAttribute('open');
    fireEvent.click(technicalToggle);
    const technicalHeader = screen.getByRole('heading', { name: '价格状态' }).closest('header')!;
    expect(technicalHeader).toHaveTextContent('技术指标日期 2026-09-11');
    expect(technicalHeader).not.toHaveTextContent('2026-08-28');
    expect(screen.getByText('估算获利比例').closest('.super-metric')).toHaveTextContent('12.50%');
    expect(screen.getByText('估算获利比例').closest('.super-metric')).not.toHaveTextContent('+12.50%');
    expect(mockLoad).toHaveBeenCalledWith('603306.SH', 183, false, 'full');
  });

  it('leaves an unknown chip date unlabelled instead of borrowing another date and preserves zero winner rate', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = {
      ...item.capital, winnerRate: 0,
      moneyflow: { tradeDate: '20260910', netMfAmount: 2_000 },
    };
    item.technical = { tradeDate: '20260911', rsi6: 51.2 };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const chipHeader = (await screen.findByRole('heading', { name: '筹码分布' })).closest('header')!;
    expect(chipHeader).toHaveTextContent('筹码日期 未标注 · 筹码来源未标注');
    expect(chipHeader).not.toHaveTextContent('2026-09-11');
    expect(chipHeader).not.toHaveTextContent('2026-09-10');
    expect(screen.getByText('估算获利比例').closest('.super-metric')).toHaveTextContent('0.00%');
    fireEvent.click(screen.getByText('价格状态与技术指标'));
    expect(screen.getByRole('heading', { name: '价格状态' }).closest('header')).toHaveTextContent('技术指标日期 2026-09-11');
  });

  it('uses independent funding dates and units without deriving total flow from order buckets', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = {
      ...item.capital, chipTradeDate: '20260828', moneyflowTradeDate: '20260910',
      marginTradeDate: '20260909', northboundTradeDate: '20240816',
      winnerRate: 12.5,
      moneyflow: {
        netMfAmount: 12_345,
        buySmAmount: 2.5, sellSmAmount: 15,
        buyMdAmount: 7, sellMdAmount: 4,
        buyLgAmount: 9, sellLgAmount: 5,
        buyElgAmount: 0.3, sellElgAmount: 0.2,
      },
      margin: { rzye: 200_000_000, rzmre: 7_000_000, rzche: 9_000_000 },
      northbound: { ratio: 1.25 },
    };
    item.technical = { tradeDate: '20260911', rsi6: 51.2 };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const flow = within(await screen.findByRole('region', { name: '个股资金流' }));
    expect(flow.getByText('资金流日期 2026-09-10')).toBeVisible();
    expect(flow.getByText('资金净流入').closest('.super-metric')).toHaveTextContent('+1.23 亿元');
    expect(flow.getByText('小单').parentElement).toHaveTextContent('-12.5 万元');
    expect(flow.getByText('中单').parentElement).toHaveTextContent('+3 万元');
    expect(flow.getByText('大单').parentElement).toHaveTextContent('+4 万元');
    expect(flow.getByText('特大单').parentElement).toHaveTextContent('+1,000 元');
    expect(flow.getByText(/不能相加替代上方资金净流入/)).toBeVisible();
    const financing = within(screen.getByRole('region', { name: '融资交易' }));
    expect(financing.getByText('融资日期 2026-09-09')).toBeVisible();
    expect(financing.getByText('融资净买入').closest('.super-metric')).toHaveTextContent('-200 万元');
    expect(financing.getByText('融资余额').closest('.super-metric')).toHaveTextContent('2 亿元');
    expect(financing.getByText('融资买入额').closest('.super-metric')).toHaveTextContent('700 万元');
    expect(financing.getByText('融资偿还额').closest('.super-metric')).toHaveTextContent('900 万元');

    const requestsBeforeExpansion = mockLoad.mock.calls.length;
    const technicalToggle = screen.getByText('价格状态与技术指标');
    const northboundToggle = screen.getByText('最近披露北向持股');
    expect(technicalToggle.closest('details')).not.toHaveAttribute('open');
    expect(northboundToggle.closest('details')).not.toHaveAttribute('open');
    fireEvent.click(technicalToggle);
    fireEvent.click(northboundToggle);
    expect(screen.getByRole('heading', { name: '价格状态' }).closest('header')).toHaveTextContent('技术指标日期 2026-09-11');
    expect(northboundToggle.closest('details')).toHaveTextContent('披露日期 2024-08-16 · 持股比例 1.25%');
    expect(northboundToggle.closest('details')).not.toHaveTextContent('+1.25%');
    expect(northboundToggle.closest('details')).toHaveTextContent('不是当日净买入');
    expect(mockLoad).toHaveBeenCalledTimes(requestsBeforeExpansion);
    expect(mockEvent).not.toHaveBeenCalled();
    expect(mockRefresh).not.toHaveBeenCalled();
    expect(mockAnalyzeConsensus).not.toHaveBeenCalled();
  });

  it('preserves genuine funding zeros and leaves incomplete or invalid differences missing', async () => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = {
      ...item.capital, chipTradeDate: '20260828',
      moneyflow: {
        netMfAmount: 0,
        buySmAmount: 0, sellSmAmount: 0,
        buyMdAmount: 5,
        sellLgAmount: 5,
        buyElgAmount: -1, sellElgAmount: 0,
      },
      margin: { rzye: 0, rzmre: 0 },
      northbound: { ratio: 0 },
    };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const flow = within(await screen.findByRole('region', { name: '个股资金流' }));
    expect(flow.getByText('资金流日期 未标注')).toBeVisible();
    expect(flow.getByText('资金净流入').closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^0 元$/);
    expect(flow.getByText('小单').parentElement?.querySelector('dd')).toHaveTextContent(/^0 元$/);
    for (const label of ['中单', '大单', '特大单']) {
      expect(flow.getByText(label).parentElement?.querySelector('dd')).toHaveTextContent(/^—$/);
    }
    const financing = within(screen.getByRole('region', { name: '融资交易' }));
    expect(financing.getByText('融资日期 未标注')).toBeVisible();
    expect(financing.getByText('融资净买入').closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^—$/);
    for (const label of ['融资余额', '融资买入额']) {
      expect(financing.getByText(label).closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^0 元$/);
    }
    const northboundToggle = screen.getByText('最近披露北向持股');
    fireEvent.click(northboundToggle);
    expect(northboundToggle.closest('details')).toHaveTextContent('披露日期 未标注 · 持股比例 0.00%');
  });

  it.each([null, undefined, true, '12', Number.NaN, Number.POSITIVE_INFINITY])('does not coerce invalid funding input %s into an amount or ratio', async invalid => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = {
      ...item.capital,
      moneyflow: { netMfAmount: invalid, buySmAmount: invalid, sellSmAmount: 1, buyMdAmount: 1, sellMdAmount: invalid },
      margin: { rzye: invalid, rzmre: invalid, rzche: 1 },
      northbound: { ratio: invalid },
    };
    // Runtime API input can violate the static number contract; keep the raw
    // value so the display guard, not a test-side coercion, rejects it.
    Object.assign(item.capital, { winnerRate: invalid });
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const flow = within(await screen.findByRole('region', { name: '个股资金流' }));
    expect(flow.getByText('资金净流入').closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^—$/);
    for (const label of ['小单', '中单']) {
      expect(flow.getByText(label).parentElement?.querySelector('dd')).toHaveTextContent(/^—$/);
    }
    const financing = within(screen.getByRole('region', { name: '融资交易' }));
    for (const label of ['融资净买入', '融资余额', '融资买入额']) {
      expect(financing.getByText(label).closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^—$/);
    }
    expect(screen.queryByText('最近披露北向持股')).not.toBeInTheDocument();
    expect(screen.getByText('估算获利比例').closest('.super-metric')?.querySelector('strong')).toHaveTextContent(/^—$/);
  });

  it.each([
    { ratio: -1, label: '—', valid: false },
    { ratio: 100.1, label: '—', valid: false },
    { ratio: 100, label: '100.00%', valid: true },
  ])('accepts only a bounded level percentage for chip and northbound ratio $ratio', async ({ ratio, label, valid }) => {
    const item = stock('603306.SH', '华懋科技');
    item.capital = { ...item.capital, winnerRate: ratio, northbound: { ratio } };
    respondWith(item);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const metric = (await screen.findByText('估算获利比例')).closest('.super-metric')?.querySelector('strong');
    expect(metric?.textContent).toBe(label);
    const northboundToggle = screen.queryByText('最近披露北向持股');
    if (valid) {
      expect(northboundToggle).toBeInTheDocument();
      fireEvent.click(northboundToggle!);
      expect(northboundToggle!.closest('details')).toHaveTextContent(`披露日期 未标注 · 持股比例 ${label}`);
    } else {
      expect(northboundToggle).not.toBeInTheDocument();
    }
  });

  it('keeps funding observations attached to their stock while the next stock is loading', async () => {
    const first = stock('603306.SH', '华懋科技');
    first.capital = { ...first.capital, moneyflowTradeDate: '20260910', moneyflow: { netMfAmount: 88 }, margin: { rzye: 900_000 } };
    const next = stock('300476.SZ', '胜宏科技');
    next.capital = { ...next.capital, moneyflowTradeDate: '20260909', moneyflow: { netMfAmount: -250 }, margin: { rzye: 40_000_000 } };
    mockGetWatchlist.mockResolvedValue([first.symbol, next.symbol]);
    let finishNext!: (value: ReturnType<typeof workspace>) => void;
    mockLoad.mockImplementation((symbol, _days, _force, view) => symbol === next.symbol
      ? new Promise(resolve => { finishNext = resolve; })
      : Promise.resolve(workspace(first, view)));
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /><LocationProbe /></MemoryRouter>);

    const firstFlow = within(await screen.findByRole('region', { name: '个股资金流' }));
    expect(firstFlow.getByText('资金净流入').closest('.super-metric')).toHaveTextContent('+88 万元');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^胜宏科技.*300476.SZ/ })); });
    expect(screen.getByTestId('location')).toHaveTextContent('symbol=300476.SZ');
    expect(screen.getByLabelText('正在加载 300476.SZ 数据')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '个股资金流' })).not.toBeInTheDocument();
    expect(mockLoad).toHaveBeenCalledWith(next.symbol, 183, false, 'full');
    await act(async () => finishNext(workspace(next, 'full')));
    const nextFlow = within(screen.getByRole('region', { name: '个股资金流' }));
    expect(screen.getByRole('heading', { name: '胜宏科技' })).toBeInTheDocument();
    expect(nextFlow.getByText('资金流日期 2026-09-09')).toBeVisible();
    expect(nextFlow.getByText('资金净流入').closest('.super-metric')).toHaveTextContent('-250 万元');
    expect(nextFlow.queryByText('+88 万元')).not.toBeInTheDocument();
    expect(screen.getByText('融资余额').closest('.super-metric')).toHaveTextContent('4,000 万元');
  });

  it('renders zero and numeric chip cost percentiles from the real recursive API key conversion', async () => {
    const fromWire = toCamelCase<ReturnType<typeof workspace>>({
      view: 'full',
      stock: {
        ...stock('603306.SH', '华懋科技'),
        capital: {
          chip_trade_date: '2026-09-10', winner_rate: 0, weighted_cost: 76.4,
          cost_50pct: 0, cost_85pct: 78.25,
          moneyflow: {}, margin: {}, northbound: {}, chip_distribution: [],
        },
      },
    });
    // camelcase-keys capitalizes the suffix after the numeric segment. A
    // hand-authored cost50pct fixture would conceal a real API/UI mismatch.
    expect(fromWire.stock.capital).toHaveProperty('cost50Pct', 0);
    expect(fromWire.stock.capital).toHaveProperty('cost85Pct', 78.25);
    expect(fromWire.stock.capital).not.toHaveProperty('cost50pct');
    mockLoad.mockResolvedValue(fromWire);
    render(<MemoryRouter initialEntries={['/super-watchlist?section=capital']}><SuperWatchlistPage /></MemoryRouter>);

    const cost50 = (await screen.findByText('50%成本分位')).closest('.super-metric')?.querySelector('strong');
    expect(cost50?.textContent).toBe('0');
    expect(screen.getByText('85%成本分位').closest('.super-metric')?.querySelector('strong')?.textContent).toBe('78.25');
    expect(screen.getByText('估算获利比例').closest('.super-metric')?.querySelector('strong')?.textContent).toBe('0.00%');
    expect(mockLoad).toHaveBeenCalledWith('603306.SH', 183, false, 'full');
  });
});
