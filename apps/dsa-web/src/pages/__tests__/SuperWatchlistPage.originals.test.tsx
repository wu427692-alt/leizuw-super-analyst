import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MonitorEvent, SuperWatchlistStock } from '../../types/investmentMonitor';
import type { ResearchNoteDetail } from '../../types/essayRadar';
import SuperWatchlistPage from '../SuperWatchlistPage';

const { mockLoad, mockEvent, mockNote, mockGetWatchlist } = vi.hoisted(() => ({
  mockLoad: vi.fn(), mockEvent: vi.fn(), mockNote: vi.fn(), mockGetWatchlist: vi.fn(),
}));
vi.mock('../../api/investmentMonitor', () => ({ investmentMonitorApi: {
  stockWorkspace: mockLoad, event: mockEvent, refreshSuperWatchlist: vi.fn(),
} }));
vi.mock('../../api/systemConfig', () => ({ systemConfigApi: { getWatchlist: mockGetWatchlist } }));
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: { note: mockNote } }));
vi.mock('../../hooks/useStockIndex', () => ({ useStockIndex: () => ({ index: [], loading: false, loaded: true, error: null }) }));
vi.mock('../../hooks/useRealtimeQuotes', () => ({ useRealtimeQuotes: () => ({ quotes: new Map(), keyFor: (symbol: string) => symbol }) }));
vi.mock('../../components/market', () => ({ MarketTimeframeChart: () => <div>行情图测试替身</div> }));

function event(id: number, type: 'essay' | 'stock_forum_post' = 'essay'): MonitorEvent {
  return {
    id, title: `资料标题 ${id}`, externalId: `topic-${id}`, eventType: type,
    sourceKey: type === 'essay' ? 'zsxq.essays' : 'public.forum',
    sourceName: type === 'essay' ? '机构段子来源' : '实际股评来源', sourceType: 'html',
    perspective: 'investor', summary: `列表摘录 ${id}`, url: `https://example.test/original/${id}`,
    symbols: ['603306.SH'], sentiment: 'neutral', importanceScore: 0, confidenceScore: 0,
    tags: [], actors: ['原作者'], metrics: {}, eventAt: '2026-09-11T01:00:00Z',
  };
}

function note(item: MonitorEvent, overrides: Partial<ResearchNoteDetail> = {}): ResearchNoteDetail {
  return {
    topicId: item.externalId, groupId: 'group-1', groupName: '真实资料组',
    title: item.title, content: `入库正文 ${item.id}`, authorName: '原作者',
    topicType: 'talk', digested: false, sticky: false, symbols: item.symbols,
    files: [], images: [], counts: {}, createdAt: item.eventAt, ...overrides,
  };
}

