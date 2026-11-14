import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchEventContent, type EventContentResult } from '../../api/eventContent';
import { homeDashboardApi } from '../../api/homeDashboard';
import { industryResearchApi } from '../../api/industryResearch';
import { useUserAccess } from '../../contexts/UserAccessContext';
import type { HomeDashboard } from '../../types/homeDashboard';
import type { MonitorEvent } from '../../types/investmentMonitor';
import WorkbenchPage from '../WorkbenchPage';

// 只桩掉网络层：页面、BottomSheet、弹层内部状态都跑真实实现。
vi.mock('../../api/homeDashboard', () => ({ homeDashboardApi: { get: vi.fn() } }));
vi.mock('../../api/industryResearch', () => ({ industryResearchApi: { projects: vi.fn() } }));
vi.mock('../../api/eventContent', () => ({ fetchEventContent: vi.fn() }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));

function event(id: number, title: string, extra: Partial<MonitorEvent> = {}): MonitorEvent {
  return {
    id,
    sourceKey: 'cls.news',
    sourceName: '财联社',
    sourceType: 'api',
    externalId: `ext-${id}`,
    eventType: 'news',
    perspective: 'investor',
    title,
    summary: null,
    url: null,
    symbols: [],
    sentiment: 'neutral',
    importanceScore: 50,
    confidenceScore: 80,
    tags: [],
    actors: [],
    metrics: {},
    eventAt: '2026-11-14T02:30:00Z',
    ...extra,
  };
}

