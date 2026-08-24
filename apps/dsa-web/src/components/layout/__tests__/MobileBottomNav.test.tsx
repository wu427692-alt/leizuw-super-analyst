import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { MobileBottomNav } from '../MobileBottomNav';

describe('MobileBottomNav', () => {
  it('exposes the four primary mobile destinations and a more drawer trigger', () => {
    const onOpenMore = vi.fn();
    render(
      <MemoryRouter initialEntries={['/super-watchlist']}>
        <MobileBottomNav onOpenMore={onOpenMore} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '首页' })).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '自选股超级看板' })).toHaveClass('is-active');
    expect(screen.getByRole('link', { name: '机构段子与录音' })).toHaveAttribute('href', '/essay-radar');
    expect(screen.getByRole('link', { name: '数据一站式获取' })).toHaveAttribute('href', '/data-acquisition');
    expect(screen.getByText('机构段子')).toBeInTheDocument();
    expect(screen.getByText('数据下载')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '问股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '投资情报台' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '导航菜单' }));
    expect(onOpenMore).toHaveBeenCalledTimes(1);
  });

  it('marks more active for secondary research pages', () => {
    render(
      <MemoryRouter initialEntries={['/essay-quant']}>
        <MobileBottomNav onOpenMore={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('button', { name: '导航菜单' })).toHaveClass('is-active');
  });

  it('keeps essay and download subpages inside their primary active destinations', () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={['/essay-radar/feed']}>
        <MobileBottomNav onOpenMore={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '机构段子与录音' })).toHaveClass('is-active');

    unmount();
    render(
      <MemoryRouter initialEntries={['/data-acquisition']}>
        <MobileBottomNav onOpenMore={() => undefined} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: '数据一站式获取' })).toHaveClass('is-active');
  });
});
