import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EssayRadarPage from '../../pages/EssayRadarPage';

const api = vi.hoisted(() => ({
  insights: vi.fn(), status: vi.fn(), dashboard: vi.fn(), wordCloud: vi.fn(), deepInsights: vi.fn(),
  dailyReports: vi.fn(), list: vi.fn(), audioFiles: vi.fn(), historicalBacklog: vi.fn(),
}));
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: api }));
vi.mock('../../contexts/UserAccessContext', () => ({
  useUserAccess: () => ({ accessEnabled: false, loggedIn: false, user: null, authMethod: 'password', isLoading: false }),
}));

const trend = [
  { date: '2026-10-01', total: 12, bullish: 5, bearish: 3, neutral: 3, mixed: 1, averageImportance: 60, averageConfidence: 0.6 },
  { date: '2026-10-02', total: 20, bullish: 9, bearish: 4, neutral: 5, mixed: 2, averageImportance: 66, averageConfidence: 0.7 },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.insights.mockResolvedValue({
    generatedAt: '2026-10-02T00:00:00Z', windowDays: 30, latestDataAt: '2026-10-02T00:00:00Z',
    yesterday: { date: '2026-10-01', analyzedCount: 12, highImportanceCount: 2, lowConfidenceCount: 1, rumorCount: 0, evidenceCoveragePercent: 88 },
    coverage: { analyzedCount: 120, evidenceRecords: 90, evidenceCoveragePercent: 75, configuredModels: ['a'], completedReportModels: 1 },
    trend, sourceQuality: [{ name: '高', count: 3 }], informationTypes: [{ name: '研究', count: 4 }],
    sourceMix: [{ name: '星球A', count: 70 }, { name: '星球B', count: 30 }, { name: '星球C', count: 10 }, { name: '星球D', count: 8 }, { name: '星球E', count: 5 }, { name: '星球F', count: 2 }],
    modelComparison: { reportDate: '2026-10-01', reports: [], consensus: [], divergences: [] },
    watchlist: [{ symbol: '300750.SZ', name: '宁德时代', mentionCount: 30, dayMentions: 1, weekMentions: 5, monthMentions: 20, stances: {}, averageImportance: 70, averageConfidence: 0.7, catalysts: [], risks: [], trend: [{ date: '2026-10-01', total: 2 }, { date: '2026-10-02', total: 5 }], latestItems: [] }],
    highNoveltySignals: [],
  });
  api.status.mockResolvedValue({ progress: { coveragePercent: 80, completed: 10, pending: 2, failed: 0 }, worker: { running: true, pollSeconds: 5, lastSyncAt: null }, mcpSync: { running: true, pollSeconds: 10, lastSyncAt: null, groups: [] }, dailyReportWorker: { running: true, runHourShanghai: 8 } });
  api.dashboard.mockResolvedValue({ days: 30, generatedAt: '', summary: { analyzedCount: 10, averageImportance: 60, stockCount: 2, tagCount: 1, totalTokens: 10 }, sentiment: [], categories: [], topTags: [], topIndustries: [], topThemes: [], topStocks: [{ key: 'k', tsCode: '300750.SZ', name: '宁德时代', mentionCount: 9, bullish: 5, bearish: 2, neutral: 2, watching: 0, averageImportance: 70 }] });
  api.wordCloud.mockResolvedValue({ startDate: '2026-09-01', endDate: '2026-10-01', sourceCount: 10, items: [{ name: '算力', count: 8, change: 2 }] });
  api.deepInsights.mockResolvedValue({
    period: { startDate: '2026-09-19', endDate: '2026-10-02', granularity: 'day' }, windowDays: 14,
    summary: { analyzedCount: 100, sourceCount: 3, themeCount: 8, stockCount: 12, divergenceCount: 2 },
    marketImpact: {
      items: [{ tsCode: '300750.SZ', name: '宁德时代', mentionCount: 6, dataSource: '本地行情库', series: [{ date: '2026-10-01', mentionCount: 1, priceReturn: 1 }], metrics: [{ period: 5, averageReturn: 1.2, winRate: 60, sampleCount: 5, confidenceInterval95: [0.2, 2.2], averageExcessReturn: 0.5 }], insight: '测试', leadLag: [{ lagSessions: 0, correlation: 0.3, sampleCount: 5 }], attentionComparison: [{ level: 'high', averageReturn5D: 1, winRate5D: 60, sampleCount: 5 }] }],
      coverage: { eventCoveragePercent: 50, coveredEventDayCount: 5, eventDayCount: 10, pricedStockCount: 3, candidateStockCount: 4, priceStart: '2026-09-01', priceEnd: '2026-10-01', benchmarkAvailable: true, sources: ['本地行情库'] },
      causalityNote: '相关不是因果', entryRule: 'e', exitRule: 'x', priceBasis: 'p', dedupeRule: 'd',
    },
    layers: { sources: [{ key: 's1', label: '星球A', count: 5 }], themes: [{ key: 't1', label: '算力', count: 4 }], stocks: [{ key: 'k1', label: '宁德时代', count: 3, tsCode: '300750.SZ', stockName: '宁德时代' }], outcomes: [{ key: 'o1', label: '5日上涨', count: 2, tsCode: '300750.SZ', stockName: '宁德时代' }], edges: [{ fromStage: 'sources', from: 's1', toStage: 'themes', to: 't1', count: 5 }] },
    themeHeatmap: { dates: ['2026-10-01', '2026-10-02'], taxonomy: { rawThemeCount: 9, canonicalThemeCount: 5 }, items: [{ name: '算力', total: 5, aliases: [], points: [{ date: '2026-10-01', count: 2, concentrationScore: 30, sharePercent: 20, dailyTotal: 10 }] }] },
    evidenceFunnel: [{ name: '扫描语料', count: 100 }, { name: '已分析', count: 60 }],
  });
  api.dailyReports.mockResolvedValue({ total: 1, items: [{ reportDate: '2026-10-01', model: 'm', sourceCount: 3, totalTokens: 100, status: 'completed', report: { executiveSummary: '结论', keyThemes: [], stockFocus: [], earningsImplications: [], valuationImplications: [], novelSignals: [], riskWatch: [], divergences: [], dataQuality: { limitations: [] } } }] });
  api.historicalBacklog.mockResolvedValue({ totalNotes: 100, queuedNotes: 10, completed: 80, pending: 5, processing: 1, failed: 2, coveragePercent: 80, unqueued: 12, groupCount: 2, notes24h: 3, notes7d: 9, notes30d: 30 });
});