function dashboard(news: MonitorEvent[], pinned: MonitorEvent[] = []): HomeDashboard {
  return {
    generatedAt: '2026-11-14T02:40:00', marketTime: '2026-11-14 10:40:00', tradeDate: '20261114',
    cnIndices: [{
      code: '000001.SH', name: '上证指数', region: 'A股', close: 3200, changePct: 0.5, history: [],
    }],
    globalIndices: [],
    breadth: { available: false, up: 0, down: 0, flat: 0, limitUp: 0, limitDown: 0, total: 0, distribution: [] },
    sectorDistribution: { available: false, up: 0, down: 0, flat: 0, total: 0, distribution: [], leaders: [], laggards: [] },
    northbound: {},
    watchlist: [],
    latestEvents: [],
    latestNews: news,
    pinnedNews: pinned,
    intelligenceSummary: {},
    warnings: [],
    cache: { hit: false, ttlSeconds: 60, ageSeconds: 0 },
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路由">{location.pathname}{location.search}</output>;
}

function fixture() {
  return (
    <MemoryRouter initialEntries={['/home']}>
      <WorkbenchPage />
      <LocationProbe />
    </MemoryRouter>
  );
}

/**
 * jsdom 没有 matchMedia，页面走桌面分支：最近快讯在「快讯」主 Tab 里（§10 一屏一屏拆）。
 * 手机分支不拆 Tab，这里用 role=tab 是否存在来判断，两种分支都能跑。
 */
async function renderWorkbench() {
  render(fixture());
  const newsTab = screen.queryByRole('tab', { name: /快讯/ });
  if (newsTab) {
    fireEvent.click(newsTab);
    await waitFor(() => expect(screen.getByRole('tab', { name: /快讯/ })).toHaveAttribute('aria-selected', 'true'));
  }
  await screen.findByText('最近快讯');
  return screen.getByLabelText('当前路由');
}

function mockDashboard(value: HomeDashboard) {
  vi.mocked(homeDashboardApi.get).mockResolvedValue(value);
  vi.mocked(industryResearchApi.projects).mockResolvedValue({ items: [] } as never);
}

function content(overrides: Partial<EventContentResult> = {}): EventContentResult {
  return {
    eventId: 100, status: 'ok', content: '', source: 'fetched', url: null, fetchedAt: null, ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  vi.mocked(useUserAccess).mockReturnValue({ user: { id: 1, name: '测试用户' } } as never);
  mockDashboard(dashboard([event(100, '【宁德时代发布新品】当日快讯原文')]));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('手机端「总览」最近快讯：就地弹层看正文，不跳实时流水', () => {
  it('点单条快讯不跳路由；已保存正文直接渲染，不再发请求', async () => {
    mockDashboard(dashboard([event(100, '【宁德时代发布新品】当日快讯原文', {
      summary: '<p>第一段：公司发布了新一代电池产品。</p><p>第二段：该产品预计明年量产。</p>',
      url: 'https://example.com/news/100',
    })]));
    const route = await renderWorkbench();
    expect(route).toHaveTextContent('/home');

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));

    // 关键回归：点快讯后仍停留在 /home，不再 navigate 到 /investment-monitor/feed。
    expect(route).toHaveTextContent('/home');
    expect(route).not.toHaveTextContent('/investment-monitor/feed');

    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(within(dialog).getByRole('heading', { name: '宁德时代发布新品' })).toBeInTheDocument();
    expect(within(dialog).getByText('财联社')).toBeInTheDocument();
    // 正文按段落渲染成纯文本，且 Tushare 这类有正文的事件不该再打正文接口。
    expect(within(dialog).getByText('第一段：公司发布了新一代电池产品。')).toBeInTheDocument();
    expect(within(dialog).getByText('第二段：该产品预计明年量产。')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /打开原文/ }))
      .toHaveAttribute('href', 'https://example.com/news/100');
    expect(fetchEventContent).not.toHaveBeenCalled();
  });

  it('没有已保存正文但有原文链接时，按需抓正文并渲染（加载态 → 正文）', async () => {
    mockDashboard(dashboard([event(100, '【宁德时代发布新品】当日快讯原文', {
      summary: null,
      url: 'https://example.com/news/100',
    })]));
    const pending = deferred<EventContentResult>();
    vi.mocked(fetchEventContent).mockReturnValue(pending.promise);
    const route = await renderWorkbench();

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));
    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(within(dialog).getByRole('status')).toHaveTextContent('正在抓取原文正文…');
    expect(fetchEventContent).toHaveBeenCalledWith(100);

    await act(async () => {
      pending.resolve(content({ content: '<p>抓到的原文正文第一段。</p><p>抓到的原文正文第二段。</p>' }));
      await pending.promise;
    });
    expect(await within(dialog).findByText('抓到的原文正文第一段。')).toBeInTheDocument();
    expect(within(dialog).getByText('抓到的原文正文第二段。')).toBeInTheDocument();
    expect(route).toHaveTextContent('/home');
    expect(route).not.toHaveTextContent('/investment-monitor/feed');
  });

  it('正文抓取失败时给「该来源未提供正文」+ 醒目阅读原文 + 可重试', async () => {
    mockDashboard(dashboard([event(100, '【宁德时代发布新品】当日快讯原文', {
      url: 'https://example.com/news/100',
    })]));
    vi.mocked(fetchEventContent)
      .mockResolvedValueOnce(content({ status: 'failed' }))
      .mockResolvedValueOnce(content({ content: '<p>重试后取到的原文正文。</p>' }));
    await renderWorkbench();

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));
    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(await within(dialog).findByText('该来源未提供正文。')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /阅读原文/ }))
      .toHaveAttribute('href', 'https://example.com/news/100');
    expect(within(dialog).getByText('正文在线抓取未完成。')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /重试读取/ }));
    expect(await within(dialog).findByText('重试后取到的原文正文。')).toBeInTheDocument();
    expect(fetchEventContent).toHaveBeenCalledTimes(2);
  });

  it('既没有已保存正文也没有原文链接时，按需询问后端，取不到就说明原因（不发伪造内容）', async () => {
    mockDashboard(dashboard([event(100, '宁德时代发布新品')]));
    // NewsSheet 的契约：没有已保存正文就交给后端按需回退（long_news 的 url 列可能为 NULL），
    // 因此这里断言「请求了一次、失败后给原因」，而不是「完全不请求」。
    vi.mocked(fetchEventContent).mockResolvedValue(content({ status: 'empty' }));
    await renderWorkbench();

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));
    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(await within(dialog).findByText('该来源未提供正文。')).toBeInTheDocument();
    expect(within(dialog).getByText('该来源也没有可用的原文链接。')).toBeInTheDocument();
    expect(fetchEventContent).toHaveBeenCalledWith(100);
  });

  it('「更多」打开快讯列表弹层，不跳路由；列表项可看全文并可返回列表', async () => {
    mockDashboard(dashboard([
      event(100, '【宁德时代发布新品】当日快讯原文', { summary: '<p>宁德时代正文</p>' }),
      event(101, '【半导体板块午后走强】当日快讯原文', { summary: '<p>半导体板块午后集体走强。</p>' }),
    ]));
    const route = await renderWorkbench();

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    expect(route).toHaveTextContent('/home');
    expect(route).not.toHaveTextContent('/investment-monitor/feed');

    const list = await screen.findByRole('dialog', { name: '财经快讯' });
    const rows = within(list).getAllByRole('button', { name: /查看快讯全文/ });
    expect(rows).toHaveLength(2);

    fireEvent.click(rows[1]);
    const detail = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(within(detail).getByText('半导体板块午后集体走强。')).toBeInTheDocument();
    expect(within(detail).getByRole('button', { name: /返回快讯列表/ })).toBeInTheDocument();
    expect(route).toHaveTextContent('/home');

    fireEvent.click(within(detail).getByRole('button', { name: /返回快讯列表/ }));
    expect(await screen.findByRole('dialog', { name: '财经快讯' })).toBeInTheDocument();
  });

  it('没有快讯时列表给空态，不伪造内容', async () => {
    mockDashboard(dashboard([]));
    await renderWorkbench();

    fireEvent.click(screen.getByRole('button', { name: '更多' }));
    const dialog = await screen.findByRole('dialog', { name: '财经快讯' });
    expect(within(dialog).getByText('暂无快讯')).toBeInTheDocument();
    expect(within(dialog).getByText('财经快讯同步后会自动出现在这里。')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('当前路由')).toHaveTextContent('/home'));
  });
});
