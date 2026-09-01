import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

describe('LandingPage', () => {
  it('introduces the real platform and enters the dashboard', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: /从变化，\s*到判断，\s*再到行动。/ })).toBeInTheDocument();
    expect(screen.getAllByText('乐子乌超级价值')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: '今日决策' })).toBeInTheDocument();
    const productTabs = within(screen.getByRole('tablist', { name: '研究任务' }));
    expect(productTabs.getByRole('tab', { name: /今日决策/ })).toBeInTheDocument();
    expect(productTabs.getByRole('tab', { name: /机会发现/ })).toBeInTheDocument();
    expect(productTabs.getByRole('tab', { name: /个股决策/ })).toBeInTheDocument();
    expect(productTabs.getByRole('tab', { name: /深度研究/ })).toBeInTheDocument();
    expect(productTabs.getByRole('tab', { name: /任务与验证/ })).toBeInTheDocument();
    expect(productTabs.queryByRole('tab', { name: /投资情报台/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('平台研究数据范围')).toBeVisible();
    expect(screen.getByLabelText('新版决策工作流')).toBeVisible();
    expect(screen.getByRole('heading', { name: /五个步骤，\s*围绕一次真实决策。/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /五个工作区，\s*围绕一次决策。/ })).toBeInTheDocument();
    expect(screen.getByAltText('乐子乌超级价值今日市场环境最新真实页面')).toHaveAttribute('src', '/landing/screens/market-overview.jpg');
    expect(screen.getByRole('link', { name: /进入今日决策/ })).toHaveAttribute('href', '/app');
    expect(screen.getAllByRole('link', { name: '使用手册' })[0]).toHaveAttribute('href', '/guide');
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute('href', '/access?mode=register&redirect=%2Fapp');
    expect(screen.getAllByRole('link', { name: '管理员' })[0]).toHaveAttribute('href', '/admin');
  });

  it('keeps the whole product visible without waiting for scroll effects', () => {
    const { container } = render(<MemoryRouter><LandingPage /></MemoryRouter>);
    const page = container.querySelector('.landing-page') as HTMLElement;
    expect(page.querySelectorAll('[data-landing-reveal]')).toHaveLength(0);
    expect(screen.getByRole('heading', { name: '先看变化' })).toBeVisible();
    expect(within(screen.getByRole('tablist', { name: '研究任务' })).getByRole('tab', { name: /机会发现/ })).toBeVisible();
    expect(screen.getByRole('link', { name: /进入今日决策/ })).toBeVisible();
  });

  it('switches the evidence chain and product screenshot without loading every screenshot at once', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);

    const evidenceTabs = within(screen.getByRole('tablist', { name: '研究证据链' }));
    const productTabs = within(screen.getByRole('tablist', { name: '研究任务' }));

    fireEvent.click(evidenceTabs.getByRole('tab', { name: /验证复盘/ }));
    expect(screen.getByRole('heading', { name: '验证复盘' })).toBeVisible();
    expect(screen.getByAltText('验证复盘最新真实界面')).toHaveAttribute('src', '/landing/screens/quant-workbench.jpg');

    fireEvent.click(productTabs.getByRole('tab', { name: /深度研究/ }));
    expect(screen.getByRole('heading', { name: '深度研究' })).toBeVisible();
    expect(screen.getByAltText('深度研究最新真实页面')).toHaveAttribute('src', '/landing/screens/industry-research.jpg');
  });
});
