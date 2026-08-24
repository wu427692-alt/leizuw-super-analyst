import { describe, expect, it } from 'vitest';
import { isCurrentShanghaiQuote, marketQuoteSession, quoteDateKey, shanghaiDateKey, shouldPreferQuote } from '../marketQuoteDate';

describe('market quote date contract', () => {
  const noonInShanghai = new Date('2026-08-20T04:00:00.000Z');

  it('accepts compact Tushare dates and current-day local snapshots', () => {
    expect(quoteDateKey('20260820')).toBe('2026-08-20');
    expect(isCurrentShanghaiQuote('2026-08-20T11:30:05', noonInShanghai)).toBe(true);
  });

  it('rejects a previous trading-day close as a current-day quote', () => {
    expect(isCurrentShanghaiQuote('20260819', noonInShanghai)).toBe(false);
    expect(isCurrentShanghaiQuote('2026-08-19T15:00:00', noonInShanghai)).toBe(false);
  });

  it('uses Asia/Shanghai at the UTC date boundary', () => {
    expect(shanghaiDateKey(new Date('2026-08-19T16:30:00.000Z'))).toBe('2026-08-20');
  });

  it('labels a previous trading day as a dated close and keeps its daily change valid', () => {
    expect(marketQuoteSession('20260819', 'tushare.index_daily', noonInShanghai)).toEqual({
      dateKey: '2026-08-19',
      label: '2026-08-19 收盘',
      canShowChange: true,
      isCurrentDay: false,
      isClose: true,
    });
  });

  it('labels current snapshots as realtime during trading and close after 15:00', () => {
    expect(marketQuoteSession('2026-08-20T10:30:00', 'tushare.legacy_snapshot', noonInShanghai).label)
      .toBe('2026-08-20 实时');
    expect(marketQuoteSession(
      '2026-08-20T15:01:00',
      'tushare.legacy_snapshot',
      new Date('2026-08-20T07:30:00.000Z'),
    ).label).toBe('2026-08-20 收盘');
  });

  it('does not mislabel an incomplete post-close snapshot as the closing price', () => {
    expect(marketQuoteSession(
      '2026-08-24T14:53:27',
      'tencent.snapshot',
      new Date('2026-08-24T07:20:00.000Z'),
    )).toMatchObject({ label: '2026-08-24 14:53 最新', isClose: false });
  });

  it('prefers a newer trading-day snapshot even when its polling freshness expired', () => {
    expect(shouldPreferQuote('2026-08-24T14:53:27', '20260821', true)).toBe(true);
    expect(shouldPreferQuote('2026-08-24T14:53:27', '2026-08-24T15:00:00', true)).toBe(false);
    expect(shouldPreferQuote('2026-08-21T15:00:00', '2026-08-24T14:53:27', false)).toBe(false);
  });

  it('does not authorize a change percentage when the trading date is missing', () => {
    expect(marketQuoteSession(null, 'tushare.index_daily', noonInShanghai)).toEqual({
      dateKey: null,
      label: '交易日未标注',
      canShowChange: false,
      isCurrentDay: false,
      isClose: false,
    });
  });
});
