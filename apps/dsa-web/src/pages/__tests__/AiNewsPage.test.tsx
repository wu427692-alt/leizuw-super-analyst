import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AiNewsPage from '../AiNewsPage';
import type { AiNewsFeed } from '../AiNewsPage';

const feed: AiNewsFeed = {
  items: [{
    id: 'test-headline', title: 'Example original English headline', url: 'https://example.org/news/1',
    source_id: 'example', source_name: 'Example RSS', publisher: 'Example RSS', published_at: null,
    discovered_at: '2026-09-30T08:00:00Z', topic: 'models', topic_label: '模型', related: [], source_count: 1, score: 1,
  }],
  total: 25, page: 1, page_size: 24, pages: 2, updated_at: '2026-09-30T08:00:00Z', refreshing: false, stale: false,
  sources: [{ id: 'example', name: 'Example RSS', url: 'https://example.org/feed', kind: 'rss', last_success: '2026-09-30T08:00:00Z', last_attempt: '2026-09-30T08:00:00Z', status: 'ok', error: null, item_count: 25 }],
  topics: [{ id: 'models', label: '模型', count: 25 }],
  stats: { items: 25, sources: 1, healthy_sources: 1, multi_source_events: 0 }, methodology: 'Test source methodology.',
};

const fetchMock = vi.fn();
const respond = (data: AiNewsFeed = feed) => ({ ok: true, status: 200, json: async () => data });
const tick = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

