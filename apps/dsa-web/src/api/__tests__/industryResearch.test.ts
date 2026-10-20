import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() }, BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Background': '1' } }));

import apiClient from '../index';
import { industryResearchApi } from '../industryResearch';
import { resetRequestCacheForTests } from '../requestCache';

describe('industryResearchApi task archive', () => {
  beforeEach(() => { vi.clearAllMocks(); resetRequestCacheForTests(); });

  it('keeps default callers compatible and normalizes task metadata', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: {
      items: [{ project_id: 'old-31', topic: '光模块' }], total: 42,
      counts: { all: 42, completed: 35, running: 2, limited: 3, failed: 2 },
    } });
    const result = await industryResearchApi.projects();
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/industry-research/projects', expect.objectContaining({
      params: { limit: 30, offset: 0, query: '', status: 'all' },
    }));
    expect(result.items[0].projectId).toBe('old-31');
    expect(result.total).toBe(42);
    expect(result.counts?.completed).toBe(35);
  });

  it('isolates cache entries for page, title and status, while deduplicating identical queries', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => ({ data: {
      items: [{ project_id: JSON.stringify(config?.params) }], total: 40,
    } }));
    const first = await industryResearchApi.projects({ limit: 12 });
    const second = await industryResearchApi.projects({ limit: 12, offset: 12 });
    const searched = await industryResearchApi.projects({ limit: 12, query: '  华懋科技  ' });
    const completed = await industryResearchApi.projects({ limit: 12, query: '华懋科技', status: 'completed' });
    const repeat = await industryResearchApi.projects({ limit: 12, query: '华懋科技' });
    expect(new Set([first, second, searched, completed]).size).toBe(4);
    expect(repeat).toBe(searched);
    expect(apiClient.get).toHaveBeenCalledTimes(4);
  });

  it('explicit searches refresh cached results and propagate failure so the page retains its successful state', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [], total: 0 } });
    await industryResearchApi.projects({ query: '新能源' });
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('temporary failure'));
    await expect(industryResearchApi.projects({ query: '新能源', force: true })).rejects.toThrow('temporary failure');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });
});
