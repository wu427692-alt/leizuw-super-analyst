import type { ConceptOverview, ConceptStock, ConceptTheme, StockThemeLens, ThemeDetail } from '../../../api/conceptThemes';

export const methodology = { version: 'QA', principles: [], weightFormula: {}, betaFormula: '测试归因', windows: [20, 60, 120], minimumObservations: 20, licenseNote: '测试数据', sources: [{ key: 'ths', name: '同花顺', provider: 'ths', reliability: 1 }] };
export const themes: ConceptTheme[] = ['测试光通信', '测试半导体'].map((name, i) => ({ id: i + 1, canonicalName: name, name, source: 'ths', sourceLabel: '同花顺', sourceCode: `QA${i}`, themeType: 'concept', level: 1, constituentCount: 30, family: '测试产业', cluster: name, canonicalSourceCount: 3, canonicalNodeCount: 3 }));
export function stock(index: number): ConceptStock {
  return { tsCode: `600${String(index).padStart(3, '0')}.SH`, name: `测试公司${index}`, sources: ['ths'], reasons: [], sourceCount: 1, weightScore: 100 - index, beta: 1, residualReturn: index, confidence: 'high', inWatchlist: index === 1 };
}
export function detail(id = 1, horizonDays = 60): ThemeDetail {
  return {
    theme: themes[id - 1], sourceNodes: [themes[id - 1]], stocks: Array.from({ length: 30 }, (_, i) => stock(i + 1)),
    watchlistStocks: [{ tsCode: stock(1).tsCode, name: stock(1).name }], totalStocks: 30, consensusStocks: 0, attributionReady: 30,
    history: { points: [], availableDates: 0, method: '测试历史' },
    institutionCorpus: { total: 0, bullish: 0, bearish: 0, neutral: 0, score: 0, recent14D: 0, priorWindow: 0, windowDays: 30, items: [], method: '测试语料' },
    relatedThemes: { items: [], targetTotalStocks: 30, method: '测试关联' }, horizonDays, methodology,
  };
}
export function lens(code: string, horizonDays = 60): StockThemeLens {
  return {
    tsCode: code, name: `画像${code}`, horizonDays, asOfDate: '2026-09-04', themes: [], primaryThemes: [], uniqueThemes: [], uniqueDrivers: [],
    uniqueDriverSummary: { categories: {}, directions: {}, method: '测试驱动' },
    overlapAudit: { independentClusterCount: 0, overlapRate: 0, dominantClusterShare: 0, clusters: [], method: '测试重叠' },
    summary: { themeCount: 0, sourceCount: 0, consensusCount: 0, independentClusterCount: 0, themeOverlapRate: 0, alphaPositiveCount: 0, stableBetaCount: 0, persistentAlphaCount: 0 }, methodology,
  };
}
export function overview(): ConceptOverview {
  return {
    items: themes, stockMatches: [], total: 2, page: 1, pageSize: 24, methodology,
    summary: { themes: 2, classifiedThemes: 2, semanticCoveragePct: 100, memberships: 60, memberedThemes: 2, attemptedThemes: 2, failedThemes: 0, scanCoveragePct: 100, membershipCoveragePct: 100, exposures: 60, exposureThemes: 2, exposureStocks: 30, researchableThemes: 2, sources: { ths: 2 }, sourceHealth: {}, types: {}, families: {}, clusterFamilies: {}, quality: { freshCatalogs: 1, totalCatalogs: 1, failedThemes: 0, warnings: [], catalogDate: '2026-09-04', exposureDate: '2026-09-04' } },
  };
}
