import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import UserGuidePage from '../UserGuidePage';

describe('UserGuidePage', () => {
  it('documents the real workspaces and links back into the product', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /不是功能清单/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '市场总览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '研究决策台' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自选股超级看板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '机构段子与录音' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据一站式获取' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '行业与公司调研' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '管理员后台' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /进入自选股超级看板/ })).toHaveAttribute('href', '/super-watchlist');
    expect(screen.getByAltText('市场总览实机页面截图')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
    expect(screen.getByLabelText('市场总览截图标注说明')).toBeInTheDocument();
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

    fireEvent.change(screen.getByRole('textbox', { name: '搜索使用手册' }), { target: { value: '龙虎榜' } });
    expect(screen.getByRole('heading', { name: '投资情报台' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '市场总览' })).not.toBeInTheDocument();
    expect(screen.getByText('当前显示 1 / 12 个工作台')).toBeInTheDocument();
  });
});
