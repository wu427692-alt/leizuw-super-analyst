import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { industryResearchApi } from '../../api/industryResearch';
import type { IndustryResearchBlueprint, IndustryResearchProject } from '../../types/industryResearch';
import IndustryResearchPage from '../IndustryResearchPage';

vi.mock('../../api/industryResearch', () => ({ industryResearchApi: {
  blueprint: vi.fn(), projects: vi.fn(), project: vi.fn(), createProject: vi.fn(),
  downloadUrl: (id: string, format: string) => `/test/${id}.${format}`,
} }));
vi.mock('../../hooks/usePageActivationRefresh', () => ({ usePageActivationRefresh: vi.fn() }));
vi.mock('../../components/common', () => ({ AppPage: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));

const running: IndustryResearchProject = {
  projectId: 'research-1', topic: '光模块', researchType: 'industry', objective: '测试报告读取',
  lookbackDays: 730, status: 'analyzing', progress: 65, stage: 'writing', message: '正在撰写章节',
  query: {}, createdAt: '2026-09-04T01:00:00Z', updatedAt: '2026-09-04T01:01:00Z',
  report: { oneSentence: '研究摘要先返回' },
};
const complete: IndustryResearchProject = {
  ...running, status: 'completed', progress: 100, stage: 'completed', message: '完整研究已完成',
  updatedAt: '2026-09-04T01:02:00Z',
  report: {
    oneSentence: '完整研究结论', executiveSummary: '这是已经完成的详细报告摘要。',
    chapters: [{ chapterId: 'industry-chain', title: '产业链深度拆解', bodyMarkdown: '这里是完整报告正文，不能被任务列表的一句话摘要替代。', evidenceIds: [], openQuestions: [], charCount: 29 }],
  },
};
const completeSummary: IndustryResearchProject = { ...complete, report: { oneSentence: '完整研究结论' } };

async function mount(path = '/industry-research?project=research-1', strict = false) {
  const page = <MemoryRouter initialEntries={[path]}><IndustryResearchPage /></MemoryRouter>;
  render(strict ? <StrictMode>{page}</StrictMode> : page);
  await act(async () => { await Promise.resolve(); });
}

async function advance(milliseconds: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

async function refreshList(items: IndustryResearchProject[]) {
  vi.mocked(industryResearchApi.projects).mockResolvedValue({ items, total: items.length });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '刷新任务' })); });
}

