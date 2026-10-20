import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { StockThemeLens, ThemeDetail } from '../../api/conceptThemes';
import { themes, stock, detail, lens, overview } from './fixtures/conceptThemes';
import ConceptThemesPage from '../ConceptThemesPage';

const mocks = vi.hoisted(() => ({
  overview: vi.fn(), theme: vi.fn(), stock: vi.fn(), leaders: vi.fn(), watchlistMap: vi.fn(),
  rotation: vi.fn(), institutionRadar: vi.fn(), lifecycle: vi.fn(), membershipChanges: vi.fn(), cluster: vi.fn(),
  exportTheme: vi.fn(), exportStock: vi.fn(), exportWatchlistMap: vi.fn(),
  refresh: null as null | (() => Promise<unknown>),
}));
vi.mock('../../api/conceptThemes', () => ({ conceptThemesApi: mocks }));
vi.mock('../../components/common', () => ({ AppPage: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
vi.mock('../../hooks/usePageActivationRefresh', () => ({ usePageActivationRefresh: (callback: typeof mocks.refresh) => { mocks.refresh = callback; } }));
vi.mock('recharts', async importOriginal => ({ ...await importOriginal<object>(), ResponsiveContainer: () => null }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function chooseTheme(name: string) {
  fireEvent.click(screen.getByRole('heading', { name, level: 3 }).closest('button')!);
}
function chooseStock(index: number) {
  const table = document.querySelector('.concept-stock-table')! as HTMLElement;
  fireEvent.click(within(table).getByText(`测试公司${index}`).closest('button')!);
}
async function ready() {
  await waitFor(() => expect(within(screen.getByRole('complementary', { name: '题材详情' })).getByRole('heading', { name: themes[0].name })).toBeInTheDocument());
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/concept-themes?section=catalog');
  window.scrollTo = vi.fn();
  mocks.overview.mockImplementation(async () => overview());
  mocks.theme.mockImplementation(async (id: number, _refresh: boolean, horizon: number) => detail(id, horizon));
  mocks.stock.mockImplementation(async (code: string, _refresh: boolean, horizon: number) => lens(code, horizon));
  mocks.exportStock.mockResolvedValue(undefined);
  mocks.exportTheme.mockResolvedValue(undefined);
});

describe('ConceptThemesPage request ownership and recovery', () => {
  it('shows actual background fill intent without treating missing members as a search miss', async () => {
    mocks.theme.mockResolvedValueOnce({ ...detail(), stocks: [], totalStocks: 0, attributionReady: 0, watchlistStocks: [], refreshNeeded: true, refreshQueued: true, refreshRequestStatus: 'queued' });
    render(<ConceptThemesPage />); await ready();
    const status = screen.getByRole('status', { name: '后台补数状态' });
    expect(status).toHaveTextContent('已加入后台补数队列');
    expect(screen.getByText('本地成分尚未齐备，后台补数后自动显示。')).toBeInTheDocument();
    expect(screen.queryByText('当前条件没有命中成分股，可清除关键词后重试。')).not.toBeInTheDocument();
  });

  it('does not claim no market evidence when independent market panels fail', async () => {
    window.history.replaceState({}, '', '/concept-themes?section=market');
    for (const method of [mocks.rotation, mocks.lifecycle, mocks.institutionRadar, mocks.membershipChanges]) method.mockRejectedValue(new Error('temporary unavailable'));
    render(<ConceptThemesPage />);
    for (const label of ['题材轮动', '题材阶段', '机构语料', '成分变化']) await screen.findByRole('button', { name: `重试${label}` });
    expect(screen.queryByText(/市场题材与机构语料暂未形成可核验交集/)).not.toBeInTheDocument();
    expect(screen.queryByText(/暂无可确认的后续成分变化/)).not.toBeInTheDocument();
    expect(screen.queryByText(/没有符合条件的机构语料候选/)).not.toBeInTheDocument();
  });

  it('does not turn a failed directory into empty search results or aligned data', async () => {
    mocks.overview.mockRejectedValueOnce(new Error('offline'));
    render(<ConceptThemesPage />);
    await screen.findByRole('button', { name: '重试题材目录' });
    expect(screen.queryByText('没有命中题材')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试题材目录' }));
    await ready();
    expect(screen.queryByRole('button', { name: '重试题材目录' })).not.toBeInTheDocument();
  });

  it('ignores a slow old theme after a different theme has been selected', async () => {
    const first = deferred<ThemeDetail>();
    mocks.theme.mockImplementation((id: number, _refresh: boolean, horizon: number) => id === 1 ? first.promise : Promise.resolve(detail(id, horizon)));
    render(<ConceptThemesPage />);
    await waitFor(() => expect(mocks.theme).toHaveBeenCalledWith(1, true, 60));
    chooseTheme(themes[1].name);
    const panel = screen.getByRole('complementary', { name: '题材详情' });
    await within(panel).findByRole('heading', { name: themes[1].name });
    await act(async () => first.resolve(detail(1)));
    expect(within(panel).queryByRole('heading', { name: themes[0].name })).not.toBeInTheDocument();
  });

  it('keeps stock waiting separate, ignores stale selections, and does not reopen a closed request', async () => {
    const a = deferred<StockThemeLens>(), b = deferred<StockThemeLens>(), closed = deferred<StockThemeLens>();
    mocks.stock.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise).mockImplementationOnce(() => closed.promise);
    render(<ConceptThemesPage />); await ready();
    chooseStock(1);
    await screen.findByRole('status', { name: '股票画像读取状态' });
    expect(screen.queryByRole('status', { name: '题材详情读取状态' })).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.stock).toHaveBeenCalledTimes(1));
    chooseStock(2);
    await waitFor(() => expect(mocks.stock).toHaveBeenCalledTimes(2));
    await act(async () => b.resolve(lens(stock(2).tsCode)));
    await act(async () => a.resolve(lens(stock(1).tsCode)));
    const panel = screen.getByRole('complementary', { name: '股票题材透镜' });
    expect(within(panel).getByRole('heading', { name: `画像${stock(2).tsCode}` })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: '关闭股票题材透镜' }));
    chooseStock(3);
    await waitFor(() => expect(mocks.stock).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole('button', { name: '关闭股票题材透镜' }));
    await act(async () => closed.resolve(lens(stock(3).tsCode)));
    expect(screen.queryByRole('complementary', { name: '股票题材透镜' })).not.toBeInTheDocument();
  });

  it('refetches both selected comparisons for a new window and hides mixed-window results', async () => {
    render(<ConceptThemesPage />); await ready();
    fireEvent.click(screen.getByRole('button', { name: '加入对照' }));
    chooseTheme(themes[1].name);
    await waitFor(() => expect(within(screen.getByRole('complementary', { name: '题材详情' })).getByRole('heading', { name: themes[1].name })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '加入对照' }));
    await screen.findByRole('button', { name: '导出对照 CSV' });
    const a = deferred<ThemeDetail>(), b = deferred<ThemeDetail>();
    mocks.theme.mockImplementation((id: number, _r: boolean, horizon: number) => horizon === 20 ? (id === 1 ? a.promise : b.promise) : Promise.resolve(detail(id, horizon)));
    fireEvent.click(screen.getByRole('button', { name: '20日' }));
    expect(screen.queryByRole('button', { name: '导出对照 CSV' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: `移除 ${themes[0].name}` })).toBeInTheDocument();
    await waitFor(() => expect(mocks.theme).toHaveBeenCalledWith(1, true, 20));
    await act(async () => b.resolve(detail(2, 20)));
    expect(screen.queryByRole('button', { name: '导出对照 CSV' })).not.toBeInTheDocument();
    await act(async () => a.resolve(detail(1, 20)));
    await screen.findByRole('button', { name: '导出对照 CSV' });
    expect(screen.queryByRole('status', { name: '题材对照读取状态' })).not.toBeInTheDocument();
  });

  it('returns to the first member page when filtering to watchlist stocks', async () => {
    render(<ConceptThemesPage />); await ready();
    const pages = screen.getByRole('navigation', { name: '成分股分页' });
    fireEvent.click(within(pages).getByRole('button', { name: '下一页' }));
    expect(document.querySelector('.concept-stock-table')).not.toHaveTextContent('测试公司1');
    fireEvent.click(screen.getByRole('button', { name: '我的自选 1' }));
    expect(document.querySelector('.concept-stock-table')).toHaveTextContent('测试公司1');
    expect(document.querySelectorAll('.concept-stock-row')).toHaveLength(1);
  });

  it('refreshes the selected detail on activation, retains it on failure, and supports retry', async () => {
    render(<ConceptThemesPage />); await ready();
    mocks.theme.mockRejectedValueOnce(new Error('temporary timeout'));
    await act(async () => { await mocks.refresh!(); });
    const panel = screen.getByRole('complementary', { name: '题材详情' });
    expect(within(panel).getByRole('heading', { name: themes[0].name })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: '重试题材详情' }));
    await waitFor(() => expect(within(panel).queryByRole('button', { name: '重试题材详情' })).not.toBeInTheDocument());
    expect(mocks.theme).toHaveBeenCalledTimes(3);
  });

  it('rejects a stock payload for the wrong symbol instead of displaying it', async () => {
    mocks.stock.mockResolvedValueOnce(lens('WRONG'));
    render(<ConceptThemesPage />); await ready(); chooseStock(1);
    await screen.findByRole('button', { name: '重试股票画像' });
    expect(screen.queryByText('画像WRONG')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试股票画像' }));
    await screen.findByRole('heading', { name: `画像${stock(1).tsCode}` });
  });

  it('accepts a canonical theme whose id differs when the requested source node proves the alias', async () => {
    const aliasDetail: ThemeDetail = {
      ...detail(1),
      theme: {
        ...themes[0],
        id: 99,
        name: '规范光通信',
        canonicalName: '规范光通信',
        family: '别名归并产业',
      },
      sourceNodes: [{ ...themes[0], canonicalName: '规范光通信' }],
    };
    mocks.theme.mockResolvedValueOnce(aliasDetail);

    render(<ConceptThemesPage />);

    const panel = screen.getByRole('complementary', { name: '题材详情' });
    expect(await within(panel).findByRole('heading', { name: '规范光通信' })).toBeInTheDocument();
    expect(within(panel).getByText('别名归并产业')).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: '重试题材详情' })).not.toBeInTheDocument();
  });

  it('rejects a stock payload for the wrong attribution window and retries the requested window', async () => {
    const code = stock(1).tsCode;
    mocks.stock.mockResolvedValueOnce({ ...lens(code, 20), name: '20日旧画像' });

    render(<ConceptThemesPage />); await ready(); chooseStock(1);

    await screen.findByRole('button', { name: '重试股票画像' });
    expect(screen.queryByRole('heading', { name: '20日旧画像' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试股票画像' }));
    await screen.findByRole('heading', { name: `画像${code}` });
    expect(mocks.stock).toHaveBeenLastCalledWith(code, true, 60);
    expect(mocks.stock).toHaveBeenCalledTimes(2);
  });

  it('clears directory filters before drilling into a related canonical theme', async () => {
    type DirectoryParams = {
      query?: string;
      themeType?: string;
      source?: string;
      family?: string;
      cluster?: string;
      minSources?: number;
      readiness?: string;
      view?: string;
    };
    const base = overview();
    const directory = {
      ...base,
      summary: {
        ...base.summary,
        families: { '测试产业': 2 },
        clusterFamilies: { '测试产业': { '测试光通信': 1, '测试半导体': 1 } },
      },
    };
    mocks.overview.mockImplementation(async (params: DirectoryParams = {}) => {
      if (params.query !== themes[1].canonicalName) return directory;
      const filtersAreClear = !params.themeType && !params.source && !params.family && !params.cluster
        && params.minSources === 1 && params.readiness === 'all' && params.view === 'canonical';
      return { ...directory, items: filtersAreClear ? [themes[1]] : [], total: filtersAreClear ? 1 : 0 };
    });
    mocks.theme.mockImplementation(async (id: number, _refresh: boolean, horizon: number) => {
      const value = detail(id, horizon);
      if (id !== themes[0].id) return value;
      return {
        ...value,
        relatedThemes: {
          targetTotalStocks: value.totalStocks,
          method: '测试题材关联',
          items: [{
            canonicalName: themes[1].canonicalName,
            family: themes[1].family,
            cluster: themes[1].cluster,
            relationType: '同主题簇',
            sharedStocks: 8,
            targetCoveragePct: 26.7,
            jaccardPct: 18.2,
            targetExclusiveStocks: 22,
            otherTotalStocks: 30,
          }],
        },
      };
    });

    render(<ConceptThemesPage />); await ready();
    fireEvent.change(screen.getByLabelText('题材类型'), { target: { value: 'industry' } });
    fireEvent.change(screen.getByLabelText('数据来源'), { target: { value: 'ths' } });
    fireEvent.change(screen.getByLabelText('共识门槛'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('数据成熟度'), { target: { value: 'researchable' } });
    fireEvent.change(screen.getByLabelText('题材展示口径'), { target: { value: 'source' } });
    const familyRail = document.querySelector('.concept-families')! as HTMLElement;
    fireEvent.click(await within(familyRail).findByRole('button', { name: /\u6d4b\u8bd5\u4ea7\u4e1a/ }));
    await waitFor(() => expect(document.querySelector('.concept-clusters')).toBeInTheDocument());
    const clusterBar = document.querySelector('.concept-clusters')! as HTMLElement;
    fireEvent.click(within(clusterBar).getByRole('button', { name: /\u6d4b\u8bd5\u5149\u901a\u4fe1/ }));

    const detailPanel = screen.getByRole('complementary', { name: '题材详情' });
    fireEvent.click(await within(detailPanel).findByRole('button', { name: /\u6d4b\u8bd5\u534a\u5bfc\u4f53.*\u540c\u4e3b\u9898\u7c07/ }));

    expect(screen.getByLabelText('题材类型')).toHaveValue('');
    expect(screen.getByLabelText('数据来源')).toHaveValue('');
    expect(screen.getByLabelText('共识门槛')).toHaveValue('1');
    expect(screen.getByLabelText('数据成熟度')).toHaveValue('all');
    expect(screen.getByLabelText('题材展示口径')).toHaveValue('canonical');
    await waitFor(() => expect(mocks.overview).toHaveBeenLastCalledWith(expect.objectContaining({
      query: themes[1].canonicalName,
      themeType: '',
      source: '',
      family: '',
      cluster: '',
      minSources: 1,
      readiness: 'all',
      view: 'canonical',
      page: 1,
    })));
    expect(await within(detailPanel).findByRole('heading', { name: themes[1].canonicalName })).toBeInTheDocument();
  });

  it('handles export failure inline and sends the displayed stock/window when retried', async () => {
    mocks.exportStock.mockRejectedValueOnce(new Error('download failed'));
    render(<ConceptThemesPage />); await ready(); chooseStock(1);
    fireEvent.click(await screen.findByRole('button', { name: '导出画像' }));
    await screen.findByText('下载未完成，请检查连接后重新点击导出。');
    fireEvent.click(screen.getByRole('button', { name: '导出画像' }));
    await screen.findByText('文件已交给浏览器下载');
    expect(mocks.exportStock).toHaveBeenLastCalledWith(stock(1).tsCode, 60);
    expect(mocks.exportStock).toHaveBeenCalledTimes(2);
  });
});
