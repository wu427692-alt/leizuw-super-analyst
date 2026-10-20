import { describe, expect, it } from 'vitest';
import { formatMarketQuoteTimestamp, isCurrentShanghaiQuote, marketQuoteSession, quoteDateKey, shanghaiDateKey, shouldPreferQuote } from '../marketQuoteDate';

describe('market quote date contract', () => {
  const noonInShanghai = new Date('2026-08-20T04:00:00.000Z');

  it('formats actual quote time in Shanghai without borrowing the research UTC convention', () => {
    expect(formatMarketQuoteTimestamp('2026-09-11T09:50:35')).toBe('2026-09-11 09:50');
    expect(formatMarketQuoteTimestamp('2026-09-11T01:50:35Z')).toBe('2026-09-11 09:50');
    expect(formatMarketQuoteTimestamp('2026-09-10T16:30:00Z')).toBe('2026-09-11 00:30');
    expect(formatMarketQuoteTimestamp('20260910')).toBe('2026-09-10');
    for (const value of [null, '', 'invalid', '2026-02-31', '2026-09-11T28:30:00']) {
      expect(formatMarketQuoteTimestamp(value)).toBeNull();
    }
  });

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

  it('rejects an older same-day snapshot even if its provider marks it fresh', () => {
    expect(shouldPreferQuote('2026-09-11T09:50:01', '2026-09-11T09:50:35')).toBe(false);
    expect(shouldPreferQuote('2026-09-11T09:50:36', '2026-09-11T09:50:35')).toBe(true);
    expect(shouldPreferQuote('2026-09-11T09:50:35', '2026-09-11T09:50:35')).toBe(true);
  });

  it('accepts a later same-day closing quote even when its poll is marked stale', () => {
    expect(shouldPreferQuote('2026-09-11T15:00:00', '2026-09-11T14:30:00', true)).toBe(true);
    expect(shouldPreferQuote('20260911', '2026-09-11T14:59:59', true)).toBe(true);
    expect(shouldPreferQuote('2026-09-11T07:00:00Z', '2026-09-11T14:30:00+08:00', true)).toBe(true);
    expect(shouldPreferQuote('2026-09-11T14:30:00', '20260911', true)).toBe(false);
    expect(shouldPreferQuote('2026-09-11T15:00:00', '20260911', true)).toBe(false);
  });

  it('never overwrites a dated quote with an undated candidate, regardless of staleness', () => {
    for (const stale of [true, false]) {
      expect(shouldPreferQuote(null, '2026-09-11T14:30:00', stale)).toBe(false);
      expect(shouldPreferQuote('', '2026-09-11T14:30:00', stale)).toBe(false);
      expect(shouldPreferQuote('invalid', '2026-09-11T14:30:00', stale)).toBe(false);
      expect(shouldPreferQuote('2026-09-11T15:00:00', null, stale)).toBe(true);
    }
  });

  it('treats a date-only daily row as the official close rather than midnight', () => {
    expect(shouldPreferQuote('2026-09-11T14:59:59', '20260911')).toBe(false);
    expect(shouldPreferQuote('2026-09-11T15:00:00', '20260911')).toBe(true);
    expect(shouldPreferQuote('20260911', '2026-09-11T14:59:59')).toBe(true);
    expect(shouldPreferQuote('20260911', '2026-09-11T15:30:00')).toBe(false);
    expect(shouldPreferQuote('20260911', '2026-09-11')).toBe(true);
  });

  it('compares explicit timezone timestamps by instant, including a UTC date boundary', () => {
    expect(shouldPreferQuote('2026-09-11T01:50:34Z', '2026-09-11T09:50:35+08:00')).toBe(false);
    expect(shouldPreferQuote('2026-09-11T01:50:36Z', '2026-09-11T09:50:35')).toBe(true);
    expect(quoteDateKey('2026-09-10T16:30:00Z')).toBe('2026-09-11');
    expect(shouldPreferQuote('2026-09-10T16:30:00Z', '2026-09-11T00:20:00+08:00')).toBe(true);
    expect(marketQuoteSession('2026-09-11T07:30:00Z', 'tencent.snapshot', new Date('2026-09-11T08:00:00Z')))
      .toMatchObject({ label: '2026-09-11 收盘', isClose: true });
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
