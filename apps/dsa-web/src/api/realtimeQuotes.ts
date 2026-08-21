import apiClient from './index';

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

export async function getRealtimeQuotes(symbols: string[]): Promise<RealtimeQuote[]> {
  const unique = Array.from(new Set(symbols.map(value => value.trim()).filter(Boolean)));
  if (!unique.length) return [];
  const response = await apiClient.get<RawQuote[]>('/api/v1/stocks/market-data/realtime', {
    params: { symbols: unique.join(','), refresh_missing: true },
  });
  return response.data.map(row => ({
    stockCode: String(row.stock_code ?? ''), stockName: row.stock_name == null ? null : String(row.stock_name),
    currentPrice: num(row.current_price) ?? 0, change: num(row.change), changePercent: num(row.change_percent),
    open: num(row.open), high: num(row.high), low: num(row.low), prevClose: num(row.prev_close),
    volume: num(row.volume), amount: num(row.amount),
    secondVolume: num(row.second_volume), secondAmount: num(row.second_amount),
    updateTime: row.update_time == null ? null : String(row.update_time),
    source: row.source == null ? null : String(row.source), staleSeconds: num(row.stale_seconds),
    isStale: Boolean(row.is_stale),
  }));
}

export type RealtimeIndexQuote = { code: string; close?: number | null; changePct?: number | null; updateTime?: string | null; source?: string | null; staleSeconds?: number | null; isStale?: boolean };

export async function getRealtimeIndices(symbols: string[]): Promise<RealtimeIndexQuote[]> {
  if (!symbols.length) return [];
  const response = await apiClient.get<RawQuote[]>('/api/v1/stocks/market-data/realtime-indices', {
    params: { symbols: symbols.join(',') },
  });
  return response.data.map(row => ({ code: String(row.code ?? ''), close: num(row.close),
    changePct: num(row.change_pct), updateTime: row.update_time == null ? null : String(row.update_time),
    source: row.source == null ? null : String(row.source), staleSeconds: num(row.stale_seconds),
    isStale: Boolean(row.is_stale) }));
}
