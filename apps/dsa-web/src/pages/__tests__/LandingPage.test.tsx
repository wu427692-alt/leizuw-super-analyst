import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

describe('LandingPage', () => {
  it('introduces the real platform and enters the dashboard', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /让市场信息，\s*成为可验证的研究优势。/ })).toBeInTheDocument();
    expect(screen.getAllByText('乐子乌超级价值')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: '市场总览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自选股超级看板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '机构段子与录音' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '投资情报台' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '量化回测与数据利用' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据一站式获取' })).toBeInTheDocument();
    expect(screen.getByText('以下功能均为已上线真实页面')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /不是概念图。/ })).toBeInTheDocument();
    expect(screen.getByAltText('乐子乌超级价值市场总览真实页面')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
    expect(screen.getByRole('link', { name: /进入研究平台/ })).toHaveAttribute('href', '/app');
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute('href', '/access?mode=register&redirect=%2Fapp');
    expect(screen.getAllByRole('link', { name: '管理员' })[0]).toHaveAttribute('href', '/admin');
  });

  it('updates the visual field without blocking the CTA', () => {
    const { container } = render(<MemoryRouter><LandingPage /></MemoryRouter>);
    const page = container.querySelector('.landing-page') as HTMLElement;
    Object.defineProperty(page, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 1000, height: 800, right: 1000, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.pointerMove(page, { clientX: 750, clientY: 200 });

    expect(page.style.getPropertyValue('--pointer-x')).toBe('75%');
    expect(screen.getByRole('link', { name: /进入研究平台/ })).toBeVisible();
  });
});
