import apiClient from './index';
import { toCamelCase } from './utils';
import type { MarketPeriod, MarketRange, MarketSeries } from '../types/marketSeries';

type CacheEntry = { expiresAt: number; value: MarketSeries };
const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<MarketSeries>>();

export async function getMarketSeries(
  symbol: string,
  period: MarketPeriod,
  range: MarketRange,
  refresh = false,
  assetType: 'stock' | 'index' = 'stock',
): Promise<MarketSeries> {
  const key = `${assetType}:${symbol}:${period}:${range}`;
  const cached = cache.get(key);
  if (!refresh && cached && cached.expiresAt > Date.now()) return cached.value;
  const running = inflight.get(key);
  if (running) return running;

  const path = assetType === 'index'
    ? `/api/v1/stocks/market-data/index/${encodeURIComponent(symbol)}`
    : `/api/v1/stocks/${encodeURIComponent(symbol)}/history`;
  const request = apiClient.get(path, {
    params: { period, range, refresh, max_points: period === 'intraday' ? 2000 : undefined },
    // These endpoints read the local SQLite market store. A two-minute wait
    // makes a transient lock look like a frozen page; fail fast so the chart's
    // recovery loop can retry without blocking stock switching.
    timeout: period === 'intraday' ? 20_000 : 45_000,
  }).then((response) => {
    const value = toCamelCase<MarketSeries>(response.data);
    cache.set(key, { value, expiresAt: Date.now() + (period === 'intraday' ? 10_000 : 300_000) });
    return value;
  }).finally(() => inflight.delete(key));
  inflight.set(key, request);
  return request;
}
