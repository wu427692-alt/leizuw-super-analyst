import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import apiClient from '../index';
import { dataAcquisitionApi } from '../dataAcquisition';
import type { AcquisitionPlan } from '../../types/dataAcquisition';

const plan: AcquisitionPlan = {
  title: '测试取数', objective: '获取行情', model: 'test', generatedAt: '2026-08-24T00:00:00Z',
  outputFormats: ['json', 'zip'], caveats: [], includeFiles: false,
  scope: { symbols: ['603306.SH'], companyNames: ['华懋科技'], keywords: [], startDate: '', endDate: '', marketWide: false },
  tasks: [{ id: 'market', source: 'tushare', resource: 'daily', label: '日线', reason: '', params: {}, fields: [] }],
};

describe('dataAcquisitionApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('submits acquisition as a background task instead of a long-held request', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      task_id: 'acq-one', status: 'queued', progress: 0, phase: 'queued', message: '已排队',
      completed_tasks: 0, total_tasks: 1, tasks: [], created_at: 'now', updated_at: 'now',
    } });

    const result = await dataAcquisitionApi.runAsync('获取行情', plan);

    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/data-acquisition/run-async', expect.objectContaining({ request: '获取行情' }));
    expect(result.taskId).toBe('acq-one');
    expect(result.totalTasks).toBe(1);
  });

  it('reports download progress from actual transferred bytes', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      config?.onDownloadProgress?.({ loaded: 50, total: 200 } as never);
      return { data: new Blob(['package']) };
    });
    const updates: Array<{ loaded: number; total?: number; percent?: number }> = [];

    await dataAcquisitionApi.download('job-one', (progress) => updates.push(progress));

    expect(updates).toEqual([{ loaded: 50, total: 200, percent: 25 }]);
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/data-acquisition/jobs/job-one/download', expect.objectContaining({ responseType: 'blob' }));
  });
});
