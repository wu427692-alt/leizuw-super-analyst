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

  it('retrieves audio as file-level rows and packages selected files in a persistent background task', async () => {
    const archive = new Blob(['zip']);
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [{ asset_id: 'topic-1:audio-1', topic_id: 'topic-1', file_id: 'audio-1', name: '交流.mp3' }], total: 1, page: 1, page_size: 20 },
    });
    const files = await essayRadarApi.audioFiles({ days: 30, query: '交流', page: 1, pageSize: 20 });
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/financial-data/research-notes/audio-files', {
      params: { days: 30, query: '交流', page: 1, page_size: 20 },
    });
    expect(files.items[0].assetId).toBe('topic-1:audio-1');

    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      task_id: 'audio-task-1', status: 'queued', phase: 'queued', progress: 0,
      total_files: 1, completed_files: 0, downloaded_bytes: 0, total_bytes: 10,
      archive_bytes: 0, message: '已提交', created_at: '2026-08-24', updated_at: '2026-08-24', expires_at: '2026-08-26',
    } });
    const submitted = await essayRadarApi.startAudioBatchTask([{ topicId: 'topic-1', fileId: 'audio-1' }]);
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/financial-data/research-notes/audio-files/batch-download-tasks', {
      items: [{ topic_id: 'topic-1', file_id: 'audio-1' }],
    });
    expect(submitted.taskId).toBe('audio-task-1');

    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: {
      task_id: 'audio-task-1', status: 'completed', phase: 'completed', progress: 100,
      total_files: 1, completed_files: 1, downloaded_bytes: 10, total_bytes: 10,
      archive_bytes: 10, message: '完成', created_at: '2026-08-24', updated_at: '2026-08-24', expires_at: '2026-08-26',
    } });
    expect((await essayRadarApi.audioBatchTask('audio-task-1')).completedFiles).toBe(1);

    const progress = vi.fn();
    vi.mocked(apiClient.get).mockImplementationOnce(async (_url, config) => {
      config?.onDownloadProgress?.({ loaded: 10, total: 10 } as never);
      return { data: archive };
    });
    const downloaded = await essayRadarApi.downloadAudioBatchTask('audio-task-1', progress);
    expect(progress).toHaveBeenCalledWith({ loaded: 10, total: 10, percent: 100 });
    expect(downloaded).toBe(archive);
  });

  it('submits selected recordings for background transcription and downloads the memo', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: {
      configured: true, transcription_configured: true, analysis_configured: true,
      max_files: 8, max_file_mb: 25, message: '可提交',
    } });
    expect((await essayRadarApi.audioAnalysisCapability()).maxFiles).toBe(8);

    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {
      task_id: 'audio-analysis-1', status: 'queued', phase: 'queued', progress: 0,
      message: '已提交', total_files: 1, completed_files: 0,
      created_at: '2026-08-28', updated_at: '2026-08-28', expires_at: '2026-09-04',
    } });
    const task = await essayRadarApi.startAudioAnalysisTask(
      [{ topicId: 'topic-1', fileId: 'audio-1' }],
      { title: '公司录音纪要', focus: '业绩', hotwords: ['CPO'], speakerCount: 3 },
    );
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/financial-data/research-notes/audio-analysis/tasks', {
      items: [{ topic_id: 'topic-1', file_id: 'audio-1' }], title: '公司录音纪要', focus: '业绩', hotwords: ['CPO'], speaker_count: 3,
    });
    expect(task.taskId).toBe('audio-analysis-1');

    const document = new Blob(['docx']);
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: document });
    expect(await essayRadarApi.downloadAudioAnalysis('audio-analysis-1', 'docx')).toBe(document);
    expect(apiClient.get).toHaveBeenLastCalledWith(
      '/api/v1/financial-data/research-notes/audio-analysis/tasks/audio-analysis-1/download',
      { params: { format: 'docx' }, responseType: 'blob', timeout: 600000 },
    );
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
