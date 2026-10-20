import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { investmentMonitorApi } from '../../api/investmentMonitor';
import type { MonitorEvent, MonitorEventList, MonitorStatus } from '../../types/investmentMonitor';
import InvestmentMonitorPage from '../InvestmentMonitorPage';

vi.mock('../../api/investmentMonitor', () => ({ investmentMonitorApi: {
  status: vi.fn(), events: vi.fn(), event: vi.fn(), startWorker: vi.fn(), stopWorker: vi.fn(), sync: vi.fn(),
} }));

const emptyEvents: MonitorEventList = { items: [], total: 0, page: 1, pageSize: 100 };
function event(id: number, title: string): MonitorEvent {
  return {
    id, title, sourceKey: 'test.news', sourceName: '本地测试来源', sourceType: 'test', externalId: String(id),
    eventType: 'news', perspective: 'institution', symbols: ['300476.SZ'], sentiment: 'neutral',
    importanceScore: 50, confidenceScore: 0.9, tags: [], actors: [], metrics: {}, summary: '确定性测试原文',
    eventAt: '2026-09-11T08:00:00Z',
  };
}
function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路由">{location.pathname}{location.search}</output>;
}
function mount(path = '/investment-monitor/feed') {
  return render(<MemoryRouter initialEntries={[path]}>
    <InvestmentMonitorPage /><LocationProbe />
    <Link to="/investment-monitor/feed?channel=news&symbol=600519.SH">另一股票新闻</Link>
  </MemoryRouter>);
}
async function flush() { await act(async () => { await Promise.resolve(); }); }

describe('首页到证据流水的筛选与原文链路', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(investmentMonitorApi.status).mockResolvedValue({
      worker: { running: false }, sources: { items: [{ sourceKey: 'test.news', name: '指定来源' }] },
    } as MonitorStatus);
    vi.mocked(investmentMonitorApi.events).mockResolvedValue(emptyEvents);
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(event(17, '指定证据原文'));
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it('首页全部新闻入口提供确定的返回首页，不跟随外部地址', async () => {
    render(<MemoryRouter initialEntries={[{ pathname: '/investment-monitor/feed', search: '?channel=news', state: { returnTo: '/app' } }]}><InvestmentMonitorPage /><LocationProbe /></MemoryRouter>);
    await flush();
    fireEvent.change(screen.getByLabelText('筛选股票'), { target: { value: '603306.SH' } }); await flush();
    expect(screen.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/app');
    fireEvent.click(screen.getByRole('link', { name: '返回首页' }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/app');
  });

  it('首次带股票和新闻参数时只读取匹配内容，且不启动监控/同步', async () => {
    mount('/investment-monitor/feed?channel=news&symbol=300476.SZ'); await flush();
    expect(screen.getByLabelText('筛选股票')).toHaveValue('300476.SZ');
    expect(investmentMonitorApi.events).toHaveBeenCalledTimes(1);
    expect(investmentMonitorApi.events).toHaveBeenLastCalledWith(expect.objectContaining({
      symbol: '300476.SZ', channel: 'news', evidenceLevel: 'factual',
    }));
    expect(investmentMonitorApi.startWorker).not.toHaveBeenCalled();
    expect(investmentMonitorApi.sync).not.toHaveBeenCalled();
  });

  it('同页再次从股票入口进入会即时更新条件，不沿用上只股票', async () => {
    mount('/investment-monitor/feed?channel=company&symbol=300476.SZ'); await flush();
    fireEvent.click(screen.getByRole('link', { name: '另一股票新闻' })); await flush();
    expect(screen.getByLabelText('筛选股票')).toHaveValue('600519.SH');
    expect(investmentMonitorApi.events).toHaveBeenLastCalledWith(expect.objectContaining({
      symbol: '600519.SH', channel: 'news',
    }));
    expect(investmentMonitorApi.events).toHaveBeenCalledTimes(2);
  });

  it('切换渠道移除来源覆盖，但保留股票并把筛选写回地址', async () => {
    mount('/investment-monitor/feed?source=test.news&symbol=300476.SZ&event=17'); await flush();
    expect(investmentMonitorApi.event).toHaveBeenCalledWith(17);
    expect(investmentMonitorApi.events).toHaveBeenLastCalledWith(expect.objectContaining({
      sourceKey: 'test.news', symbol: '300476.SZ', channel: undefined, days: 3650,
    }));
    fireEvent.click(screen.getByRole('button', { name: '财经快讯' })); await flush();
    const route = screen.getByLabelText('当前路由').textContent ?? '';
    expect(route).toContain('channel=news');
    expect(route).toContain('symbol=300476.SZ');
    expect(route).not.toContain('source=');
    expect(route).not.toContain('event=');
    expect(investmentMonitorApi.events).toHaveBeenLastCalledWith(expect.objectContaining({
      sourceKey: undefined, symbol: '300476.SZ', channel: 'news', days: 7,
    }));
  });

  it('未知渠道安全回到事实流，具体event入口仍打开对应原文', async () => {
    mount('/investment-monitor/feed?channel=invalid&event=17'); await flush();
    expect(investmentMonitorApi.events).toHaveBeenLastCalledWith(expect.objectContaining({
      channel: undefined, evidenceLevel: 'factual',
    }));
    expect(investmentMonitorApi.event).toHaveBeenCalledWith(17);
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
    expect(screen.getByText('确定性测试原文')).toBeVisible();
  });

  it('有来源链接也先页内阅读，关闭只移除event且保留股票和渠道', async () => {
    const article = { ...event(21, '有链接的研报'), eventType: 'research_report_pdf', url: 'https://example.test/report.pdf' };
    vi.mocked(investmentMonitorApi.events).mockResolvedValue({ ...emptyEvents, items: [article], total: 1 });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(article);
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    mount('/investment-monitor/feed?channel=research&symbol=300476.SZ'); await flush();
    fireEvent.click(screen.getByRole('button', { name: '查看资料：有链接的研报' })); await flush();
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByRole('link', { name: '打开原始 PDF' })).toHaveAttribute('href', article.url);
    expect(open).not.toHaveBeenCalled();
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(21);
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('event=21');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' })); await flush();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('channel=research&symbol=300476.SZ');
    expect(screen.getByLabelText('当前路由')).not.toHaveTextContent('event=');
  });

  it('条件改变后的迟到旧响应不能覆盖新股票证据', async () => {
    let resolveOld!: (value: MonitorEventList) => void;
    const old = new Promise<MonitorEventList>(resolve => { resolveOld = resolve; });
    vi.mocked(investmentMonitorApi.events).mockReturnValueOnce(old).mockResolvedValue({
      ...emptyEvents, items: [event(2, '新股票证据')], total: 1,
    });
    mount('/investment-monitor/feed?symbol=300476.SZ'); await flush();
    fireEvent.click(screen.getByRole('link', { name: '另一股票新闻' })); await flush();
    expect(screen.getByText('新股票证据')).toBeVisible();
    await act(async () => { resolveOld({ ...emptyEvents, items: [event(1, '迟到旧股票证据')], total: 1 }); });
    expect(screen.queryByText('迟到旧股票证据')).not.toBeInTheDocument();
    expect(screen.getByText('新股票证据')).toBeVisible();
  });
});
