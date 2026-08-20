import type { MonitorEvent } from './investmentMonitor';

export type MarketPoint = { date?: string | null; value?: number | null };

export type MarketIndexCard = {
  code: string;
  name: string;
  region: string;
  tradeDate?: string | null;
  close?: number | null;
  change?: number | null;
  changePct?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  amount?: number | null;
  amountYi?: number | null;
  history: MarketPoint[];
};

export type HomeWatchlistCard = {
  symbol: string;
  name: string;
  eventCount: number;
  highPriorityCount: number;
  opportunityScore: number;
  riskScore: number;
  perspectives: Record<string, number>;
  sentiment: Record<string, number>;
  latestQuote?: {
    currentPrice?: number;
    change?: number;
    changePercent?: number;
    open?: number;
    high?: number;
    low?: number;
    prevClose?: number;
    volume?: number;
    amount?: number;
    updateTime?: string;
  } | null;
  institutionRatingCount: number;
  latestRating?: string | null;
  latestEventAt?: string | null;
  history: MarketPoint[];
  latestCatalyst?: MonitorEvent | null;
  latestRisk?: MonitorEvent | null;
  latestInstitution?: MonitorEvent | null;
  latestEvents: MonitorEvent[];
};

export type HomeDashboard = {
  generatedAt: string;
  marketTime: string;
  tradeDate: string;
  cnIndices: MarketIndexCard[];
  globalIndices: MarketIndexCard[];
  breadth: {
    up: number; down: number; flat: number; limitUp: number; limitDown: number; total: number;
    distribution: Array<{ label: string; count: number }>;
  };
  northbound: { tradeDate?: string; northMoney?: number; northMoneyYi?: number; southMoney?: number };
  watchlist: HomeWatchlistCard[];
  latestEvents: MonitorEvent[];
  intelligenceSummary: {
    eventCount?: number; highPriorityCount?: number; bullishCount?: number; bearishCount?: number; activeSourceCount?: number;
  };
  warnings: string[];
  cache: { hit: boolean; ttlSeconds: number; ageSeconds: number };
};
