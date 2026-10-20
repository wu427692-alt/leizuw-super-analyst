import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import UserGuidePage from '../UserGuidePage';

// jsdom has no native modal implementation; browser QA covers inertness and Tab focus.
const originalShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal');
const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close');
const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.open = false; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
});
afterAll(() => {
  if (originalShowModal) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', originalShowModal);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  if (originalClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll);
  else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView');
});
const CurrentLocation = () => <output aria-label="当前位置">{useLocation().hash}</output>;

describe('UserGuidePage', () => {
  it('documents the real workspaces and links back into the product', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /不是功能清单/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '市场总览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '任务与验证' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自选股超级看板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '机构段子与录音' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据一站式获取' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '行业与公司调研' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '管理员后台' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /进入自选股超级看板/ })).toHaveAttribute('href', '/super-watchlist');
    expect(screen.getByAltText('市场总览实机页面截图')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
    expect(screen.getByLabelText('市场总览截图标注说明')).toBeInTheDocument();
    expect(screen.queryByLabelText('市场总览局部功能截图')).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByLabelText('市场总览真实页面截图详解')).getByRole('button', { name: /展开.*局部操作说明/ }));
    expect(screen.getByLabelText('市场总览局部功能截图')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '市场总览真实页面局部：核心指数切换' })).toBeInTheDocument();
  });

  it('opens and closes a real screenshot preview', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    fireEvent.click(screen.getByRole('button', { name: '放大查看市场总览截图' }));
    expect(screen.getByRole('dialog', { name: '页面截图预览' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭截图预览' }));
    expect(screen.queryByRole('dialog', { name: '页面截图预览' })).not.toBeInTheDocument();
  });

  it('filters the complete manual by a detailed function keyword', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    fireEvent.change(screen.getByRole('textbox', { name: '搜索使用手册' }), { target: { value: '用户只能看到自己的任务' } });
    expect(screen.getByRole('heading', { name: '任务与验证' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '市场总览' })).not.toBeInTheDocument();
    expect(screen.getByText('当前显示 1 / 11 个工作台')).toBeInTheDocument();
  });

  it('shows actionable search results near the input and supports multiple terms', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);
    fireEvent.change(screen.getByRole('textbox', { name: '搜索使用手册' }), { target: { value: '  研报   ＰＤＦ ' } });
    const results = screen.getByLabelText('手册搜索结果');
    expect(within(results).getByRole('link', { name: /数据一站式获取/ })).toHaveAttribute('href', '#download');
    expect(within(results).queryByRole('link', { name: /管理员后台/ })).not.toBeInTheDocument();
    fireEvent.click(within(results).getByRole('link', { name: /数据一站式获取/ }));
    expect(document.getElementById('download')).toHaveFocus();
  });

  it('reveals an excluded chapter when the full table of contents is clicked', () => {
    render(<MemoryRouter><UserGuidePage /><CurrentLocation /></MemoryRouter>);
    fireEvent.change(screen.getByRole('textbox', { name: '搜索使用手册' }), { target: { value: '用户只能看到自己的任务' } });
    expect(document.getElementById('watchlist')).toBeNull();
    fireEvent.click(within(screen.getByLabelText('完整使用手册目录')).getByRole('link', { name: '自选股超级看板' }));
    expect(screen.getByRole('textbox', { name: '搜索使用手册' })).toHaveValue('');
    expect(document.getElementById('watchlist')).toHaveFocus();
    expect(screen.getByLabelText('当前位置')).toHaveTextContent('#watchlist');
  });

  it('reports empty results beside search and clears both keyword and category', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '系统管理' }));
    expect(screen.getByRole('button', { name: '系统管理' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(screen.getByRole('textbox', { name: '搜索使用手册' }), { target: { value: '不可能匹配的关键词' } });
    const results = screen.getByLabelText('手册搜索结果');
    expect(within(results).getByText('没有匹配的操作说明')).toBeVisible();
    fireEvent.click(within(results).getByRole('button', { name: '清除全部筛选' }));
    expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('当前显示 11 / 11 个工作台')).toBeInTheDocument();
  });

  it('restores focus and the previous scroll lock when the native preview is cancelled', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);
    document.body.style.overflow = 'clip';
    const trigger = screen.getByRole('button', { name: '放大查看市场总览截图' });
    trigger.focus();
    fireEvent.click(trigger);
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent(screen.getByRole('dialog', { name: '页面截图预览' }), new Event('cancel'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe('clip');
    expect(trigger).toHaveFocus();
    document.body.style.overflow = '';
  });

  it('uses a populated watchlist screenshot and never presents loading screens as real examples', () => {
    const { container } = render(<MemoryRouter><UserGuidePage /></MemoryRouter>);
    expect(screen.getByAltText('自选股超级看板实机页面截图')).toHaveAttribute('src', '/landing/screens/super-watchlist-preview.png');
    expect(container.querySelector('img[src="/landing/screens/essay-radar.jpg"]')).toBeNull();
    expect(container.querySelector('a[href="/research-center"]')).toBeNull();
    expect(container.querySelector('a[href="/investment-monitor"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '放大查看自选股超级看板截图' }));
    const preview = screen.getByRole('dialog');
    expect(within(preview).getByText('历史实机截图 · 非实时数据')).toBeInTheDocument();
    expect(within(preview).getByRole('link', { name: '打开原尺寸图片' })).toHaveAttribute('rel', 'noreferrer');
  });

  it('documents transcript-only downloads and links to the real sub-workspaces', () => {
    const { container } = render(<MemoryRouter><UserGuidePage /></MemoryRouter>);
    expect(screen.getByText('原文字稿查看与下载')).toBeInTheDocument();
    expect(screen.getByText('正式报告 Word / PDF')).toBeInTheDocument();
    const audioModule = screen.getByText('原文字稿查看与下载').closest('details')!;
    fireEvent.click(audioModule.querySelector('summary')!);
    expect(within(audioModule).getByRole('link', { name: '打开此子页' })).toHaveAttribute('href', '/essay-radar/feed?tab=audio');
    for (const route of [
      '/essay-quant?section=builder', '/essay-quant?section=natural-language', '/essay-quant?section=data',
      '/essay-quant?section=results', '/essay-quant?section=history', '/data-acquisition?view=reports', '/data-acquisition?view=tasks',
    ]) expect(container.querySelector(`a[href="${route}"]`)).not.toBeNull();
    expect(screen.queryByText('发送通知')).not.toBeInTheDocument();
    expect(screen.queryByText('带证据索引的录音小作文。')).not.toBeInTheDocument();
  });
});
