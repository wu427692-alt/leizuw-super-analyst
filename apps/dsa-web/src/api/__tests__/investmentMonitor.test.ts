import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import apiClient from '../index';
import { investmentMonitorApi } from '../investmentMonitor';

describe('investmentMonitorApi dragon tiger endpoints', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loads an exact trade date and preserves nested seat details', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        trade_date: '20260819',
        summary: { symbol_count: 74, row_count: 78, seat_count: 748 },
        items: [{ ts_code: '601011.SH', name: '宝泰隆', seats: [{ net_buy: 1200 }] }],
      },
    });

    const result = await investmentMonitorApi.dragonTigerDaily('2026-08-19', true);

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/investment-monitor/dragon-tiger/daily', {
      params: { trade_date: '2026-08-19', refresh: true },
      timeout: 60000,
    });
    expect(result.summary.seatCount).toBe(748);
    expect(result.items[0].tsCode).toBe('601011.SH');
    expect(result.items[0].seats?.[0].netBuy).toBe(1200);
  });

  it('uses local history query separately from explicit incremental sync', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0, cached_trade_days: 5 } });
    vi.mocked(apiClient.post).mockResolvedValue({ data: { trade_days: 5, top_list_count: 374 } });

    const history = await investmentMonitorApi.dragonTigerHistory({
      startDate: '2026-08-13', endDate: '2026-08-19', symbol: '601011.SH', query: '涨幅', pageSize: 100,
    });
    const sync = await investmentMonitorApi.syncDragonTiger('2026-08-13', '2026-08-19');

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/investment-monitor/dragon-tiger/history', { params: {
      start_date: '2026-08-13', end_date: '2026-08-19', symbol: '601011.SH', query: '涨幅', page: 1, page_size: 100,
    } });
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/investment-monitor/dragon-tiger/sync', {
      start_date: '2026-08-13', end_date: '2026-08-19',
    }, { timeout: 120000 });
    expect(history.cachedTradeDays).toBe(5);
    expect(sync.topListCount).toBe(374);
  });

  it('refreshes the shared workers instead of a page-local stock adapter', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { mode: 'shared_workers' } });

    await investmentMonitorApi.refreshSuperWatchlist();

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/investment-monitor/super-watchlist/refresh',
      undefined,
      { timeout: 60000 },
    );
  });

  it('loads source inventory BI with a bounded period', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { days: 30, summary: { stored_event_count: 23945 }, sources: [] },
    });

    const result = await investmentMonitorApi.sourceBI(30);

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/investment-monitor/source-bi', { params: { days: 30 } });
    expect(result.summary.storedEventCount).toBe(23945);
  });
});
