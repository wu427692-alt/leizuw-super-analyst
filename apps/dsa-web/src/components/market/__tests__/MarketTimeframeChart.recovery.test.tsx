import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { MarketSeries } from '../../../types/marketSeries';
import { MarketTimeframeChart } from '../MarketTimeframeChart';

const { mockGetMarketSeries } = vi.hoisted(() => ({ mockGetMarketSeries: vi.fn() }));

vi.mock('../../../api/marketSeries', () => ({ getMarketSeries: mockGetMarketSeries }));
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children, data }: { children?: ReactNode; data?: unknown[] }) => <div data-testid="market-chart" data-chart-rows={JSON.stringify(data)}>{children}</div>,
  Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Area: () => null, CartesianGrid: () => null, Cell: () => null, Line: () => null,
  ReferenceLine: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

const recoveredSeries: MarketSeries = {
  stockCode: '300476.SZ', stockName: '胜宏科技', period: 'intraday', range: '1d',
  source: 'sqlite.test', storedCount: 2, latestAt: '2026-08-20T15:00:00', preClose: 100,
  refreshed: false, storage: 'sqlite',
  data: [
    { date: '2026-08-20T09:30:00', open: 100, high: 101, low: 99, close: 100, volume: 10 },
    { date: '2026-08-20T09:31:00', open: 100, high: 102, low: 100, close: 101, volume: 20 },
  ],
};