function workspace(items: MonitorEvent[]): SuperWatchlistStock {
  const essays = items.filter(item => item.eventType === 'essay');
  const comments = items.filter(item => item.eventType === 'stock_forum_post');
  return {
    symbol: '603306.SH', name: '华懋科技', history: [], market: { price: 76, changePct: 0 },
    valuation: {}, technical: {}, fundamentals: {},
    capital: { moneyflow: {}, margin: {}, northbound: {}, chipDistribution: [] },
    ownership: { pledge: {}, shareUnlock: [], holderTrades: [], repurchases: [] },
    institution: { researchCount: 0, latest: [], institutions: [] },
    company: { profile: {}, announcementCount: 0, announcements: [] },
    alternative: { essayCount: essays.length, essays, catalysts: [], risks: [] },
    consensus: {
      brokerReportCount: 0, ratings: [], targetPrice: { sampleCount: 0 }, forecasts: [],
      essayExpectationCount: 0, essayExpectations: [], method: '测试',
      essayAnalysis: {
        status: 'not_started', sourceCount: 0, analyzedCount: 0, pendingCount: 0,
        summary: '', hasExplicitExpectations: false, profitOutlook: '', valuationOutlook: '',
        estimates: [], metricCounts: {}, consensusPoints: [], conflicts: [], caveats: [], sourceNotes: [],
      },
    },
    messages: { count: 0, items: [], channels: [] },
    stockComments: { count: comments.length, items: comments, sourceNote: '公开列表摘录' },
    signals: [], coverage: [], timeline: [],
    evidence: { eventCount: 0, rawEventCount: 0, factualCount: 0, unverifiedCount: 0, sourceCount: 0, originalLinkCount: 0, originalLinkCoverage: 0, channels: [] },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

async function openOriginal(item: MonitorEvent) {
  // Flush the watchlist -> stock-workspace promise chain before querying the
  // drawer trigger; the detail request itself remains controlled by each test.
  await act(async () => { await Promise.resolve(); });
  const row = await screen.findByRole('button', { name: new RegExp(`${item.title}.*(?:查看原文|详情)`) });
  fireEvent.click(row);
  return screen.findByRole('dialog');
}

function renderItems(items: MonitorEvent[], strict = false) {
  mockLoad.mockResolvedValue({ stock: workspace(items), view: 'full' });
  const page = <MemoryRouter initialEntries={[`/super-watchlist?section=${items[0].eventType === 'essay' ? 'essay' : 'comments'}`]}><SuperWatchlistPage /></MemoryRouter>;
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}

describe('SuperWatchlist original readers', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockGetWatchlist.mockResolvedValue(['603306.SH']);
  });
  afterEach(cleanup);

  it('preserves the labelled forum excerpt during loading and failure, then retries only its detail read', async () => {
    const item = event(1, 'stock_forum_post');
    const pending = deferred<MonitorEvent>();
    mockEvent.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ ...item, summary: '更长的已入库摘录' });
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(within(dialog).getByText('正在读取已入库详情，以下为列表摘录。')).toBeInTheDocument();
    expect(within(dialog).getByText(item.summary!)).toBeInTheDocument();
    expect(within(dialog).getByText(/不保证包含完整帖子/)).toBeInTheDocument();
    const workspaceReads = mockLoad.mock.calls.length;

    await act(async () => pending.reject(new Error('upstream raw payload: SECRET_TEST_ONLY')));
    expect(within(dialog).getByText('详情暂未读取成功，以下仍为列表摘录。')).toBeInTheDocument();
    expect(within(dialog).queryByText(/SECRET_TEST_ONLY/)).not.toBeInTheDocument();
    expect(within(dialog).getByText(item.summary!)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '重试读取详情' }));
    expect(await within(dialog).findByText('更长的已入库摘录')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '重试读取详情' })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { name: '已入库股评摘录' })).toBeInTheDocument();
    expect(mockEvent).toHaveBeenCalledTimes(2);
    expect(mockLoad).toHaveBeenCalledTimes(workspaceReads);
  });

  it('shows the real forum source and preserves an explicit zero without fabricating missing engagement counts', async () => {
    const item = event(2, 'stock_forum_post');
    const detail = { ...item, sourceName: '独立公开论坛', metrics: { replyCount: 0, views: null, likeCount: Number.NaN } };
    mockEvent.mockResolvedValue(detail);
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByText('独立公开论坛')).toBeInTheDocument();
    expect(within(dialog).queryByText(/东方财富/)).not.toBeInTheDocument();
    expect(within(dialog).getByText('回复')).toBeInTheDocument();
    expect(within(dialog).getByText('0')).toBeInTheDocument();
    expect(within(dialog).queryByText('浏览')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('点赞')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: '查看来源原帖' })).toHaveAttribute('href', item.url);
  });

  it('does not show a forum title as missing body text, nor accept a different event response', async () => {
    const item = { ...event(3, 'stock_forum_post'), summary: '' };
    mockEvent.mockResolvedValue({ ...item, id: 999, summary: '其他记录的内容' });
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByRole('button', { name: '重试读取详情' })).toBeInTheDocument();
    expect(within(dialog).queryByText('其他记录的内容')).not.toBeInTheDocument();
    expect(within(dialog).getByText('这条记录没有正文摘录。')).toBeInTheDocument();
    expect(within(dialog).queryByText(item.title)).not.toBeInTheDocument();
  });

  it('keeps essay summaries out of the original body during loading or failure and allows an in-place retry', async () => {
    const item = event(4);
    const pending = deferred<ResearchNoteDetail>();
    mockNote.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(note(item));
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(within(dialog).getByText('正在读取已入库原文…')).toBeInTheDocument();
    expect(within(dialog).queryByText(item.summary!)).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('heading', { name: '已入库原文' })).not.toBeInTheDocument();
    await act(async () => pending.reject(new Error('{"raw":"sensitive upstream detail"}')));
    expect(within(dialog).getByText('原文暂未读取成功，可以重试或前往来源查看。')).toBeInTheDocument();
    expect(within(dialog).queryByText(/sensitive upstream detail/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(item.summary!)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '重试读取原文' }));
    expect(await within(dialog).findByText('入库正文 4')).toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { name: '已入库原文' })).toBeInTheDocument();
    expect(mockNote).toHaveBeenNthCalledWith(1, item.externalId);
    expect(mockNote).toHaveBeenNthCalledWith(2, item.externalId);
  });

  it('rejects a mismatched topic and does not show someone else’s content as the current original', async () => {
    const item = event(5);
    mockNote.mockResolvedValue(note(item, { topicId: 'another-topic', content: '不属于该条原文' }));
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByRole('button', { name: '重试读取原文' })).toBeInTheDocument();
    expect(within(dialog).queryByText('不属于该条原文')).not.toBeInTheDocument();
  });

  it('ignores a late original response after closing it and reading a different essay', async () => {
    const first = event(6);
    const second = event(7);
    const pending = deferred<ResearchNoteDetail>();
    mockNote.mockImplementation((id: string) => id === first.externalId ? pending.promise : Promise.resolve(note(second)));
    renderItems([first, second]);
    await openOriginal(first);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const secondDialog = await openOriginal(second);
    expect(await within(secondDialog).findByText('入库正文 7')).toBeInTheDocument();
    await act(async () => pending.resolve(note(first)));
    expect(within(secondDialog).getByText('入库正文 7')).toBeInTheDocument();
    expect(within(secondDialog).queryByText('入库正文 6')).not.toBeInTheDocument();
  });

  it('ignores an old forum failure after closing and reopening the same record', async () => {
    const item = event(10, 'stock_forum_post');
    const pending = deferred<MonitorEvent>();
    mockEvent.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ ...item, summary: '第二次读取到的摘录' });
    renderItems([item]);
    await openOriginal(item);
    fireEvent.keyDown(document, { key: 'Escape' });
    const reopened = await openOriginal(item);
    expect(await within(reopened).findByText('第二次读取到的摘录')).toBeInTheDocument();
    await act(async () => pending.reject(new Error('closed request failed late')));
    expect(within(reopened).getByText('第二次读取到的摘录')).toBeInTheDocument();
    expect(within(reopened).queryByRole('button', { name: '重试读取详情' })).not.toBeInTheDocument();
  });

  it('does not direct readers to nonexistent attachments when the saved original has no body or assets', async () => {
    const item = event(11);
    mockNote.mockResolvedValue(note(item, { content: '' }));
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByText('本条未保存文字正文、图片或附件，请前往来源查看。')).toBeInTheDocument();
    expect(within(dialog).queryByText(/下方/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(item.summary!)).not.toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: '在知识星球打开' })).toHaveAttribute('href', item.url);
  });

  it('keeps named attachments visible when links are missing and uses an available download-only URL', async () => {
    const item = event(8);
    mockNote.mockResolvedValue(note(item, {
      content: '', files: [
        { fileId: 'missing', name: '华懋科技半年报附件尚无链接.pdf' },
        { fileId: 'download', name: '华懋科技业务更新与预期全文字稿.txt', downloadUrl: 'https://example.test/transcript-download' },
      ], images: [{ imageId: 'missing-image' }],
    }));
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByText('华懋科技半年报附件尚无链接.pdf')).toBeInTheDocument();
    expect(within(dialog).getByText('暂无链接')).toBeInTheDocument();
    expect(within(dialog).getByText('图片 1 · 暂无可用链接')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /华懋科技业务更新与预期全文字稿.txt.*下载/ })).toHaveAttribute('href', 'https://example.test/transcript-download');
  });

  it('shows an image-level fallback without hiding readable text or refetching the note', async () => {
    const item = event(9);
    mockNote.mockResolvedValue(note(item, { images: [{ imageId: 'image', viewUrl: 'https://example.test/image' }] }));
    renderItems([item]);
    const dialog = await openOriginal(item);
    const image = await within(dialog).findByRole('img', { name: '小作文图片 1' });
    fireEvent.error(image);
    expect(within(dialog).getByText('图片暂未载入，可打开原图查看。')).toBeInTheDocument();
    expect(within(dialog).getByText('入库正文 9')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /图片 1/ })).toHaveAttribute('href', 'https://example.test/image');
    expect(mockNote).toHaveBeenCalledTimes(1);
  });

  it.each(['essay', 'stock_forum_post'] as const)('performs one logical detail GET per attempt under StrictMode for %s', async type => {
    const item = event(20, type);
    const pending = deferred<MonitorEvent | ResearchNoteDetail>();
    const read = type === 'essay' ? mockNote : mockEvent;
    read.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(type === 'essay' ? note(item) : item);
    renderItems([item], true);
    const dialog = await openOriginal(item);
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => pending.reject(new Error('transient failure')));
    fireEvent.click(within(dialog).getByRole('button', { name: type === 'essay' ? '重试读取原文' : '重试读取详情' }));
    expect(await within(dialog).findByText(type === 'essay' ? '入库正文 20' : item.summary!)).toBeInTheDocument();
    await act(async () => { await Promise.resolve(); });
    expect(read).toHaveBeenCalledTimes(2);
  });

  it.each([
    { type: 'essay', status: 401 }, { type: 'essay', status: 403 },
    { type: 'stock_forum_post', status: 401 }, { type: 'stock_forum_post', status: 403 },
  ] as const)('hides seed metadata and content after $status and keeps them hidden during retries for $type', async ({ type, status }) => {
    const item = { ...event(21, type), metrics: { views: 18, imageUrls: ['https://example.test/private-seed.jpg'] } };
    const pendingRetry = deferred<MonitorEvent | ResearchNoteDetail>();
    const read = type === 'essay' ? mockNote : mockEvent;
    read.mockRejectedValueOnce({ response: { status } }).mockReturnValueOnce(pendingRetry.promise)
      .mockResolvedValueOnce(type === 'essay' ? note(item) : item);
    renderItems([item]);
    const dialog = await openOriginal(item);
    const deniedMessage = type === 'essay'
      ? '当前账号无法读取该原文，请重新登录或确认访问权限。'
      : '当前账号无法读取该股评，请重新登录或确认访问权限。';
    const retryLabel = type === 'essay' ? '重试读取原文' : '重试读取详情';
    expect(await within(dialog).findByText(deniedMessage)).toBeInTheDocument();
    const checkHidden = () => {
      expect(within(dialog).queryByText(item.title)).not.toBeInTheDocument();
      expect(within(dialog).queryByText(item.summary!)).not.toBeInTheDocument();
      expect(within(dialog).queryByText(item.sourceName)).not.toBeInTheDocument();
      expect(within(dialog).queryByText('原作者')).not.toBeInTheDocument();
      expect(within(dialog).queryByRole('link')).not.toBeInTheDocument();
      expect(within(dialog).queryByRole('img')).not.toBeInTheDocument();
    };
    checkHidden();
    fireEvent.click(within(dialog).getByRole('button', { name: retryLabel }));
    expect(within(dialog).getByRole('button', { name: retryLabel })).toBeDisabled();
    checkHidden();
    await act(async () => pendingRetry.reject(new Error('network failed after denial')));
    expect(within(dialog).getByText(deniedMessage)).toBeInTheDocument();
    checkHidden();
    fireEvent.click(within(dialog).getByRole('button', { name: retryLabel }));
    expect(await within(dialog).findByText(type === 'essay' ? '入库正文 21' : item.summary!)).toBeInTheDocument();
    expect(within(dialog).queryByText(deniedMessage)).not.toBeInTheDocument();
  });

  it('rejects unsafe forum source and image URLs while keeping genuine HTTPS images', async () => {
    const item = { ...event(22, 'stock_forum_post'), url: 'javascript:alert(1)', metrics: { imageUrls: [
      'javascript:alert(1)', 'data:image/svg+xml,bad', '//evil.test/image',
      'https://name:password@evil.test/image', 'https://example.test/safe.jpg',
    ] } };
    mockEvent.mockResolvedValue(item);
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByText(item.summary!)).toBeInTheDocument();
    expect(within(dialog).queryByRole('link', { name: '查看来源原帖' })).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole('img')).toHaveLength(1);
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', 'https://example.test/safe.jpg');
    expect(within(dialog).getAllByRole('link')).toHaveLength(1);
  });

  it('accepts explicit same-origin API assets but rejects unsafe, protocol-relative and userinfo attachment URLs', async () => {
    const item = { ...event(23), url: 'https://name:password@evil.test/original' };
    const downloadPath = '/api/v1/financial-data/research-notes/topic-23/media/files/file-1/download';
    const imagePath = '/api/v1/financial-data/research-notes/topic-23/media/images/image-1';
    const unsafe = [
      'javascript:alert(1)', 'data:text/html,bad', '//evil.test/file',
      'https://name:password@evil.test/file', '/api/../../access', '/api/\\evil.test/file', '/api/file\nname',
    ];
    mockNote.mockResolvedValue(note(item, {
      files: [
        ...unsafe.map((url, index) => ({ fileId: `unsafe-${index}`, name: `不安全附件 ${index}`, viewUrl: url })),
        { fileId: 'fallback', name: '可下载的文字稿.txt', viewUrl: '//evil.test/file', downloadUrl: downloadPath },
      ],
      images: [{ imageId: 'image', viewUrl: imagePath }, { imageId: 'invalid', viewUrl: 'data:image/svg+xml,bad' }],
    }));
    renderItems([item]);
    const dialog = await openOriginal(item);
    expect(await within(dialog).findByText('入库正文 23')).toBeInTheDocument();
    expect(within(dialog).queryByRole('link', { name: '在知识星球打开' })).not.toBeInTheDocument();
    expect(within(dialog).queryAllByRole('link', { name: /不安全附件/ })).toHaveLength(0);
    expect(within(dialog).getByRole('link', { name: /可下载的文字稿.txt.*下载/ })).toHaveAttribute('href', new URL(downloadPath, window.location.origin).href);
    expect(within(dialog).getByRole('img', { name: '小作文图片 1' })).toHaveAttribute('src', new URL(imagePath, window.location.origin).href);
    expect(within(dialog).getAllByRole('img')).toHaveLength(1);
    expect(within(dialog).getAllByRole('link')).toHaveLength(2);
  });
});
