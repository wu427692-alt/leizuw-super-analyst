import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

describe('LandingPage', () => {
  it('introduces the real platform and enters the dashboard', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /研究，\s*不该从\s*整理资料开始。/ })).toBeInTheDocument();
    expect(screen.getAllByText('乐子乌超级价值')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: '市场总览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自选股超级看板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '机构段子与录音' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '投资情报台' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '量化回测与数据利用' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据一站式获取' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '概念题材查看' })).toBeInTheDocument();
    expect(screen.getByText('中国股票研究工作台')).toBeInTheDocument();
    expect(screen.getByLabelText('平台研究数据范围')).toBeVisible();
    expect(screen.getByLabelText('研究工作台三层结构')).toBeVisible();
    expect(screen.getByRole('heading', { name: /八个界面，\s*对应八类研究任务。/ })).toBeInTheDocument();
    expect(screen.getByAltText('乐子乌超级价值市场总览真实页面')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
    expect(screen.getByRole('link', { name: /进入研究平台/ })).toHaveAttribute('href', '/app');
    expect(screen.getAllByRole('link', { name: '使用手册' })[0]).toHaveAttribute('href', '/guide');
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute('href', '/access?mode=register&redirect=%2Fapp');
    expect(screen.getAllByRole('link', { name: '管理员' })[0]).toHaveAttribute('href', '/admin');
  });

  it('keeps the whole product visible without waiting for scroll effects', () => {
    const { container } = render(<MemoryRouter><LandingPage /></MemoryRouter>);
    const page = container.querySelector('.landing-page') as HTMLElement;
    expect(page.querySelectorAll('[data-landing-reveal]')).toHaveLength(0);
    expect(screen.getByRole('heading', { name: '数据有口径' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '概念题材查看' })).toBeVisible();
    expect(screen.getByRole('link', { name: /进入研究平台/ })).toBeVisible();
  });
});
