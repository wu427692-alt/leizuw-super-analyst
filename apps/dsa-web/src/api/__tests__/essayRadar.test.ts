import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import apiClient from '../index';
import { essayRadarApi } from '../essayRadar';

describe('essayRadarApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('searches the full raw-note feed and forwards AI status separately', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20, scope: 'all_stored_notes' },
    });

    const result = await essayRadarApi.list({
      days: 0,
      query: '华懋 科技',
      analysisStatus: 'uncompleted',
      knownTotal: 240,
      minImportance: 0,
      page: 1,
      pageSize: 20,
    });

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/essay-radar/feed', {
      params: expect.objectContaining({
        days: 0,
        query: '华懋 科技',
        analysis_status: 'uncompleted',
        known_total: 240,
        min_importance: undefined,
      }),
    });
    expect(result.scope).toBe('all_stored_notes');
    expect(result.pageSize).toBe(20);
  });

  it('allows local historical queue writes to finish without the global 30 second timeout', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { queue: { selected: 100 }, backlog: { total_notes: 100 }, worker: { running: true } },
    });

    await essayRadarApi.backfillCount(100, 'newest');

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/essay-radar/backfill-count',
      { count: 100, order: 'newest' },
      { timeout: 180000 },
    );
  });

  it('exports every feed match with the active search filters', async () => {
    const workbook = new Blob(['xlsx']);
    vi.mocked(apiClient.get).mockResolvedValue({ data: workbook });

    const result = await essayRadarApi.exportFeed({
      days: 30,
      query: '华懋 科技',
      analysisStatus: 'completed',
      sentiment: 'bullish',
      minImportance: 75,
    });

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/essay-radar/feed/export', {
      params: expect.objectContaining({
        days: 30,
        query: '华懋 科技',
        analysis_status: 'completed',
        sentiment: 'bullish',
        min_importance: 75,
      }),
      responseType: 'blob',
      timeout: 300000,
    });
    expect(result).toBe(workbook);
  });

  it('retrieves audio as file-level rows and batch downloads selected files', async () => {
    const archive = new Blob(['zip']);
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [{ asset_id: 'topic-1:audio-1', topic_id: 'topic-1', file_id: 'audio-1', name: '交流.mp3' }], total: 1, page: 1, page_size: 20 },
    });
    const files = await essayRadarApi.audioFiles({ days: 30, query: '交流', page: 1, pageSize: 20 });
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/financial-data/research-notes/audio-files', {
      params: { days: 30, query: '交流', page: 1, page_size: 20 },
    });
    expect(files.items[0].assetId).toBe('topic-1:audio-1');

    vi.mocked(apiClient.post).mockResolvedValue({ data: archive });
    const downloaded = await essayRadarApi.downloadSelectedAudio([{ topicId: 'topic-1', fileId: 'audio-1' }]);
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/financial-data/research-notes/audio-files/batch-download', {
      items: [{ topic_id: 'topic-1', file_id: 'audio-1' }],
    }, { responseType: 'blob', timeout: 600000 });
    expect(downloaded).toBe(archive);
  });

  it('downloads only checked essays as an Excel workbook', async () => {
    const workbook = new Blob(['xlsx']);
    vi.mocked(apiClient.post).mockResolvedValue({ data: workbook });

    const result = await essayRadarApi.exportSelected(['topic-1', 'topic-2']);

    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/essay-radar/feed/export-selected', {
      topic_ids: ['topic-1', 'topic-2'],
    }, { responseType: 'blob', timeout: 300000 });
    expect(result).toBe(workbook);
  });

  it('preserves recent-library activity counts when numeric snake-case keys are converted', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        total_notes: 82080,
        notes_24h: 432,
        notes_7d: 2052,
        notes_30d: 7898,
      },
    });

    const result = await essayRadarApi.historicalBacklog();

    expect(result.totalNotes).toBe(82080);
    expect(result.notes24h).toBe(432);
    expect(result.notes7d).toBe(2052);
    expect(result.notes30d).toBe(7898);
  });

  it('loads one locally stored original note for the in-page reader', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        topic_id: 'topic/88',
        group_name: '测试星球',
        title: '华懋订单跟踪',
        content: '完整正文',
        images: [],
        files: [],
      },
    });

    const result = await essayRadarApi.note('topic/88');

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/v1/financial-data/research-notes/topic%2F88',
    );
    expect(result.topicId).toBe('topic/88');
    expect(result.groupName).toBe('测试星球');
  });
});
