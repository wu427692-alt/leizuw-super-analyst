import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRealtimeIndices, useRealtimeQuotes } from '../useRealtimeQuotes';
import { getRealtimeIndices, getRealtimeQuotes, type RealtimeQuote } from '../../api/realtimeQuotes';
import { isAshareLiveWindow } from '../../utils/marketSession';

vi.mock('../../api/realtimeQuotes', () => ({ getRealtimeQuotes: vi.fn(), getRealtimeIndices: vi.fn() }));
vi.mock('../../utils/marketSession', () => ({ isAshareLiveWindow: vi.fn() }));

const quote = (stockCode: string, currentPrice: number): RealtimeQuote => ({ stockCode, currentPrice, updateTime: '2026-09-11T15:00:00+08:00' });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };

describe('realtime quote batch activation', () => {
  beforeEach(() => {
    vi.clearAllMocks(); vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(isAshareLiveWindow).mockReturnValue(false);
    vi.mocked(getRealtimeQuotes).mockResolvedValue([]);
    vi.mocked(getRealtimeIndices).mockResolvedValue([]);
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it('immediately fetches a newly added symbol outside the trading window', async () => {
    vi.mocked(getRealtimeQuotes).mockResolvedValueOnce([quote('603306', 76)]).mockResolvedValueOnce([quote('603306', 76), quote('300308', 858)]);
    const { result, rerender } = renderHook(({ symbols }) => useRealtimeQuotes(symbols), { initialProps: { symbols: ['603306.SH'] } });
    await flush();
    expect(getRealtimeQuotes).toHaveBeenCalledTimes(1);
    rerender({ symbols: ['603306.SH', '300308.SZ'] });
    await flush();
    expect(getRealtimeQuotes).toHaveBeenLastCalledWith(['300308', '603306']);
    expect(result.current.quotes.get('300308')?.currentPrice).toBe(858);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(getRealtimeQuotes).toHaveBeenCalledTimes(2);
  });

  it('does not wait for a previous batch or let its late success remove the new stock', async () => {
    let finishOld!: (rows: RealtimeQuote[]) => void;
    vi.mocked(getRealtimeQuotes).mockImplementationOnce(() => new Promise(done => { finishOld = done; }))
      .mockResolvedValueOnce([quote('603306', 77), quote('300308', 850)]);
    const { result, rerender } = renderHook(({ symbols }) => useRealtimeQuotes(symbols), { initialProps: { symbols: ['603306.SH'] } });
    rerender({ symbols: ['603306.SH', '300308.SZ'] }); await flush();
    expect(result.current.quotes.get('300308')?.currentPrice).toBe(850);
    await act(async () => { finishOld([quote('603306', 75)]); });
    expect(result.current.quotes.get('603306')?.currentPrice).toBe(77);
    expect(result.current.quotes.get('300308')?.currentPrice).toBe(850);
  });

  it('ignores a late failure from a removed stock batch', async () => {
    let failOld!: (error: Error) => void;
    vi.mocked(getRealtimeQuotes).mockImplementationOnce(() => new Promise((_, reject) => { failOld = reject; }))
      .mockResolvedValueOnce([quote('300308', 850)]);
    const { result, rerender } = renderHook(({ symbols }) => useRealtimeQuotes(symbols), { initialProps: { symbols: ['603306.SH'] } });
    rerender({ symbols: ['300308.SZ'] }); await flush();
    await act(async () => { failOld(new Error('old stock timeout')); });
    expect(result.current.error).toBeNull();
    expect([...result.current.quotes.keys()]).toEqual(['300308']);
  });

  it('coalesces the same normalized batch and filters removed symbols immediately', async () => {
    vi.mocked(getRealtimeQuotes).mockResolvedValue([quote('603306', 76), quote('300308', 850)]);
    const { result, rerender } = renderHook(({ symbols }) => useRealtimeQuotes(symbols), { initialProps: { symbols: ['603306.SH', '300308.SZ'] } });
    await flush();
    rerender({ symbols: ['300308.SZ', 'sh603306', '603306.SH'] }); await flush();
    expect(getRealtimeQuotes).toHaveBeenCalledTimes(1);
    rerender({ symbols: [] });
    expect(result.current.quotes.size).toBe(0);
  });

  it('defers hidden new batches until visibility returns, even when markets are closed', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    const { rerender } = renderHook(({ symbols }) => useRealtimeQuotes(symbols), { initialProps: { symbols: ['603306.SH'] } });
    rerender({ symbols: ['300308.SZ'] }); await flush();
    expect(getRealtimeQuotes).not.toHaveBeenCalled();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(getRealtimeQuotes).toHaveBeenCalledExactlyOnceWith(['300308']);
  });

  it('retries a current batch on activation and retains the last successful quote on failure', async () => {
    vi.mocked(getRealtimeQuotes).mockResolvedValueOnce([quote('603306', 76)]).mockRejectedValueOnce(new Error('temporary timeout'))
      .mockResolvedValueOnce([quote('603306', 77)]);
    const { result } = renderHook(() => useRealtimeQuotes(['603306.SH'])); await flush();
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current.error).toBe('temporary timeout');
    expect(result.current.quotes.get('603306')?.currentPrice).toBe(76);
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current.error).toBeNull();
    expect(result.current.quotes.get('603306')?.currentPrice).toBe(77);
  });

  it('also refreshes a changed index batch immediately and ignores the old response', async () => {
    let finishOld!: (rows: Awaited<ReturnType<typeof getRealtimeIndices>>) => void;
    vi.mocked(getRealtimeIndices).mockImplementationOnce(() => new Promise(done => { finishOld = done; }))
      .mockResolvedValueOnce([{ code: '000300.SH', close: 4500 }]);
    const { result, rerender } = renderHook(({ symbols }) => useRealtimeIndices(symbols), { initialProps: { symbols: ['000001.SH'] } });
    rerender({ symbols: ['000300.SH'] }); await flush();
    await act(async () => { finishOld([{ code: '000001.SH', close: 3900 }]); });
    expect([...result.current.keys()]).toEqual(['000300.SH']);
  });
});
