import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preloadRouteData, resetRouteDataPreloadForTests } from '../routeDataPreload';
import { investmentMonitorApi } from '../../api/investmentMonitor';
import { systemConfigApi } from '../../api/systemConfig';
import { getMarketSeries } from '../../api/marketSeries';
import { essayRadarApi } from '../../api/essayRadar';
import { industryResearchApi } from '../../api/industryResearch';
import { essayQuantApi } from '../../api/essayQuant';
import { dataAcquisitionApi } from '../../api/dataAcquisition';
import { clearRequestCache } from '../../api/requestCache';

vi.mock('../../api/investmentMonitor', () => ({ investmentMonitorApi: { stockWorkspace: vi.fn() } }));
vi.mock('../../api/systemConfig', () => ({ systemConfigApi: { getWatchlist: vi.fn() } }));
vi.mock('../../api/marketSeries', () => ({ getMarketSeries: vi.fn() }));
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: { status: vi.fn(), dashboard: vi.fn(), audioAnalysisTasks: vi.fn(), audioBatchTasks: vi.fn() } }));
vi.mock('../../api/industryResearch', () => ({ industryResearchApi: { projects: vi.fn() } }));
vi.mock('../../api/essayQuant', () => ({ essayQuantApi: { tasks: vi.fn() } }));
vi.mock('../../api/dataAcquisition', () => ({ dataAcquisitionApi: { tasks: vi.fn() } }));

describe('bounded route data preload', () => {
  beforeEach(() => {
    vi.clearAllMocks(); clearRequestCache(); resetRouteDataPreloadForTests();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.stubGlobal('navigator', { connection: { effectiveType: '4g', saveData: false } });
    vi.mocked(systemConfigApi.getWatchlist).mockResolvedValue(['603306.SH', '300308.SZ']);
    vi.mocked(investmentMonitorApi.stockWorkspace).mockResolvedValue({} as never);
    vi.mocked(getMarketSeries).mockResolvedValue({} as never);
    vi.mocked(essayRadarApi.status).mockResolvedValue({} as never);
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('warms only the first stock overview, never starts chart refill or heavy full detail', async () => {
    await preloadRouteData('/super-watchlist');
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenCalledExactlyOnceWith('603306.SH', 183, false, 'overview');
    expect(getMarketSeries).not.toHaveBeenCalled();
  });

  it('coalesces hover and focus warmup without broadening its request scope', async () => {
    await Promise.all([preloadRouteData('/super-watchlist'), preloadRouteData('/super-watchlist')]);
    expect(systemConfigApi.getWatchlist).toHaveBeenCalledTimes(1);
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenCalledTimes(1);
  });

  it('keeps one pending preload even when a slow request exceeds the hover reuse window', async () => {
    vi.useFakeTimers();
    let resolve!: (symbols: string[]) => void;
    vi.mocked(systemConfigApi.getWatchlist).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const first = preloadRouteData('/super-watchlist');
    await vi.dynamicImportSettled();
    await vi.advanceTimersByTimeAsync(16_000);
    const repeated = preloadRouteData('/super-watchlist');
    expect(repeated).toBe(first);
    resolve(['603306.SH']);
    await repeated;
    expect(systemConfigApi.getWatchlist).toHaveBeenCalledTimes(1);
  });

  it('retires an old preload before delayed code loading can start private API reads', async () => {
    const pending = preloadRouteData('/super-watchlist');
    clearRequestCache();
    await pending;
    expect(systemConfigApi.getWatchlist).not.toHaveBeenCalled();
    expect(investmentMonitorApi.stockWorkspace).not.toHaveBeenCalled();
  });

  it('never follows the previous account watchlist after login clears the request cache', async () => {
    let resolve!: (symbols: string[]) => void;
    vi.mocked(systemConfigApi.getWatchlist).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const previous = preloadRouteData('/super-watchlist');
    await vi.dynamicImportSettled();
    expect(systemConfigApi.getWatchlist).toHaveBeenCalledTimes(1);
    clearRequestCache();
    vi.mocked(systemConfigApi.getWatchlist).mockResolvedValueOnce(['300308.SZ']);
    await preloadRouteData('/super-watchlist');
    resolve(['603306.SH']);
    await previous;
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenCalledExactlyOnceWith('300308.SZ', 183, false, 'overview');
  });

  it('allows the new account to warm immediately after a completed previous account preload', async () => {
    await preloadRouteData('/super-watchlist');
    clearRequestCache();
    vi.mocked(systemConfigApi.getWatchlist).mockResolvedValueOnce(['300308.SZ']);
    await preloadRouteData('/super-watchlist');
    expect(systemConfigApi.getWatchlist).toHaveBeenCalledTimes(2);
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenLastCalledWith('300308.SZ', 183, false, 'overview');
  });

  it('does not fabricate a default stock when the user watchlist is empty', async () => {
    vi.mocked(systemConfigApi.getWatchlist).mockResolvedValue([]);
    await preloadRouteData('/super-watchlist');
    expect(investmentMonitorApi.stockWorkspace).not.toHaveBeenCalled();
    expect(getMarketSeries).not.toHaveBeenCalled();
  });

  it('does not start hidden tab warmup', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await preloadRouteData('/super-watchlist');
    expect(systemConfigApi.getWatchlist).not.toHaveBeenCalled();
  });

  it.each([
    { effectiveType: '4g', saveData: true },
    { effectiveType: '2g', saveData: false },
    { effectiveType: 'slow-2g', saveData: false },
  ])('respects the same network restriction as route code preload: %j', async connection => {
    vi.stubGlobal('navigator', { connection });
    await preloadRouteData('/super-watchlist');
    await preloadRouteData('/essay-radar/feed');
    expect(systemConfigApi.getWatchlist).not.toHaveBeenCalled();
    expect(essayRadarApi.status).not.toHaveBeenCalled();
    // A skipped warm-up must not poison the 15-second reuse window.
    vi.stubGlobal('navigator', { connection: { effectiveType: '4g', saveData: false } });
    await preloadRouteData('/super-watchlist');
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenCalledTimes(1);
  });

  it('leaves all five task reads to the mounted task page', async () => {
    await Promise.all([preloadRouteData('/tasks'), preloadRouteData('/tasks')]);
    expect(industryResearchApi.projects).not.toHaveBeenCalled();
    expect(essayQuantApi.tasks).not.toHaveBeenCalled();
    expect(essayRadarApi.audioAnalysisTasks).not.toHaveBeenCalled();
    expect(essayRadarApi.audioBatchTasks).not.toHaveBeenCalled();
    expect(dataAcquisitionApi.tasks).not.toHaveBeenCalled();
  });

  it.each(['/essay-radar', '/essay-radar/feed', '/essay-radar/insights', '/essay-radar/trends'])('warms only shared status for %s, not unrelated dashboard aggregates', async path => {
    await preloadRouteData(path);
    expect(essayRadarApi.status).toHaveBeenCalledTimes(1);
    expect(essayRadarApi.dashboard).not.toHaveBeenCalled();
    expect(essayRadarApi.audioAnalysisTasks).not.toHaveBeenCalled();
  });

  it('allows a failed overview warm-up to retry rather than hiding its failure for 15 seconds', async () => {
    vi.mocked(investmentMonitorApi.stockWorkspace).mockRejectedValueOnce(new Error('temporary read failure'));
    await expect(preloadRouteData('/super-watchlist')).rejects.toThrow('temporary read failure');
    await preloadRouteData('/super-watchlist');
    expect(investmentMonitorApi.stockWorkspace).toHaveBeenCalledTimes(2);
  });
});
