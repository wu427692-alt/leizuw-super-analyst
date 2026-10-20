import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

import apiClient from '../index';
import { conceptThemesApi, normalizeConceptOverview } from '../conceptThemes';
import { resetRequestCacheForTests } from '../requestCache';

describe('conceptThemesApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRequestCacheForTests();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-06T00:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    resetRequestCacheForTests();
  });

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

  it('reuses a fresh concept response without issuing another request', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [{ canonical_name: '光模块' }], total: 1 } });

    const first = await conceptThemesApi.rotation(20, 24);
    await vi.advanceTimersByTimeAsync(59_999);
    const second = await conceptThemesApi.rotation(20, 24);

    expect(first.items[0].canonicalName).toBe('光模块');
    expect(second).toBe(first);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('waits for and deduplicates a refresh once the concept response expires', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [{ canonical_name: '旧结果' }], total: 1 } });
    await conceptThemesApi.rotation(20, 24);
    await vi.advanceTimersByTimeAsync(60_001);

    let resolveRefresh!: (value: { data: { items: Array<{ canonical_name: string }>; total: number } }) => void;
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { resolveRefresh = resolve; }));
    const firstRefresh = conceptThemesApi.rotation(20, 24);
    const duplicateRefresh = conceptThemesApi.rotation(20, 24);
    let settled = false;
    void firstRefresh.finally(() => { settled = true; });
    await Promise.resolve();

    expect(settled).toBe(false);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    resolveRefresh({ data: { items: [{ canonical_name: '新结果' }], total: 1 } });
    await expect(Promise.all([firstRefresh, duplicateRefresh])).resolves.toEqual([
      expect.objectContaining({ items: [expect.objectContaining({ canonicalName: '新结果' })] }),
      expect.objectContaining({ items: [expect.objectContaining({ canonicalName: '新结果' })] }),
    ]);
  });

  it('exposes a refresh failure after expiry instead of returning the old concept response', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [{ canonical_name: '旧结果' }], total: 1 } });
    await conceptThemesApi.rotation(20, 24);
    await vi.advanceTimersByTimeAsync(60_001);
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('refresh failed'));

    await expect(conceptThemesApi.rotation(20, 24)).rejects.toThrow('refresh failed');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('does not cache a theme payload for the wrong attribution window and retries the network', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          theme: { id: 7, canonical_name: '光模块' },
          source_nodes: [],
          horizon_days: 20,
        },
      })
      .mockResolvedValueOnce({
        data: {
          theme: { id: 7, canonical_name: '光模块' },
          source_nodes: [],
          horizon_days: 60,
        },
      });

    await expect(conceptThemesApi.theme(7, true, 60)).rejects.toThrow('题材身份或归因窗口与请求不一致');
    await expect(conceptThemesApi.theme(7, true, 60)).resolves.toEqual(expect.objectContaining({
      theme: expect.objectContaining({ id: 7 }),
      horizonDays: 60,
    }));
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('does not cache a stock payload for a different symbol and retries the network', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { ts_code: '000001.SZ', name: '错误股票', horizon_days: 60 } })
      .mockResolvedValueOnce({ data: { ts_code: '600519.SH', name: '贵州茅台', horizon_days: 60 } });

    await expect(conceptThemesApi.stock('600519.SH', true, 60)).rejects.toThrow('股票身份或归因窗口与请求不一致');
    await expect(conceptThemesApi.stock('600519.SH', true, 60)).resolves.toEqual(expect.objectContaining({
      tsCode: '600519.SH',
      horizonDays: 60,
    }));
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('accepts and caches a canonical theme alias proven by the requested source node', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        theme: { id: 99, canonical_name: '光模块' },
        source_nodes: [{ id: 7, canonical_name: '光模块', source: 'ths', source_code: '885531' }],
        horizon_days: 60,
      },
    });

    const first = await conceptThemesApi.theme(7, true, 60);
    const second = await conceptThemesApi.theme(7, true, 60);

    expect(first.theme.id).toBe(99);
    expect(first.sourceNodes[0].id).toBe(7);
    expect(second).toBe(first);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('cleans up the object URL even when a CSV download click fails', async () => {
    const blob = new Blob(['theme']);
    vi.mocked(apiClient.get).mockResolvedValue({ data: blob, headers: { 'content-disposition': "attachment; filename*=UTF-8''bad%ZZ.csv" } });
    const url = 'blob:concept-export';
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue(url);
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => { throw new Error('download blocked'); });

    await expect(conceptThemesApi.exportTheme(7, 60)).rejects.toThrow('download blocked');
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(revokeObjectURL).toHaveBeenCalledWith(url);

    click.mockRestore();
    createObjectURL.mockRestore();
    revokeObjectURL.mockRestore();
  });
});
