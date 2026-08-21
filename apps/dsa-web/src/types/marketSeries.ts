export type MarketPeriod = 'intraday' | 'daily' | 'weekly' | 'monthly' | 'yearly';
export type MarketRange = '1d' | '5d' | '1m' | '3m' | '6m' | '1y' | '2y' | '3y' | '5y' | '10y' | 'max';

export type MarketBar = {
  date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
  amount?: number | null;
  cumulativeVolume?: number | null;
  cumulativeAmount?: number | null;
  changePercent?: number | null;
};

export type MarketSeries = {
  stockCode: string;
  stockName?: string | null;
  period: MarketPeriod;
  range?: string | null;
  source?: string | null;
  storedCount: number;
  latestAt?: string | null;
  preClose?: number | null;
  refreshed: boolean;
  storage: string;
  data: MarketBar[];
};
