import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { essayExportsApi } from '../essayExports';

vi.mock('../index', () => ({
  default: { get: vi.fn() },
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

describe('essayExportsApi owner-scoped task ledger', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses the shared authenticated client without narrowing to one workspace and preserves nullable progress', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [{
      task_id: 'export-1', workspace_id: 'meeting-older', status: 'indexing', progress: null,
      matched_total: null, target_total: null, requested_limit: null, retrieved_count: 234,
      packaged_count: 0, bytes_written: 81_000, bytes_total: null,
    }], total: 1 } });
    const result = await essayExportsApi.tasks();
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/financial-data/essays/package/tasks', {
      headers: { 'X-DSA-Route-Load': 'background' },
    });
    expect(result).toEqual({ items: [{
      taskId: 'export-1', workspaceId: 'meeting-older', status: 'indexing', progress: null,
      matchedTotal: null, targetTotal: null, requestedLimit: null, retrievedCount: 234,
      packagedCount: 0, bytesWritten: 81_000, bytesTotal: null,
    }], total: 1 });
  });

  it('fetches each new owner/page request and propagates a refresh failure instead of cached success', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [], total: 0 } })
      .mockRejectedValueOnce(new Error('refresh failed'));
    await expect(essayExportsApi.tasks()).resolves.toEqual({ items: [], total: 0 });
    await expect(essayExportsApi.tasks()).rejects.toThrow('refresh failed');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });
});
