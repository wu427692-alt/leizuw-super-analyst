import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { MobileBottomNav } from '../MobileBottomNav';

describe('MobileBottomNav', () => {
  it('exposes the five decision workspaces', () => {
    render(
      <MemoryRouter initialEntries={['/super-watchlist']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '首页' })).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '自选股超级看板' })).toHaveClass('is-active');
    expect(screen.getByRole('link', { name: '概念题材查看' })).toHaveAttribute('href', '/concept-themes');
    expect(screen.getByRole('link', { name: '行业与公司调研' })).toHaveAttribute('href', '/industry-research');
    expect(screen.getByRole('link', { name: '任务与验证' })).toHaveAttribute('href', '/tasks');
    expect(screen.getByText('机会')).toBeInTheDocument();
    expect(screen.getByText('任务')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '问股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '投资情报台' })).not.toBeInTheDocument();

    expect(screen.queryByRole('button', { name: '导航菜单' })).not.toBeInTheDocument();
  });

  it('marks tasks active', () => {
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '任务与验证' })).toHaveClass('is-active');
  });

  it('does not put utility pages in the primary mobile bar', () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={['/essay-radar/feed']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '机构段子与录音' })).not.toBeInTheDocument();

    unmount();
    render(
      <MemoryRouter initialEntries={['/data-acquisition']}>
        <MobileBottomNav />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('link', { name: '数据一站式获取' })).not.toBeInTheDocument();
  });
});