describe('IndustryResearchPage 完整报告读取', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.stubGlobal('requestAnimationFrame', vi.fn());
    vi.mocked(industryResearchApi.blueprint).mockImplementation(() => new Promise<IndustryResearchBlueprint>(() => undefined));
    vi.mocked(industryResearchApi.projects).mockResolvedValue({ items: [running], total: 1 });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('列表先完成也继续读取完整详情，取得章节后停止轮询且不被后续摘要覆盖', async () => {
    const detail = vi.mocked(industryResearchApi.project);
    detail.mockResolvedValueOnce(running).mockResolvedValue(complete);
    await mount();
    await refreshList([completeSummary]);
    expect(screen.queryByText('这里是完整报告正文，不能被任务列表的一句话摘要替代。')).not.toBeInTheDocument();

    await advance(3_000);
    expect(detail).toHaveBeenCalledTimes(2);
    expect(screen.getByText('这里是完整报告正文，不能被任务列表的一句话摘要替代。')).toBeInTheDocument();
    await refreshList([completeSummary]);
    await advance(12_000);
    expect(detail).toHaveBeenCalledTimes(2);
    expect(screen.getByText('这里是完整报告正文，不能被任务列表的一句话摘要替代。')).toBeInTheDocument();
    expect(industryResearchApi.createProject).not.toHaveBeenCalled();
  });

  it('终态详情短暂超时后保留摘要并继续恢复，不把接口超时当任务失败', async () => {
    const detail = vi.mocked(industryResearchApi.project);
    detail.mockResolvedValueOnce(running).mockRejectedValueOnce(new Error('Network Error')).mockResolvedValue(complete);
    await mount();
    await refreshList([completeSummary]);
    await advance(3_000);
    expect(screen.queryByText('研究任务没有完成')).not.toBeInTheDocument();
    expect(screen.getAllByText('研究摘要先返回').length).toBeGreaterThan(0);
    await advance(3_000);
    expect(detail).toHaveBeenCalledTimes(3);
    expect(screen.getByText('这里是完整报告正文，不能被任务列表的一句话摘要替代。')).toBeInTheDocument();
  });

  it('完整详情允许有限完成且没有报告，不会为了等章节无限请求', async () => {
    const limited: IndustryResearchProject = { ...complete, status: 'limited', report: undefined, message: '资料覆盖有限，研究已结束' };
    const detail = vi.mocked(industryResearchApi.project);
    detail.mockResolvedValueOnce(running).mockResolvedValue(limited);
    await mount();
    await refreshList([limited]);
    await advance(3_000);
    expect(screen.getByText(/质量门没有允许发布完整报告/)).toBeInTheDocument();
    await advance(12_000);
    expect(detail).toHaveBeenCalledTimes(2);
  });

  it('慢请求不重叠；切换任务后旧请求不能覆盖当前报告', async () => {
    let finishOld!: (value: IndustryResearchProject) => void;
    const second: IndustryResearchProject = {
      ...complete, projectId: 'research-2', topic: '新能源',
      report: { ...complete.report, oneSentence: '新能源独立结论', chapters: [{ chapterId: 'new-energy', title: '新能源研究', bodyMarkdown: '新的课题正文保持可见。', evidenceIds: [], openQuestions: [], charCount: 11 }] },
    };
    const detail = vi.mocked(industryResearchApi.project);
    detail.mockResolvedValueOnce(running)
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockResolvedValue(second);
    await mount();
    await advance(9_000);
    expect(detail).toHaveBeenCalledTimes(2);
    await refreshList([running, { ...second, report: { oneSentence: '新能源独立结论' } }]);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /新能源.*打开报告/ })); });
    expect(screen.getByText('新的课题正文保持可见。')).toBeInTheDocument();
    await act(async () => { finishOld(complete); });
    expect(screen.getByText('新的课题正文保持可见。')).toBeInTheDocument();
    expect(screen.queryByText('这里是完整报告正文，不能被任务列表的一句话摘要替代。')).not.toBeInTheDocument();
  });

  const archive = Array.from({ length: 40 }, (_, index): IndustryResearchProject => ({
    ...completeSummary, projectId: `archive-${index + 1}`, topic: `历史研究第${index + 1}篇`,
  }));
  const counts = { all: 40, completed: 40, running: 0, limited: 0, failed: 0 };
  const detailFor = (projectId: string): IndustryResearchProject => ({
    ...complete, ...archive.find(item => item.projectId === projectId),
    report: { ...complete.report, chapters: [{
      chapterId: projectId, title: `${projectId}历史章节`, bodyMarkdown: `${projectId} 的历史报告全文已读取。`,
      evidenceIds: [], openQuestions: [], charCount: 25,
    }] },
  });
  function mockArchive() {
    vi.mocked(industryResearchApi.projects).mockImplementation(async (options = {}) => {
      const offset = options.offset ?? 0;
      const filtered = archive.filter(item => item.topic.includes(options.query ?? ''));
      return {
        items: filtered.slice(offset, offset + (options.limit ?? 30)), total: filtered.length,
        counts: { ...counts, all: filtered.length, completed: filtered.length },
      };
    });
    vi.mocked(industryResearchApi.project).mockImplementation(async projectId => detailFor(projectId));
  }

  it('分页从服务器读取，12条以后和30条以后的历史报告都可打开', async () => {
    mockArchive();
    await mount('/industry-research');
    expect(screen.getByText('共 40 个任务 · 第 1 / 4 页')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /历史研究第13篇.*打开报告/ })).not.toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })); });
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 12, offset: 12 }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /历史研究第13篇.*打开报告/ })); });
    expect(screen.getByText('archive-13 的历史报告全文已读取。')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })); });
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 24 }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /历史研究第31篇.*打开报告/ })); });
    expect(screen.getByText('archive-31 的历史报告全文已读取。')).toBeInTheDocument();
    expect(screen.getByText('共 40 个任务 · 第 3 / 4 页')).toBeInTheDocument();
    expect(industryResearchApi.createProject).not.toHaveBeenCalled();
  });

  it('标题输入不自动提交，点击搜索从第一页查询全库而不是当前12条', async () => {
    mockArchive();
    await mount('/industry-research');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })); });
    const calls = vi.mocked(industryResearchApi.projects).mock.calls.length;
    fireEvent.change(screen.getByRole('textbox', { name: '按报告标题搜索' }), { target: { value: '第31篇' } });
    expect(industryResearchApi.projects).toHaveBeenCalledTimes(calls);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '搜索报告' })); });
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith(expect.objectContaining({ query: '第31篇', offset: 0 }));
    expect(screen.getByRole('button', { name: /历史研究第31篇.*打开报告/ })).toBeInTheDocument();
    expect(screen.getByText('标题“第31篇” · 共 1 个任务 · 第 1 / 1 页')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完整完成1' })).toBeInTheDocument();
  });

  it('翻页失败保留成功页及完整报告，点击重试继续原来的下一页', async () => {
    mockArchive();
    await mount('/industry-research');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /历史研究第1篇.*打开报告/ })); });
    vi.mocked(industryResearchApi.projects).mockRejectedValueOnce(new Error('upstream timeout'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })); });
    expect(screen.getByText('共 40 个任务 · 第 1 / 4 页')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /历史研究第12篇.*打开报告/ })).toBeInTheDocument();
    expect(screen.getByText('archive-1 的历史报告全文已读取。')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试任务列表' })); });
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 12 }));
    expect(screen.getByText('共 40 个任务 · 第 2 / 4 页')).toBeInTheDocument();
    expect(screen.getByText('archive-1 的历史报告全文已读取。')).toBeInTheDocument();
  });

  it('状态筛选在服务端作用于全部档案并重置页码，数量来自全库而非本页', async () => {
    mockArchive();
    await mount('/industry-research');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页' })); });
    vi.mocked(industryResearchApi.projects).mockResolvedValueOnce({
      items: [{ ...archive[30], status: 'failed' }], total: 1,
      counts: { all: 40, completed: 39, failed: 1, limited: 0, running: 0 },
    });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '失败0' })); });
    expect(industryResearchApi.projects).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, status: 'failed' }));
    expect(screen.getByText('共 1 个任务 · 第 1 / 1 页')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全部40' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /历史研究第31篇.*查看原因/ })).toBeInTheDocument();
  });

  it('StrictMode直接打开第31份报告不被默认蓝图重放覆盖，也不依赖第一页包含它', async () => {
    mockArchive();
    await mount('/industry-research?project=archive-31', true);
    expect(screen.getByText('archive-31 的历史报告全文已读取。')).toBeInTheDocument();
    expect(industryResearchApi.blueprint).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /历史研究第31篇.*打开报告/ })).not.toBeInTheDocument();
    expect(industryResearchApi.project).toHaveBeenCalledWith('archive-31');
    expect(industryResearchApi.createProject).not.toHaveBeenCalled();
  });

  it('旧报告直达请求失败可重试，无需重新支付创建报告', async () => {
    mockArchive();
    vi.mocked(industryResearchApi.project).mockRejectedValueOnce(new Error('暂时无法读取'));
    await mount('/industry-research?project=archive-31');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试打开报告' })); });
    expect(screen.getByText('archive-31 的历史报告全文已读取。')).toBeInTheDocument();
    expect(industryResearchApi.createProject).not.toHaveBeenCalled();
  });

  it('先发出的慢搜索不能覆盖用户后来提交的搜索结果', async () => {
    mockArchive();
    await mount('/industry-research');
    let finishOld!: (result: { items: IndustryResearchProject[]; total: number }) => void;
    vi.mocked(industryResearchApi.projects).mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    fireEvent.change(screen.getByRole('textbox', { name: '按报告标题搜索' }), { target: { value: '第13篇' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '搜索报告' })); });
    fireEvent.change(screen.getByRole('textbox', { name: '按报告标题搜索' }), { target: { value: '第31篇' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '搜索报告' })); });
    expect(screen.getByText('标题“第31篇” · 共 1 个任务 · 第 1 / 1 页')).toBeInTheDocument();
    await act(async () => { finishOld({ items: [archive[12]], total: 1 }); });
    expect(screen.getByText('标题“第31篇” · 共 1 个任务 · 第 1 / 1 页')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /历史研究第31篇.*打开报告/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /历史研究第13篇.*打开报告/ })).not.toBeInTheDocument();
  });
});