describe('MarketTimeframeChart recovery', () => {
  beforeEach(() => vi.clearAllMocks());

  it('automatically recovers from one transient request failure and clears the blocking state', async () => {
    mockGetMarketSeries.mockRejectedValueOnce(new Error('临时 SQLite 锁')).mockResolvedValue(recoveredSeries);

    render(<MarketTimeframeChart symbol="300476.SZ" initialPeriod="intraday" initialRange="1d" />);

    expect(await screen.findByText('行情连接正在恢复，页面会自动重试…')).toBeInTheDocument();
    await waitFor(() => expect(mockGetMarketSeries).toHaveBeenCalledTimes(2), { timeout: 3_000 });
    expect(await screen.findByText(/2 个分钟点/)).toBeInTheDocument();
    expect(screen.queryByText('行情连接正在恢复，页面会自动重试…')).not.toBeInTheDocument();
  });

  it('does not call an old timestamp with seconds realtime or show metadata newer than its bars', async () => {
    mockGetMarketSeries.mockResolvedValue(recoveredSeries);
    render(<MarketTimeframeChart symbol="300476.SZ" initialPeriod="intraday" initialRange="1d" />);
    expect(await screen.findByText('2026-08-20 09:31:00')).toBeInTheDocument();
    expect(screen.queryByText(/当前分钟实时/)).not.toBeInTheDocument();
    expect(screen.queryByText('MA5')).not.toBeInTheDocument();
  });

  it('does not reinterpret intraday data as weekly bars while the request is pending', async () => {
    let resolveWeekly!: (value: MarketSeries) => void;
    mockGetMarketSeries.mockResolvedValueOnce(recoveredSeries).mockImplementationOnce(() => new Promise<MarketSeries>(resolve => { resolveWeekly = resolve; }));
    render(<MarketTimeframeChart symbol="300476.SZ" initialPeriod="intraday" initialRange="1d" />);
    await screen.findByText('2026-08-20 09:31:00');
    fireEvent.click(screen.getByRole('button', { name: '周K' }));
    expect(screen.queryByText('2026-08-20 09:31:00')).not.toBeInTheDocument();
    expect(screen.getByText('正在读取本地行情数据库…')).toBeInTheDocument();
    expect(screen.getByText('上期收盘')).toBeInTheDocument();
    await act(async () => resolveWeekly({ ...recoveredSeries, period: 'weekly', range: '2y', data: [{ date: '2026-08-14', close: 91 }, { date: '2026-08-21', close: 102 }] }));
    expect(await screen.findByText('2026-08-21')).toBeInTheDocument();
    expect(screen.getByText('102.00')).toBeInTheDocument();
  });

  it('preserves the selected period, range and loaded bars without refetching when compact changes', async () => {
    const selectedSeries: MarketSeries = {
      ...recoveredSeries, period: 'weekly', range: '5y', storedCount: 3,
      data: [
        { date: '2021-08-20', close: 77, volume: 10 },
        { date: '2026-08-14', close: 120, volume: 20 },
        { date: '2026-08-21', close: 142.75, volume: 30 },
      ],
    };
    mockGetMarketSeries.mockResolvedValue(selectedSeries)
      .mockResolvedValueOnce(recoveredSeries)
      .mockResolvedValueOnce({
        ...recoveredSeries, period: 'weekly', range: '2y',
        data: [{ date: '2026-08-14', close: 91 }, { date: '2026-08-21', close: 102 }],
      });
    const { rerender } = render(<MarketTimeframeChart symbol="300476.SZ" compact initialPeriod="intraday" initialRange="1d" />);
    expect(await within(screen.getByRole('group', { name: '最新' })).findByText('101.00')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '周K' }));
    expect(await within(screen.getByRole('group', { name: '最新' })).findByText('102.00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '5年' }));
    expect(await within(screen.getByRole('group', { name: '最新' })).findByText('142.75')).toBeInTheDocument();
    expect(mockGetMarketSeries.mock.calls).toEqual([
      ['300476.SZ', 'intraday', '1d', false, 'stock'],
      ['300476.SZ', 'weekly', '2y', false, 'stock'],
      ['300476.SZ', 'weekly', '5y', false, 'stock'],
    ]);
    const savedCharts = screen.getAllByTestId('market-chart');
    const savedRows = savedCharts.map(chart => chart.getAttribute('data-chart-rows'));
    expect(savedCharts).toHaveLength(2);
    expect(JSON.parse(savedRows[0] ?? '[]')).toMatchObject(selectedSeries.data);

    for (const compact of [false, true]) {
      await act(async () => {
        rerender(<MarketTimeframeChart symbol="300476.SZ" compact={compact} initialPeriod="intraday" initialRange="1d" />);
      });
      expect(screen.getByRole('button', { name: '周K' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByRole('button', { name: '5年' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByRole('button', { name: '分时' })).toHaveAttribute('aria-pressed', 'false');
      expect(screen.getByRole('button', { name: '2年' })).toHaveAttribute('aria-pressed', 'false');
      expect(within(screen.getByRole('group', { name: '最新' })).getByText('142.75')).toBeInTheDocument();
      expect(screen.getByText('2026-08-21')).toBeInTheDocument();
      expect(screen.getByText(/3 个数据点/)).toBeInTheDocument();
      expect(screen.queryByText('正在读取本地行情数据库…')).not.toBeInTheDocument();
      screen.getAllByTestId('market-chart').forEach((chart, index) => {
        expect(chart).toBe(savedCharts[index]);
        expect(chart).toHaveAttribute('data-chart-rows', savedRows[index]);
      });
      expect(mockGetMarketSeries).toHaveBeenCalledTimes(3);
    }
  });

  it('does not retain another stock during an identity change, even when its response arrives late', async () => {
    let resolveFirst!: (value: MarketSeries) => void;
    mockGetMarketSeries.mockImplementationOnce(() => new Promise<MarketSeries>(resolve => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ ...recoveredSeries, stockCode: '603306.SH', data: [{ date: '2026-08-20T09:32:00', close: 77 }] });
    const { rerender } = render(<MarketTimeframeChart symbol="300476.SZ" initialPeriod="intraday" initialRange="1d" />);
    rerender(<MarketTimeframeChart symbol="603306.SH" initialPeriod="intraday" initialRange="1d" />);
    expect(await within(screen.getByRole('group', { name: '最新' })).findByText('77.00')).toBeInTheDocument();
    await act(async () => resolveFirst(recoveredSeries));
    expect(screen.queryByText('101.00')).not.toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: '最新' })).getByText('77.00')).toBeInTheDocument();
  });

  it('rejects wrong-exchange index responses instead of showing another asset', async () => {
    mockGetMarketSeries.mockResolvedValue({ ...recoveredSeries, stockCode: '000001.SZ' });
    render(<MarketTimeframeChart symbol="000001.SH" assetType="index" initialPeriod="intraday" initialRange="1d" />);
    expect(await screen.findByText('行情连接正在恢复，页面会自动重试…')).toBeInTheDocument();
    expect(screen.queryByText('101.00')).not.toBeInTheDocument();
  });
});
