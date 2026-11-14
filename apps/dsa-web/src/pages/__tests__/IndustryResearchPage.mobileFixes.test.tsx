import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
vi.mock('../../components/common', () => ({
  AppPage: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  // 页面新增的共享原语：这里只需可渲染，布局断言不在本文件覆盖范围。
  PageTabs: ({ items, value }: { items: Array<{ key: string; label: string }>; value: string }) => (
    <div data-testid="mock-page-tabs" data-active={value}>{items.map(item => <span key={item.key}>{item.label}</span>)}</div>
  ),
  Metric: () => null,
  MetricRow: () => null,
  Sparkline: () => null,
  BarMeter: () => null,
  ShareBar: () => null,
  HeatCell: () => null,
}));

const emptyEvidenceBlueprint: IndustryResearchBlueprint = {
  topic: '光模块',
  researchType: 'industry',
  lookbackDays: 730,
  queryTerms: ['光模块'],
  generatedAt: '2026-09-20T00:00:00Z',
  methodology: {
    name: 'Evidence-to-Decision', principles: [], stages: [], aiFlow: [],
    requiredQuestions: [], evidenceRule: '每个判断都要能回到原文', dataRequirements: [],
  },
  snapshot: {
    topic: '光模块', researchType: 'industry', lookbackDays: 730, queryTerms: ['光模块'],
    totals: { evidence: 0, reports: 0, notes: 0, events: 0, mediaFiles: 0 },
    coverage: [], companies: [], timeline: [], evidence: [], sourceHash: 'sha256:empty',
  },
};

const runningProject: IndustryResearchProject = {
  projectId: 'research-new', topic: '光模块', researchType: 'industry', objective: '测试发起',
  lookbackDays: 730, status: 'queued', progress: 0, stage: 'contract', message: '任务已创建，正在排队',
  query: {}, createdAt: '2026-09-20T01:00:00Z', updatedAt: '2026-09-20T01:00:00Z',
};

async function mount() {
  render(<MemoryRouter initialEntries={['/industry-research']}><IndustryResearchPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('button', { name: /开始行业研究/ })).toBeTruthy());
}

describe('IndustryResearchPage 手机端修复', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    vi.mocked(industryResearchApi.blueprint).mockResolvedValue(emptyEvidenceBlueprint);
    vi.mocked(industryResearchApi.projects).mockResolvedValue({
      items: [], total: 0, counts: { all: 0, running: 0, completed: 0, limited: 0, failed: 0 },
    });
  });

  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('证据库没有证据时给出空态，而不是渲染一片空白', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: /证据库/ }));
    expect(await screen.findByText('当前筛选条件下没有可核对的证据')).toBeInTheDocument();
  });

  it('开始研究后页面上存在可定位的结论/进度卡，手机端据此滚动而不是跳到页面底部', async () => {
    vi.mocked(industryResearchApi.createProject).mockResolvedValue(runningProject);
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /开始行业研究/ }));
    expect(await screen.findByText('Kimi 正在研究')).toBeInTheDocument();
    expect(document.getElementById('industry-research-answer')).not.toBeNull();
  });

  it('创建任务失败的错误提示排在命令栏与任务账本之间，用户不必滚到底部才能看到', async () => {
    vi.mocked(industryResearchApi.createProject).mockRejectedValue(new Error('课题创建失败：服务暂不可用'));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /开始行业研究/ }));
    const alert = await screen.findByText('课题创建失败：服务暂不可用');
    const taskCenter = document.querySelector('.ir-task-center');
    expect(taskCenter).not.toBeNull();
    expect(Boolean(alert.compareDocumentPosition(taskCenter as Node) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });
});
