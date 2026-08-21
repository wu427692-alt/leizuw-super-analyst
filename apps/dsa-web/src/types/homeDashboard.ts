import type { MonitorEvent } from './investmentMonitor';

export type MarketPoint = { date?: string | null; value?: number | null };

export type DistributionBucket = { label: string; count: number };

export type SectorMover = {
  name: string;
  changePct: number;
  companyCount?: number | null;
  leader?: string | null;
};

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
  source?: string | null;
  updateTime?: string | null;
  isStale?: boolean | null;
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
    secondVolume?: number;
    secondAmount?: number;
    updateTime?: string;
    source?: string;
    staleSeconds?: number;
    isStale?: boolean;
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
    available: boolean;
    up: number; down: number; flat: number; limitUp: number; limitDown: number; total: number;
    distribution: DistributionBucket[];
    tradeDate?: string; updatedAt?: string | null; source?: string | null; reason?: string | null;
  };
  sectorDistribution: {
    available: boolean;
    up: number; down: number; flat: number; total: number;
    distribution: DistributionBucket[];
    leaders: SectorMover[]; laggards: SectorMover[];
    tradeDate?: string; updatedAt?: string | null; source?: string | null; reason?: string | null;
  };
  northbound: { tradeDate?: string; northMoney?: number; northMoneyYi?: number; southMoney?: number; source?: string };
  watchlist: HomeWatchlistCard[];
  latestEvents: MonitorEvent[];
  intelligenceSummary: {
    eventCount?: number; highPriorityCount?: number; bullishCount?: number; bearishCount?: number; activeSourceCount?: number;
  };
  warnings: string[];
  cache: { hit: boolean; ttlSeconds: number; ageSeconds: number };
};
