import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SuperWatchlistDashboard, SuperWatchlistStock } from '../../types/investmentMonitor';
import SuperWatchlistPage from '../SuperWatchlistPage';

const { mockAdd, mockLoad, mockEvent, mockRemove, mockQuotes, mockNote, mockRefresh, mockAnalyzeConsensus, mockEssayConsensus } = vi.hoisted(() => ({
  mockAdd: vi.fn(),
  mockLoad: vi.fn(),
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
    superWatchlist: mockLoad,
    event: mockEvent,
    backfillWatchlist: vi.fn(),
    refreshSuperWatchlist: mockRefresh,
    analyzeEssayConsensus: mockAnalyzeConsensus,
    essayConsensus: mockEssayConsensus,
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
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
  MarketTimeframeChart: ({ symbol }: { symbol: string }) => <div data-testid="market-chart">{symbol}</div>,
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

function dashboard(stocks: SuperWatchlistStock[]): SuperWatchlistDashboard {
  return {
    version: 'test', generatedAt: '2026-08-20T10:00:00+08:00', days: 183,
    stocks, backfillJobs: [], comparison: [], iterations: [],
  };
}

describe('SuperWatchlistPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockQuotes.clear();
    mockAdd.mockResolvedValue(['603306', '300476']);
    mockRemove.mockResolvedValue(['300476']);
    mockNote.mockResolvedValue({
      topicId: 'topic-88', groupId: '1', groupName: '测试星球',
      title: '华懋下半年订单跟踪', content: '华懋订单可能改善。',
      authorName: '研究员', topicType: 'talk', digested: false, sticky: false,
      symbols: ['603306.SH'],
      files: [{ fileId: 'file-1', name: '调研附件.pdf', viewUrl: 'https://example.test/file.pdf' }],
      images: [{ imageId: 'image-1', viewUrl: 'https://example.test/image.jpg' }],
      counts: {},
      createdAt: '2026-08-20T08:00:00Z',
    });
    mockEvent.mockReset();
  });

  it('adds a watchlist stock by selecting its Chinese name', async () => {
    mockLoad.mockResolvedValue(dashboard([stock('603306.SH', '华懋科技')]));

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
    mockLoad.mockResolvedValue(dashboard([item]));

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    expect((await screen.findAllByText('74.28')).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('70.11')).not.toBeInTheDocument();
    expect(screen.getByText(/最新行情/)).toBeInTheDocument();
  });

  it('requires confirmation, removes one stock, and keeps the remaining stock visible', async () => {
    const initial = dashboard([stock('603306', '华懋科技'), stock('300476', '胜宏科技')]);
    mockLoad.mockResolvedValueOnce(initial).mockResolvedValue(dashboard([initial.stocks[1]]));

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: '删除自选股 华懋科技' }));
    expect(screen.getByText(/已入库的历史行情、公告和研究资料会保留/)).toBeInTheDocument();
    expect(mockRemove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith('603306'));
    await waitFor(() => expect(screen.queryByText('华懋科技')).not.toBeInTheDocument());
    expect(screen.getAllByText('胜宏科技').length).toBeGreaterThan(0);
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
    mockLoad.mockResolvedValue(dashboard([item]));

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '小作文' }));

    const original = screen.getByRole('button', { name: /华懋下半年订单跟踪.*查看原文/ });
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
    mockLoad.mockResolvedValue(dashboard([item]));
    mockEvent.mockResolvedValue(comment);

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '股评监控' }));

    const detailButton = screen.getByRole('button', { name: /华懋订单讨论.*15评 · 详情/ });
    expect(screen.queryByRole('link', { name: /华懋订单讨论/ })).not.toBeInTheDocument();
    fireEvent.click(detailButton);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByText('公开帖子正文摘录：订单边际改善仍需核验。')).toBeInTheDocument();
    expect(screen.getAllByText('股友甲').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('12,000')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '股评图片 1' })).toHaveAttribute('src', 'https://example.test/post.jpg');
    expect(screen.getByRole('link', { name: /在东方财富查看原帖/ })).toHaveAttribute('href', comment.url);
    expect(mockEvent).toHaveBeenCalledWith(901);
  });

  it('renders the latest-20 essay expectation analysis in a full research workspace', async () => {
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
        status: 'completed', sourceCount: 20, analyzedCount: 20, pendingCount: 0,
        summary: '近期材料同时给出利润与估值推测。', hasExplicitExpectations: true,
        profitOutlook: '部分材料推测 2026 年净利润约 8 亿元。',
        valuationOutlook: '有材料给出 100 元目标价和 300 亿元目标市值。',
        estimates: [
          { topicId: 'topic-profit', subject: '华懋科技', subjectRelation: 'target_stock', metric: 'net_profit', period: '2026E', valueText: '净利润约 8 亿元', evidence: '预计全年净利润约 8 亿元', confidence: 0.91 },
          { eventId: 999, topicId: 'topic-price', subject: '富创优越', subjectRelation: 'acquisition_target', metric: 'target_price', period: '12个月', valueText: '目标价 100 元', evidence: '目标价看到 100 元', confidence: 0.84 },
        ],
        metricCounts: { net_profit: 1, target_price: 1 },
        consensusPoints: ['盈利方向偏积极'], conflicts: [], caveats: ['均为未经核验的小作文推测'], sourceNotes: [],
      },
    };
    mockLoad.mockResolvedValue(dashboard([item]));
    mockNote.mockResolvedValue({
      topicId: 'topic-price', groupId: '1', groupName: '测试星球', title: '华懋目标价跟踪',
      content: '原文明确写到目标价看到 100 元。', authorName: '研究员', topicType: 'talk',
      digested: false, sticky: false, symbols: ['603306.SH'], files: [], images: [], counts: {},
      createdAt: '2026-08-20T08:00:00Z',
    });

    render(<MemoryRouter><SuperWatchlistPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '一致预期' }));

    expect(screen.getByText('一致预期研究工作台')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新分析最近20篇' })).toBeInTheDocument();
    expect(screen.getByText('净利润约 8 亿元')).toBeInTheDocument();
    expect(screen.getByText('目标价 100 元')).toBeInTheDocument();
    expect(screen.getByText('富创优越 · 收购标的')).toBeInTheDocument();
    expect(screen.getByText('利润预期').closest('.super-expectation-anchor')).toHaveTextContent('1');
    expect(screen.getAllByText('目标价')[0].closest('.super-expectation-anchor')).toHaveTextContent('1');
    expect(screen.getByText('均为未经核验的小作文推测')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /目标价.*目标价 100 元/ }));
    expect(await screen.findByText('原文明确写到目标价看到 100 元。')).toBeInTheDocument();
    expect(mockNote).toHaveBeenCalledWith('topic-price');
  });
});
