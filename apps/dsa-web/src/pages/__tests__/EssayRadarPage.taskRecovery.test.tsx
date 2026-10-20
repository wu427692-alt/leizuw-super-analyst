import { act, cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { essayRadarApi } from '../../api/essayRadar';
import { useUserAccess } from '../../contexts/UserAccessContext';
import type { EssayAudioAnalysisTask, EssayAudioBatchTask } from '../../types/essayRadar';
import EssayRadarPage from '../EssayRadarPage';

vi.mock('../../api/essayRadar', () => ({ essayRadarApi: {
  audioBatchTask: vi.fn(), audioAnalysisTask: vi.fn(), audioAnalysisTasks: vi.fn(),
  audioAnalysisCapability: vi.fn(), audioFiles: vi.fn(), status: vi.fn(), historicalBacklog: vi.fn(),
} }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));

const batchKey = 'dsa:essay-radar:v2:user:1:batch-task';
const analysisKey = 'dsa:essay-radar:v2:user:1:analysis-task';
const timestamps = { createdAt: '2026-09-04T01:00:00Z', updatedAt: '2026-09-04T01:01:00Z', expiresAt: '2026-09-06T01:00:00Z' };
const batch: EssayAudioBatchTask = {
  ...timestamps, taskId: 'batch-1', status: 'running', phase: 'downloading', progress: 45,
  message: '正在打包所选录音', totalFiles: 2, completedFiles: 1,
  downloadedBytes: 1024, totalBytes: 2048, archiveBytes: 0,
};
const analysis: EssayAudioAnalysisTask = {
  ...timestamps, taskId: 'analysis-1', status: 'running', phase: 'transcribing', progress: 45,
  message: '正在转写所选录音', totalFiles: 2, completedFiles: 1, generateMemo: false,
};

const cases = [
  { name: '录音打包', key: batchKey, mock: () => vi.mocked(essayRadarApi.audioBatchTask), task: batch, interval: 1000, progressLabel: '录音后台下载与打包进度' },
  { name: '录音转写', key: analysisKey, mock: () => vi.mocked(essayRadarApi.audioAnalysisTask), task: analysis, interval: 1500, progressLabel: '录音转写进度' },
] as const;

async function mount() {
  render(<MemoryRouter initialEntries={['/essay-radar/feed?tab=audio']}><EssayRadarPage /></MemoryRouter>);
  await act(async () => { await Promise.resolve(); });
}

async function advance(milliseconds: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

describe.each(cases)('$name任务状态读取恢复', ({ key, mock, task, interval, progressLabel }) => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    localStorage.clear();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(useUserAccess).mockReturnValue({
      accessEnabled: true, loggedIn: true, user: { id: 1, name: '用户 A', status: 'approved' },
      authMethod: 'password', isLoading: false, register: vi.fn(), login: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
      registrationMode: 'approval' as const, smsLoginEnabled: false, sendSms: vi.fn(), smsLogin: vi.fn(), bindPhone: vi.fn(),
    });
    localStorage.setItem(key, task.taskId);
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayRadarApi.audioAnalysisCapability).mockResolvedValue({
      configured: false, transcriptionConfigured: false, analysisConfigured: false,
      maxFiles: 8, maxFileMb: 512, message: '测试不执行语音或 AI 任务',
    });
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.status).mockRejectedValue(new Error('辅助状态暂不可用'));
    vi.mocked(essayRadarApi.historicalBacklog).mockRejectedValue(new Error('辅助统计暂不可用'));
  });

  afterEach(() => { cleanup(); vi.useRealTimers(); localStorage.clear(); });

  it('保留进度与任务编号，短暂超时后恢复完成且不再轮询', async () => {
    const api = mock();
    api.mockResolvedValueOnce(task as never)
      .mockRejectedValueOnce(new Error('timeout of 30000ms exceeded'))
      .mockResolvedValue({ ...task, status: 'completed', phase: 'completed', progress: 100, message: '恢复后处理完成' } as never);
    await mount();
    expect(screen.getByRole('progressbar', { name: progressLabel })).toHaveAttribute('aria-valuenow', '45');

    await advance(interval);
    expect(api).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem(key)).toBe(task.taskId);
    expect(screen.getByRole('progressbar', { name: progressLabel })).toHaveAttribute('aria-valuenow', '45');

    await advance(4999);
    expect(api).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(api).toHaveBeenLastCalledWith(task.taskId);
    expect(screen.getByRole('progressbar', { name: progressLabel })).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText('恢复后处理完成')).toBeInTheDocument();
    await advance(30_000);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it.each([404, 410])('仅在服务确认%s时清理失效任务并停止重试', async (status) => {
    const api = mock();
    api.mockResolvedValueOnce(task as never).mockRejectedValue({
      isAxiosError: true, response: { status },
    });
    await mount();
    await advance(interval);
    expect(localStorage.getItem(key)).toBeNull();
    expect(screen.queryByRole('progressbar', { name: progressLabel })).not.toBeInTheDocument();
    expect(screen.getByText(/已不存在或已过期/)).toBeInTheDocument();
    await advance(30_000);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('服务明确返回失败时保留原因与任务记录，不继续轮询', async () => {
    const api = mock();
    api.mockResolvedValue({ ...task, status: 'failed', phase: 'failed', error: '源文件已不可用' } as never);
    await mount();
    expect(localStorage.getItem(key)).toBe(task.taskId);
    expect(screen.getByText('源文件已不可用')).toBeInTheDocument();
    await advance(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('首次网络读取失败仍保留任务，并在隐藏页面时暂停网络重试', async () => {
    const api = mock();
    api.mockRejectedValueOnce(new Error('Network Error')).mockResolvedValue(task as never);
    await mount();
    expect(localStorage.getItem(key)).toBe(task.taskId);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await advance(10_000);
    expect(api).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await advance(5000);
    expect(api).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('progressbar', { name: progressLabel })).toHaveAttribute('aria-valuenow', '45');
  });

  it('离开页面后不再追加网络重试', async () => {
    const api = mock();
    api.mockRejectedValue(new Error('Network Error'));
    await mount();
    cleanup();
    await advance(30_000);
    expect(api).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(key)).toBe(task.taskId);
  });
});