afterEach(() => cleanup());

async function mount(path: string) {
  render(<MemoryRouter initialEntries={[path]}><EssayRadarPage /></MemoryRouter>);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe('EssayRadar views smoke', () => {
  it('renders today judgement with pulse visualizations', async () => {
    await mount('/essay-radar');
    expect(await screen.findByText('关注度与构成')).toBeInTheDocument();
    expect(screen.getByLabelText('来源构成')).toBeInTheDocument();
    expect(screen.getByLabelText('情绪构成')).toBeInTheDocument();
  });
  it('renders atlas sub tabs and only the active pane', async () => {
    await mount('/essay-radar/insights');
    expect(screen.getByRole('tab', { name: '关联验证' })).toBeInTheDocument();
    expect(screen.queryByText('主题热力迁移')).not.toBeInTheDocument();
  });
  it('renders trends view', async () => {
    await mount('/essay-radar/trends');
    expect(await screen.findByText('提及量与平均重要度')).toBeInTheDocument();
  });
  it('renders reports view', async () => {
    await mount('/essay-radar/reports');
    expect(await screen.findByText('模型共识与分歧')).toBeInTheDocument();
  });
  it('renders system view', async () => {
    await mount('/essay-radar/system');
    expect(await screen.findByText('实时分析')).toBeInTheDocument();
    expect(await screen.findByText('历史小作文补分析')).toBeInTheDocument();
  });
});
