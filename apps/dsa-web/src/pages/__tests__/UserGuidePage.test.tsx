import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import UserGuidePage from '../UserGuidePage';

describe('UserGuidePage', () => {
  it('documents the real workspaces and links back into the product', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /从问题出发/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '市场总览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自选股超级看板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '机构段子与录音' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据一站式获取' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '行业调研' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /打开自选股看板/ })).toHaveAttribute('href', '/super-watchlist');
    expect(screen.getByAltText('市场总览实机页面截图')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
  });

  it('opens and closes a real screenshot preview', () => {
    render(<MemoryRouter><UserGuidePage /></MemoryRouter>);

    fireEvent.click(screen.getByRole('button', { name: '放大查看市场总览截图' }));
    expect(screen.getByRole('dialog', { name: '页面截图预览' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭截图预览' }));
    expect(screen.queryByRole('dialog', { name: '页面截图预览' })).not.toBeInTheDocument();
  });
});