beforeEach(() => {
  window.history.replaceState({}, '', '/ai-news');
  localStorage.removeItem('leziwu-ai-news-theme');
  fetchMock.mockReset().mockResolvedValue(respond());
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('AiNewsPage', () => {
  it('uses a same-origin cache read, preserves original titles and distinguishes discovery time', async () => {
    render(<AiNewsPage />);
    const headline = await screen.findByRole('link', { name: 'Example original English headline' });
    expect(headline).toHaveAttribute('target', '_blank');
    expect(headline).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText(/收录于/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/v1\/ai-news\/feed\?/), expect.objectContaining({ credentials: 'same-origin', signal: expect.any(AbortSignal) }));
    expect(new URL(fetchMock.mock.calls[0][0], 'https://local.invalid').searchParams.get('window')).toBe('7d');
  });

  it('submits search explicitly, then resets page when a filter changes', async () => {
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Open model' } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toContain('q=Open+model');
    fireEvent.change(screen.getByLabelText('主题'), { target: { value: 'models' } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[2][0]).toContain('topic=models');
    expect(fetchMock.mock.calls[2][0]).toContain('page=1');
  });

  it('paginates through server pages and keeps real site navigation', async () => {
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toContain('page=2');
    expect(screen.getByRole('heading', { name: '新闻时间线' })).toBeInTheDocument();
  });

  it('retains last successful results after background failure and offers inline retry', async () => {
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    fetchMock.mockRejectedValueOnce(new Error('连接中断'));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('保留上次成功读取的结果');
    expect(screen.getByRole('link', { name: 'Example original English headline' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('displays source failures and stale data without hiding the usable feed', async () => {
    fetchMock.mockResolvedValue(respond({ ...feed, stale: true, sources: [{ ...feed.sources[0], status: 'error', error: 'RSS timeout' }] }));
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    expect(screen.getByText(/缓存可能已过期/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看来源状态' }));
    expect(screen.getByRole('heading', { name: '来源目录' })).toBeInTheDocument();
    expect(screen.getByText('RSS timeout')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Example RSS' })).toHaveAttribute('href', 'https://example.org/feed');
  });

  it('polls only while visible and refreshes on returning to the tab', async () => {
    vi.useFakeTimers();
    render(<AiNewsPage />);
    await tick();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('shows first-refresh state and never fabricates articles or statistics', async () => {
    fetchMock.mockResolvedValue(respond({ ...feed, items: [], total: 0, pages: 1, refreshing: true, updated_at: null, sources: [], topics: [], stats: { items: 0, sources: 0, healthy_sources: 0, multi_source_events: 0 } }));
    render(<AiNewsPage />);
    expect(await screen.findByRole('heading', { name: '正在准备首批新闻' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Example original English headline' })).not.toBeInTheDocument();
    expect(screen.getByText('等待首轮更新')).toBeInTheDocument();
  });

  it('rejects unsafe feed links and inherits the workspace theme without a parallel toggle', async () => {
    fetchMock.mockResolvedValue(respond({ ...feed, items: [{ ...feed.items[0], url: 'javascript:alert(1)' }] }));
    const { container } = render(<AiNewsPage />);
    await screen.findByText('Example original English headline');
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(container.querySelector('.ai-news')).not.toHaveAttribute('data-ai-theme');
    expect(screen.queryByRole('button', { name: '切换深色模式' })).not.toBeInTheDocument();
  });

  it('marks successful but old sources as stale and states the source count window', async () => {
    fetchMock.mockResolvedValue(respond({ ...feed, sources: [{ ...feed.sources[0], last_success: '2000-01-01T00:00:00Z', kind: 'official' }] }));
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    fireEvent.click(screen.getByRole('button', { name: '来源' }));
    expect(screen.getByText('陈旧')).toBeInTheDocument();
    expect(screen.getByText('官方动态')).toBeInTheDocument();
    expect(screen.getByText('当前窗口条目')).toBeInTheDocument();
    expect(screen.getByText(/统计范围：近 7 天/)).toBeInTheDocument();
  });

  it('aborts a hung read after 15 seconds and presents an inline timeout', async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    fetchMock.mockImplementation((_url: string, options: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
      requestSignal = options.signal;
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    render(<AiNewsPage />);
    await act(async () => { vi.advanceTimersByTime(15000); });
    expect(requestSignal?.aborted).toBe(true);
    expect(screen.getByRole('alert')).toHaveTextContent('请求超时');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('ages retained source records across the stale threshold when later reads fail', async () => {
    window.history.replaceState({}, '', '/ai-news?view=sources');
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-30T12:00:00Z'));
    fetchMock.mockResolvedValueOnce(respond({ ...feed, sources: [{ ...feed.sources[0], last_success: '2026-09-30T09:00:30Z' }] }));
    fetchMock.mockRejectedValue(new Error('Network unavailable'));
    render(<AiNewsPage />);
    await tick();
    expect(screen.getByText('正常')).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(screen.getByText('陈旧')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Example RSS' })).toBeInTheDocument();
  });

  it('defaults to latest and groups and timestamps in Beijing time', async () => {
    fetchMock.mockResolvedValue(respond({ ...feed, items: [{ ...feed.items[0], published_at: '2026-09-29T18:30:00Z', summary: 'Source supplied brief.' }] }));
    render(<AiNewsPage />);
    await screen.findByRole('link', { name: 'Example original English headline' });
    expect(fetchMock.mock.calls[0][0]).toContain('view=latest');
    expect(screen.getByText('2026年9月30日')).toBeInTheDocument();
    expect(screen.getByText('02:30')).toBeInTheDocument();
    expect(screen.getByText('Source supplied brief.')).toBeInTheDocument();
    expect(screen.getByText('来源摘要')).toBeInTheDocument();
  });

  it('does not interrupt reading on automatic new items and accepts them explicitly', async () => {
    vi.useFakeTimers();
    render(<AiNewsPage />); await tick();
    fetchMock.mockResolvedValue(respond({ ...feed, items: [{ ...feed.items[0], id: 'new', title: 'A new source article' }, ...feed.items], stats: { ...feed.stats, items: 26 } }));
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(screen.queryByRole('link', { name: 'A new source article' })).not.toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '1 条新动态，点击查看' }));
    expect(screen.getByRole('link', { name: 'A new source article' })).toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledOnce();
  });

  it('ignores natural score decay instead of announcing fake new content', async () => {
    window.history.replaceState({}, '', '/ai-news?view=hot');
    vi.useFakeTimers(); render(<AiNewsPage />); await tick();
    fetchMock.mockResolvedValue(respond({ ...feed, items: [{ ...feed.items[0], score: .75 }], last_checked_at: '2026-09-30T09:00:00Z' }));
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(screen.queryByRole('button', { name: /点击查看/ })).not.toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it('automatically fills an initially empty cache without waiting for a new-item click', async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValueOnce(respond({ ...feed, items: [], total: 0, refreshing: true }));
    render(<AiNewsPage />); await tick();
    expect(screen.getByRole('heading', { name: '正在准备首批新闻' })).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(screen.getByRole('link', { name: 'Example original English headline' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /点击查看/ })).not.toBeInTheDocument();
  });

  it('uses each source cadence for stale status instead of the old fixed three hours', async () => {
    window.history.replaceState({}, '', '/ai-news?view=sources');
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-30T12:00:00Z'));
    fetchMock.mockResolvedValue(respond({ ...feed, sources: [{ ...feed.sources[0], last_success: '2026-09-30T11:44:00Z', interval_seconds: 300 }] }));
    render(<AiNewsPage />); await tick();
    expect(screen.getByText('陈旧')).toBeInTheDocument();
    expect(screen.getByText('5 分钟')).toBeInTheDocument();
  });
});
