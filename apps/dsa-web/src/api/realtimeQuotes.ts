import apiClient, { BACKGROUND_ROUTE_HEADERS } from './index';

export type RealtimeQuote = {
  stockCode: string; stockName?: string | null; currentPrice: number;
  change?: number | null; changePercent?: number | null; open?: number | null;
  high?: number | null; low?: number | null; prevClose?: number | null;
  volume?: number | null; amount?: number | null;
  secondVolume?: number | null; secondAmount?: number | null; updateTime?: string | null;
  source?: string | null; staleSeconds?: number | null; isStale?: boolean | null;
};

type RawQuote = Record<string, unknown>;
const num = (value: unknown) => typeof value === 'number' ? value : value == null ? null : Number(value);
type TimedValue<T> = { expiresAt: number; value: T };
const quoteCache = new Map<string, TimedValue<RealtimeQuote[]>>();
const quoteInflight = new Map<string, Promise<RealtimeQuote[]>>();
const indexCache = new Map<string, TimedValue<RealtimeIndexQuote[]>>();
const indexInflight = new Map<string, Promise<RealtimeIndexQuote[]>>();
const REALTIME_COALESCE_MS = 1_000;

export async function getRealtimeQuotes(symbols: string[]): Promise<RealtimeQuote[]> {
  const unique = Array.from(new Set(symbols.map(value => value.trim()).filter(Boolean))).sort();
  if (!unique.length) return [];
  const key = unique.join(',');
  const cached = quoteCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const running = quoteInflight.get(key);
  if (running) return running;
  const request = apiClient.get<RawQuote[]>('/api/v1/stocks/market-data/realtime', {
    params: { symbols: key, refresh_missing: true },
    headers: BACKGROUND_ROUTE_HEADERS,
  }).then((response) => response.data.map(row => ({
      stockCode: String(row.stock_code ?? ''), stockName: row.stock_name == null ? null : String(row.stock_name),
      currentPrice: num(row.current_price) ?? 0, change: num(row.change), changePercent: num(row.change_percent),
      open: num(row.open), high: num(row.high), low: num(row.low), prevClose: num(row.prev_close),
      volume: num(row.volume), amount: num(row.amount),
      secondVolume: num(row.second_volume), secondAmount: num(row.second_amount),
      updateTime: row.update_time == null ? null : String(row.update_time),
      source: row.source == null ? null : String(row.source), staleSeconds: num(row.stale_seconds),
      isStale: Boolean(row.is_stale),
    })))
    .then((value) => {
      quoteCache.set(key, { value, expiresAt: Date.now() + REALTIME_COALESCE_MS });
      return value;
    })
    .finally(() => quoteInflight.delete(key));
  quoteInflight.set(key, request);
  return request;
}

export type RealtimeIndexQuote = { code: string; close?: number | null; changePct?: number | null; updateTime?: string | null; source?: string | null; staleSeconds?: number | null; isStale?: boolean };

export async function getRealtimeIndices(symbols: string[]): Promise<RealtimeIndexQuote[]> {
  const unique = Array.from(new Set(symbols.map(value => value.trim()).filter(Boolean))).sort();
  if (!unique.length) return [];
  const key = unique.join(',');
  const cached = indexCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const running = indexInflight.get(key);
  if (running) return running;
  const request = apiClient.get<RawQuote[]>('/api/v1/stocks/market-data/realtime-indices', {
    params: { symbols: key },
    headers: BACKGROUND_ROUTE_HEADERS,
  }).then((response) => response.data.map(row => ({ code: String(row.code ?? ''), close: num(row.close),
      changePct: num(row.change_pct), updateTime: row.update_time == null ? null : String(row.update_time),
      source: row.source == null ? null : String(row.source), staleSeconds: num(row.stale_seconds),
      isStale: Boolean(row.is_stale) })))
    .then((value) => {
      indexCache.set(key, { value, expiresAt: Date.now() + REALTIME_COALESCE_MS });
      return value;
    })
    .finally(() => indexInflight.delete(key));
  indexInflight.set(key, request);
  return request;
}
