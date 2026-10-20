import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { essayRadarApi } from '../../api/essayRadar';
import { useUserAccess } from '../../contexts/UserAccessContext';
import type { EssayAnalysis, EssayAudioAnalysisTask, EssayAudioBatchTask, EssayAudioFile } from '../../types/essayRadar';
import EssayRadarPage from '../EssayRadarPage';

vi.mock('../../api/essayRadar', () => ({ essayRadarApi: {
  list: vi.fn(), exportFeed: vi.fn(), exportSelected: vi.fn(),
  audioFiles: vi.fn(), startAudioBatchTask: vi.fn(), audioBatchTask: vi.fn(),
  audioAnalysisCapability: vi.fn(), audioAnalysisTasks: vi.fn(), audioAnalysisTask: vi.fn(),
  audioAnalysisTranscript: vi.fn(), startAudioAnalysisTask: vi.fn(), retryAudioAnalysisTask: vi.fn(),
  downloadAudioTranscript: vi.fn(), downloadSelectedAudioTranscripts: vi.fn(), downloadAudioAnalysis: vi.fn(),
  status: vi.fn(), historicalBacklog: vi.fn(),
} }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (reason: unknown) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

function auth(id = 1) {
  return {
    accessEnabled: true, loggedIn: true, user: { id, name: `用户 ${id}`, status: 'approved' },
    authMethod: 'password', isLoading: false, register: vi.fn(), login: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
    registrationMode: 'approval' as const, smsLoginEnabled: false, sendSms: vi.fn(), smsLogin: vi.fn(), bindPhone: vi.fn(),
  };
}

function essay(topicId: string, title: string): EssayAnalysis {
  return {
    topicId, status: 'completed', summary: `${title}摘要`, primaryCategory: 'company_research', sentiment: 'neutral',
    importanceScore: 70, confidenceScore: 0.8, tags: [], industries: [], themes: [], stockMentions: [], keyPoints: [],
    catalysts: [], risks: [], evidence: [], contradictions: [], falsificationConditions: [], monitoringPoints: [],
    earningsImpact: '', valuationImpact: '', sourceQuality: 'test', noveltyScore: 60, informationType: 'research',
    note: { title, content: `${title}原文`, groupName: '测试星球', files: [], images: [] },
  };
}

function audio(assetId: string, name: string, overrides: Partial<EssayAudioFile> = {}): EssayAudioFile {
  return {
    assetId, topicId: `topic-${assetId}`, fileId: `file-${assetId}`, name, groupName: '测试星球',
    noteTitle: name, createdAt: '2026-09-04T01:00:00Z', ...overrides,
  };
}

const timestamps = { createdAt: '2026-09-04T01:00:00Z', updatedAt: '2026-09-04T01:01:00Z', expiresAt: '2026-09-06T01:00:00Z' };

function transcriptTask(taskId = 'transcript-task'): EssayAudioAnalysisTask {
  return {
    ...timestamps, taskId, status: 'completed', phase: 'completed', progress: 100, message: '逐字稿已完成',
    generateMemo: false, totalFiles: 2, completedFiles: 2,
    transcriptArtifacts: [
      { fileId: 'file-a', filename: '文件 A.mp3', lineCount: 1 },
      { fileId: 'file-b', filename: '文件 B.mp3', lineCount: 1 },
    ],
    result: { title: '测试逐字稿', transcriptOnly: true },
  };
}

function batchTask(taskId: string, message: string): EssayAudioBatchTask {
  return {
    ...timestamps, taskId, status: 'completed', phase: 'completed', progress: 100, message,
    totalFiles: 1, completedFiles: 1, downloadedBytes: 10, totalBytes: 10, archiveBytes: 10,
  };
}

function mount(path = '/essay-radar/feed') {
  return render(<MemoryRouter initialEntries={[path]}><EssayRadarPage /></MemoryRouter>);
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

async function advance(milliseconds: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

describe('机构段子与录音检索并发和身份隔离', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
    localStorage.clear();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(useUserAccess).mockReturnValue(auth());
    vi.mocked(essayRadarApi.list).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayRadarApi.audioAnalysisCapability).mockResolvedValue({
      configured: false, transcriptionConfigured: false, analysisConfigured: false,
      maxFiles: 8, maxFileMb: 512, message: '测试环境不执行外部任务',
    });
    vi.mocked(essayRadarApi.status).mockRejectedValue(new Error('status unavailable'));
    vi.mocked(essayRadarApi.historicalBacklog).mockRejectedValue(new Error('backlog unavailable'));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it('小作文旧关键词的慢响应不能覆盖新关键词结果', async () => {
    const oldResult = deferred<Awaited<ReturnType<typeof essayRadarApi.list>>>();
    const newResult = deferred<Awaited<ReturnType<typeof essayRadarApi.list>>>();
    vi.mocked(essayRadarApi.list).mockImplementation((filters) => {
      if (filters.query === '旧关键词') return oldResult.promise;
      if (filters.query === '新关键词') return newResult.promise;
      return Promise.resolve({ items: [essay('baseline', '基线结果')], total: 1, page: 1, pageSize: 20 });
    });
    mount();
    await flush();
    expect(screen.getByText('基线结果')).toBeVisible();

    fireEvent.change(screen.getByLabelText('搜索小作文'), { target: { value: '旧关键词' } });
    await advance(350);
    await advance(30_000);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(2);
    fireEvent.change(screen.getByLabelText('搜索小作文'), { target: { value: '新关键词' } });
    await advance(350);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(3);
    await act(async () => { newResult.resolve({ items: [essay('new', '新关键词结果')], total: 1, page: 1, pageSize: 20 }); });
    expect(screen.getByText('新关键词结果')).toBeVisible();
    await act(async () => { oldResult.resolve({ items: [essay('old', '迟到的旧结果')], total: 1, page: 1, pageSize: 20 }); });
    expect(screen.queryByText('迟到的旧结果')).not.toBeInTheDocument();
    expect(screen.getByText('新关键词结果')).toBeVisible();
  });

  it('录音切换关键词时保留旧快照并明确加载，迟到响应不能覆盖新结果', async () => {
    const oldResult = deferred<Awaited<ReturnType<typeof essayRadarApi.audioFiles>>>();
    const newResult = deferred<Awaited<ReturnType<typeof essayRadarApi.audioFiles>>>();
    vi.mocked(essayRadarApi.audioFiles).mockImplementation((filters) => {
      if (filters.query === '旧关键词') return oldResult.promise;
      if (filters.query === '新关键词') return newResult.promise;
      return Promise.resolve({ items: [audio('baseline', '基线录音.mp3')], total: 1, page: 1, pageSize: 20 });
    });
    mount('/essay-radar/feed?tab=audio');
    await flush();
    expect(screen.getByText('基线录音.mp3')).toBeVisible();

    fireEvent.change(screen.getByLabelText('搜索录音文件'), { target: { value: '旧关键词' } });
    await advance(350);
    expect(screen.getByText('正在加载新条件，下方暂时保留上一次结果…')).toBeVisible();
    expect(screen.getByText('基线录音.mp3')).toBeVisible();
    expect(screen.getByLabelText('选择录音 基线录音.mp3')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('搜索录音文件'), { target: { value: '新关键词' } });
    await advance(350);
    await act(async () => { newResult.resolve({ items: [audio('new', '新关键词录音.mp3')], total: 1, page: 1, pageSize: 20 }); });
    expect(screen.getByText('新关键词录音.mp3')).toBeVisible();
    await act(async () => { oldResult.resolve({ items: [audio('old', '迟到旧录音.mp3')], total: 1, page: 1, pageSize: 20 }); });
    expect(screen.queryByText('迟到旧录音.mp3')).not.toBeInTheDocument();
    expect(screen.getByText('新关键词录音.mp3')).toBeVisible();
  });

  it('录音首次未完成显示加载，失败后显示可重试错误而非索引或空结果', async () => {
    const pending = deferred<Awaited<ReturnType<typeof essayRadarApi.audioFiles>>>();
    vi.mocked(essayRadarApi.audioFiles).mockReturnValue(pending.promise);
    mount('/essay-radar/feed?tab=audio');
    await flush();
    expect(screen.getByText('正在检索整个本地库，请稍等…')).toBeVisible();
    expect(screen.queryByText('没有匹配的录音文件')).not.toBeInTheDocument();
    await act(async () => { pending.reject(new Error('timeout')); });
    expect(screen.getByRole('alert')).toHaveTextContent('检索暂未成功');
    expect(screen.getByRole('button', { name: '重试检索' })).toBeEnabled();
    expect(screen.queryByText(/录音文件索引正在加载/)).not.toBeInTheDocument();
    expect(screen.queryByText('正在检索整个本地库，请稍等…')).not.toBeInTheDocument();
    expect(screen.queryByText('没有匹配的录音文件')).not.toBeInTheDocument();
  });

  it.each(['essays', 'audio'] as const)('%s 请求超过三轮刷新仍只在途一份，晚返回可以落地', async mode => {
    const pending = deferred<{ items: []; total: number; page: number; pageSize: number }>();
    const request = vi.mocked(mode === 'audio' ? essayRadarApi.audioFiles : essayRadarApi.list);
    request.mockReturnValue(pending.promise);
    mount(mode === 'audio' ? '/essay-radar/feed?tab=audio' : '/essay-radar/feed');
    await flush();
    await advance(45_000);
    fireEvent.focus(window);
    fireEvent(document, new Event('visibilitychange'));
    await flush();
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.getByText('正在检索整个本地库，请稍等…')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await act(async () => pending.resolve({ items: [], total: 0, page: 1, pageSize: 20 }));
    expect(screen.getByText(mode === 'audio' ? '没有匹配的录音文件' : '没有匹配的小作文')).toBeVisible();
    expect(screen.queryByText('正在检索整个本地库，请稍等…')).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(1);
    await advance(15_000);
    expect(request).toHaveBeenCalledTimes(2);
    expect(essayRadarApi.startAudioBatchTask).not.toHaveBeenCalled();
    expect(essayRadarApi.startAudioAnalysisTask).not.toHaveBeenCalled();
  });

  it.each(['essays', 'audio'] as const)('%s 首次网络失败脱敏显示并可手动重试恢复', async mode => {
    const request = vi.mocked(mode === 'audio' ? essayRadarApi.audioFiles : essayRadarApi.list);
    request.mockRejectedValueOnce(new Error('private SQL /config/.env 原文故障细节'));
    request.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    mount(mode === 'audio' ? '/essay-radar/feed?tab=audio' : '/essay-radar/feed');
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('检索暂未成功');
    expect(screen.getByRole('alert')).toHaveTextContent('服务或网络暂时不可用');
    expect(document.body).not.toHaveTextContent('private SQL');
    expect(document.body).not.toHaveTextContent('准备索引');
    expect(screen.queryByText(/没有匹配的/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全选/取消本页' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText(mode === 'audio' ? '没有匹配的录音文件' : '没有匹配的小作文')).toBeVisible();
  });

  it.each([401, 403, 400, 422])('HTTP %s 暂停定时和焦点重试，手动重试保留全文与小作文筛选语义', async status => {
    vi.mocked(essayRadarApi.list).mockRejectedValue({
      isAxiosError: true, response: { status, data: { detail: 'private validation payload' } },
    });
    mount('/essay-radar/feed?query=%E4%B8%AD%E9%99%85%E6%97%AD%E5%88%9B');
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('检索暂未成功');
    expect(screen.getByRole('alert')).toHaveTextContent(status === 401 || status === 403 ? '登录状态或访问权限需要确认' : '当前请求或服务版本不兼容');
    expect(screen.getByLabelText('自动更新状态')).toHaveTextContent('检索自动刷新已暂停');
    expect(document.body).not.toHaveTextContent('private validation payload');
    const statusCalls = vi.mocked(essayRadarApi.status).mock.calls.length;
    const backlogCalls = vi.mocked(essayRadarApi.historicalBacklog).mock.calls.length;
    await advance(75_000);
    fireEvent.focus(window);
    fireEvent(document, new Event('visibilitychange'));
    await flush();
    expect(essayRadarApi.list).toHaveBeenCalledTimes(1);
    expect(essayRadarApi.status).toHaveBeenCalledTimes(statusCalls);
    expect(essayRadarApi.historicalBacklog).toHaveBeenCalledTimes(backlogCalls);

    vi.mocked(essayRadarApi.list).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await flush();
    expect(essayRadarApi.list).toHaveBeenCalledTimes(2);
    expect(essayRadarApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ query: '中际旭创', queryScope: 'full', analysisStatus: 'essay' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await advance(15_000);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(3);
  });

  it('录音权限失败也停止自动检索，恢复后可重新获取文件', async () => {
    vi.mocked(essayRadarApi.audioFiles).mockRejectedValue({ isAxiosError: true, response: { status: 403 } });
    mount('/essay-radar/feed?tab=audio');
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('登录状态或访问权限需要确认');
    await advance(60_000);
    expect(essayRadarApi.audioFiles).toHaveBeenCalledTimes(1);
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [audio('recovered', '恢复的录音.mp3')], total: 1, page: 1, pageSize: 20 });
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await flush();
    expect(screen.getByLabelText('选择录音 恢复的录音.mp3')).toBeEnabled();
  });

  it.each(['essays', 'audio'] as const)('%s 筛选失败保留旧结果但不能勾选或误提交下载，重试后恢复', async mode => {
    const oldTitle = mode === 'audio' ? '旧录音.mp3' : '旧小作文';
    const newTitle = mode === 'audio' ? '新录音.mp3' : '新小作文';
    const pending = deferred<{ items: []; total: number; page: number; pageSize: number }>();
    if (mode === 'audio') {
      vi.mocked(essayRadarApi.audioFiles).mockImplementation(filters => filters.query
        ? pending.promise
        : Promise.resolve({ items: [audio('old', oldTitle)], total: 1, page: 1, pageSize: 20 }));
    } else {
      vi.mocked(essayRadarApi.list).mockImplementation(filters => filters.query
        ? pending.promise
        : Promise.resolve({ items: [essay('old', oldTitle)], total: 1, page: 1, pageSize: 20 }));
    }
    mount(mode === 'audio' ? '/essay-radar/feed?tab=audio' : '/essay-radar/feed');
    await flush();
    const oldCheckbox = screen.getByLabelText(`${mode === 'audio' ? '选择录音' : '选择小作文'} ${oldTitle}`);
    const downloadLabel = mode === 'audio' ? '提交后台打包' : '下载已选原文 Excel';
    fireEvent.click(oldCheckbox);
    expect(oldCheckbox).toBeChecked();
    fireEvent.change(screen.getByLabelText(mode === 'audio' ? '搜索录音文件' : '搜索小作文'), { target: { value: '新筛选' } });
    // Even the debounce window must not treat the old snapshot as the new search.
    expect(oldCheckbox).toBeDisabled();
    expect(screen.getByRole('button', { name: downloadLabel })).toBeDisabled();
    await advance(350);
    expect(oldCheckbox).not.toBeChecked();
    await act(async () => pending.reject({ isAxiosError: true, response: { status: 500 } }));
    expect(screen.getByText(oldTitle)).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('不代表当前筛选');
    expect(oldCheckbox).toBeDisabled();
    expect(screen.getByRole('button', { name: '全选/取消本页' })).toBeDisabled();
    expect(screen.getByRole('button', { name: downloadLabel })).toBeDisabled();
    fireEvent.click(oldCheckbox);
    fireEvent.click(screen.getByRole('button', { name: downloadLabel }));
    if (mode === 'essays') {
      expect(screen.getByRole('button', { name: '导出当前搜索结果 Excel' })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: '导出当前搜索结果 Excel' }));
      vi.mocked(essayRadarApi.list).mockResolvedValue({ items: [essay('new', newTitle)], total: 1, page: 1, pageSize: 20 });
    } else {
      vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [audio('new', newTitle)], total: 1, page: 1, pageSize: 20 });
    }
    expect(essayRadarApi.exportSelected).not.toHaveBeenCalled();
    expect(essayRadarApi.exportFeed).not.toHaveBeenCalled();
    expect(essayRadarApi.startAudioBatchTask).not.toHaveBeenCalled();
    expect(essayRadarApi.startAudioAnalysisTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await flush();
    expect(screen.queryByText(oldTitle)).not.toBeInTheDocument();
    const newCheckbox = screen.getByLabelText(`${mode === 'audio' ? '选择录音' : '选择小作文'} ${newTitle}`);
    expect(newCheckbox).toBeEnabled();
    expect(newCheckbox).not.toBeChecked();
    fireEvent.click(newCheckbox);
    expect(screen.getByRole('button', { name: downloadLabel })).toBeEnabled();
  });

  it('暂时失败可以在下一轮自动恢复，失败期间不冒充加载', async () => {
    vi.mocked(essayRadarApi.list).mockRejectedValueOnce({ isAxiosError: true, response: { status: 503 } });
    vi.mocked(essayRadarApi.list).mockResolvedValue({ items: [essay('recovered', '自动恢复结果')], total: 1, page: 1, pageSize: 20 });
    mount();
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('检索暂未成功');
    expect(screen.queryByText('正在检索整个本地库，请稍等…')).not.toBeInTheDocument();
    await advance(15_000);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(2);
    expect(screen.getByText('自动恢复结果')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('同条件后台刷新失败也提示错误并禁用此前勾选，不显示成功命中数', async () => {
    vi.mocked(essayRadarApi.list).mockResolvedValueOnce({ items: [essay('saved', '上次检索结果')], total: 1, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.list).mockRejectedValue(new Error('temporary failure'));
    mount();
    await flush();
    fireEvent.click(screen.getByLabelText('选择小作文 上次检索结果'));
    await advance(15_000);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('alert')).toHaveTextContent('不代表当前筛选');
    expect(screen.getByText('上次检索结果')).toBeVisible();
    expect(screen.getByLabelText('选择小作文 上次检索结果')).toBeDisabled();
    expect(screen.getByRole('button', { name: '下载已选原文 Excel' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '导出当前搜索结果 Excel' })).toBeDisabled();
    expect(screen.queryByText(/当前条件命中/)).not.toBeInTheDocument();
    expect(screen.queryByText('正在刷新当前结果…')).not.toBeInTheDocument();
  });

  it('旧关键词迟到的权限失败不暂停已成功的新关键词刷新', async () => {
    const oldResult = deferred<Awaited<ReturnType<typeof essayRadarApi.list>>>();
    vi.mocked(essayRadarApi.list).mockImplementation(filters => filters.query === '旧关键词'
      ? oldResult.promise
      : Promise.resolve({ items: [essay('new', '新筛选有效结果')], total: 1, page: 1, pageSize: 20 }));
    mount('/essay-radar/feed?query=%E6%97%A7%E5%85%B3%E9%94%AE%E8%AF%8D');
    await flush();
    fireEvent.change(screen.getByLabelText('搜索小作文'), { target: { value: '新关键词' } });
    await advance(350);
    expect(screen.getByText('新筛选有效结果')).toBeVisible();
    await act(async () => oldResult.reject({ isAxiosError: true, response: { status: 403 } }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await advance(15_000);
    expect(essayRadarApi.list).toHaveBeenCalledTimes(3);
    expect(essayRadarApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ query: '新关键词' }));
  });

  it('相同筛选跨页保留勾选并复用预取，不把分页当作换筛选', async () => {
    vi.mocked(essayRadarApi.list).mockImplementation(async filters => ({
      items: [essay(`page-${filters.page}`, `第${filters.page}页资料`)], total: 40, page: filters.page ?? 1, pageSize: 20,
    }));
    mount();
    await flush();
    fireEvent.click(screen.getByLabelText('选择小作文 第1页资料'));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await flush();
    expect(screen.getByText('第2页资料')).toBeVisible();
    expect(screen.getByRole('button', { name: '下载已选原文 Excel' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    await flush();
    expect(screen.getByLabelText('选择小作文 第1页资料')).toBeChecked();
    expect(essayRadarApi.list).toHaveBeenCalledTimes(2);
  });

  it('任务恢复键按用户隔离，切换账号会卸载旧状态且旧响应不能回写', async () => {
    const oldTask = deferred<EssayAudioBatchTask>();
    localStorage.setItem('dsa:essay-radar:v2:user:1:batch-task', 'batch-user-1');
    localStorage.setItem('dsa:essay-radar:v2:user:2:batch-task', 'batch-user-2');
    vi.mocked(essayRadarApi.audioBatchTask).mockImplementation((taskId) => taskId === 'batch-user-1'
      ? oldTask.promise
      : Promise.resolve(batchTask('batch-user-2', '账号二任务')));
    const view = mount('/essay-radar/feed?tab=audio');
    await flush();
    expect(essayRadarApi.audioBatchTask).toHaveBeenCalledWith('batch-user-1');

    vi.mocked(useUserAccess).mockReturnValue(auth(2));
    view.rerender(<MemoryRouter initialEntries={['/essay-radar/feed?tab=audio']}><EssayRadarPage /></MemoryRouter>);
    await flush();
    expect(essayRadarApi.audioBatchTask).toHaveBeenCalledWith('batch-user-2');
    expect(screen.getByText('账号二任务')).toBeVisible();
    await act(async () => { oldTask.resolve(batchTask('batch-user-1', '账号一迟到任务')); });
    expect(screen.queryByText('账号一迟到任务')).not.toBeInTheDocument();
    expect(screen.getByText('账号二任务')).toBeVisible();
  });

  it('浏览器禁用 localStorage 时仍可检索和提交后台打包', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage blocked'); });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage blocked'); });
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [audio('one', '可提交录音.mp3')], total: 1, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.startAudioBatchTask).mockResolvedValue(batchTask('batch-safe', '打包已完成'));
    mount('/essay-radar/feed?tab=audio');
    await flush();
    fireEvent.click(screen.getByLabelText('选择录音 可提交录音.mp3'));
    fireEvent.click(screen.getByRole('button', { name: '提交后台打包' }));
    await flush();
    expect(essayRadarApi.startAudioBatchTask).toHaveBeenCalledWith([{ topicId: 'topic-one', fileId: 'file-one' }]);
    expect(screen.getByText(/已提交 1 个录音到后台打包/)).toBeVisible();
    expect(get).toHaveBeenCalled();
    expect(set).toHaveBeenCalled();
  });

  it('列表默认小作文范围与导出范围一致', async () => {
    vi.mocked(essayRadarApi.list).mockResolvedValue({ items: [essay('one', '可导出小作文')], total: 1, page: 1, pageSize: 20 });
    vi.mocked(essayRadarApi.exportFeed).mockResolvedValue(new Blob(['xlsx']));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
    mount();
    await flush();
    expect(essayRadarApi.list).toHaveBeenCalledWith(expect.objectContaining({ analysisStatus: 'essay' }));
    fireEvent.click(screen.getByRole('button', { name: '导出当前搜索结果 Excel' }));
    await flush();
    expect(essayRadarApi.exportFeed).toHaveBeenCalledWith(expect.objectContaining({ analysisStatus: 'essay' }));
  });
});

describe('录音任务详情与逐字稿切换', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
    localStorage.clear();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.mocked(useUserAccess).mockReturnValue(auth());
    vi.mocked(essayRadarApi.status).mockRejectedValue(new Error('status unavailable'));
    vi.mocked(essayRadarApi.historicalBacklog).mockRejectedValue(new Error('backlog unavailable'));
    vi.mocked(essayRadarApi.audioAnalysisCapability).mockResolvedValue({
      configured: false, transcriptionConfigured: true, analysisConfigured: false,
      maxFiles: 8, maxFileMb: 512, message: '仅测试逐字稿',
    });
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it('活动任务不在历史区重复，任务详情点击有加载和错误状态', async () => {
    const active = transcriptTask('active-task');
    const history = { ...transcriptTask('history-task'), title: '历史任务' };
    const detail = deferred<EssayAudioAnalysisTask>();
    localStorage.setItem('dsa:essay-radar:v2:user:1:analysis-task', active.taskId);
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockResolvedValue({ items: [active, active, history], total: 3 });
    vi.mocked(essayRadarApi.audioAnalysisTask).mockImplementation((taskId) => taskId === active.taskId
      ? Promise.resolve(active)
      : detail.promise);
    mount('/essay-radar/feed?tab=audio');
    await flush();
    expect(screen.queryByRole('button', { name: /录音转写 · 2 个文件/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /历史任务/ }));
    expect(screen.getByText('正在读取任务详情…')).toBeVisible();
    expect(screen.getByRole('button', { name: /历史任务/ })).toBeDisabled();
    await act(async () => { detail.reject(new Error('详情读取超时')); });
    expect(screen.getByRole('alert')).toHaveTextContent('详情读取超时');
  });

  it('从录音行打开多文件任务时定位对应文件，快速切换后旧逐字稿不能覆盖', async () => {
    const task = transcriptTask();
    const firstB = Promise.resolve({ fileId: 'file-b', filename: '文件 B.mp3', text: 'B 初始', lines: [{ timestamp: '00:01', speaker: 'B', text: 'B 初始' }] });
    const slowA = deferred<Awaited<ReturnType<typeof essayRadarApi.audioAnalysisTranscript>>>();
    const latestB = deferred<Awaited<ReturnType<typeof essayRadarApi.audioAnalysisTranscript>>>();
    let bCalls = 0;
    vi.mocked(essayRadarApi.audioFiles).mockResolvedValue({
      items: [audio('b', '文件 B.mp3', { fileId: 'file-b', transcribed: true, transcriptTaskId: task.taskId })],
      total: 1, page: 1, pageSize: 20,
    });
    vi.mocked(essayRadarApi.audioAnalysisTasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayRadarApi.audioAnalysisTask).mockResolvedValue(task);
    vi.mocked(essayRadarApi.audioAnalysisTranscript).mockImplementation((_taskId, fileId) => {
      if (fileId === 'file-a') return slowA.promise;
      bCalls += 1;
      return bCalls === 1 ? firstB : latestB.promise;
    });
    mount('/essay-radar/feed?tab=audio');
    await flush();
    fireEvent.click(screen.getByRole('button', { name: '查看原文' }));
    await flush();
    expect(screen.getByText('B 初始')).toBeVisible();
    expect(screen.getByRole('button', { name: /文件 B\.mp3/ })).toHaveClass('is-active');

    fireEvent.click(screen.getByRole('button', { name: /文件 A\.mp3/ }));
    await flush();
    fireEvent.click(screen.getByRole('button', { name: /文件 B\.mp3/ }));
    await flush();
    await act(async () => { latestB.resolve({ fileId: 'file-b', filename: '文件 B.mp3', text: 'B 最新', lines: [{ timestamp: '00:02', speaker: 'B', text: 'B 最新' }] }); });
    expect(screen.getByText('B 最新')).toBeVisible();
    await act(async () => { slowA.resolve({ fileId: 'file-a', filename: '文件 A.mp3', text: 'A 迟到', lines: [{ timestamp: '00:03', speaker: 'A', text: 'A 迟到' }] }); });
    expect(screen.queryByText('A 迟到')).not.toBeInTheDocument();
    expect(screen.getByText('B 最新')).toBeVisible();
  });
});
