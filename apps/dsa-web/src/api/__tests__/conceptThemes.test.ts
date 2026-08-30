import { describe, expect, it } from 'vitest';

import { normalizeConceptOverview } from '../conceptThemes';

describe('normalizeConceptOverview', () => {
  it('preserves business dictionary keys while converting response fields', () => {
    const result = normalizeConceptOverview({
      items: [],
      total: 0,
      page: 1,
      page_size: 48,
      summary: {
        themes: 1,
        memberships: 0,
        membered_themes: 0,
        attempted_themes: 0,
        failed_themes: 0,
        scan_coverage_pct: 0,
        membership_coverage_pct: 0,
        exposures: 0,
        sources: { tushare_ths: 1 },
        types: { concept_board: 1 },
        families: { 'AI算力与数字基础设施': 1 },
        cluster_families: { 'AI算力与数字基础设施': { 'CPO/光模块': 1 } },
      },
      methodology: {
        version: 'test',
        principles: [],
        weight_formula: {},
        beta_formula: '',
        windows: [],
        minimum_observations: 20,
        sources: [],
        license_note: '',
      },
      sync: { status: 'idle', progress: 0, stage: '', sources: { tushare_dc_theme: 1 } },
    });

    expect(result.pageSize).toBe(48);
    expect(result.summary.memberedThemes).toBe(0);
    expect(result.summary.families).toEqual({ 'AI算力与数字基础设施': 1 });
    expect(result.summary.clusterFamilies).toEqual({ 'AI算力与数字基础设施': { 'CPO/光模块': 1 } });
    expect(result.summary.sources).toEqual({ tushare_ths: 1 });
    expect(result.sync?.sources).toEqual({ tushare_dc_theme: 1 });
  });
});
