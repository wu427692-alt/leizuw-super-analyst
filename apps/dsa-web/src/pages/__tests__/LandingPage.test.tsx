import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import LandingPage from '../LandingPage';

function renderLanding() {
  return render(<MemoryRouter><LandingPage /></MemoryRouter>);
}

function productTabs() {
  return within(screen.getByRole('tablist', { name: '产品功能展示' }));
}

const productCases = [
  {
    title: '市场总览', alt: '市场总览历史界面', screenshot: '/landing/screens/market-overview.jpg',
    action: '查看市场总览', href: '/app',
  },
  {
    title: '概念题材', alt: '概念题材历史界面', screenshot: '/landing/screens/concept-themes.jpg',
    action: '探索概念题材', href: '/concept-themes',
  },
  {
    title: '自选股', alt: '自选股超级看板历史界面', screenshot: '/landing/screens/super-watchlist-preview.png',
    action: '探索自选股', href: '/super-watchlist',
  },
  {
    title: '深度调研', alt: '行业与公司调研历史界面', screenshot: '/landing/screens/industry-research.jpg',
    action: '开始深度调研', href: '/industry-research',
  },
  {
    title: '量化验证', alt: '量化研究工作台历史界面', screenshot: '/landing/screens/quant-workbench.jpg',
    action: '进入量化工作台', href: '/essay-quant',
  },
] as const;

describe('LandingPage', () => {
  it('presents the product clearly and keeps every public entry route intact', () => {
    renderLanding();

    expect(screen.getByRole('heading', { name: /让信息沉淀，\s*让判断清晰。/ })).toBeInTheDocument();
    const registerPath = '/access?mode=register&redirect=%2Fapp';
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute(
      'href',
      registerPath,
    );
    expect(screen.getByRole('link', { name: '注册账号' })).toHaveAttribute('href', registerPath);
    expect(screen.getByText('登录或注册，开启你的个人研究空间。')).toBeVisible();
    expect(screen.queryByText(/姓名与密码即可提交注册申请/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '进入平台' })).toHaveAttribute('href', '/app');
    expect(screen.getAllByRole('link', { name: '使用手册' })).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: '使用手册' })[0]).toHaveAttribute('href', '/guide');
    expect(screen.getByRole('link', { name: '管理员' })).toHaveAttribute('href', '/admin');
    expect(screen.getByText('历史界面示意 · 非实时行情')).toBeVisible();
  });

  it('shows five product tabs with the stock workspace selected by default', () => {
    renderLanding();

    const tabs = productTabs();
    const market = tabs.getByRole('tab', { name: '市场总览' });
    const concepts = tabs.getByRole('tab', { name: '概念题材' });
    const watchlist = tabs.getByRole('tab', { name: '自选股' });
    const research = tabs.getByRole('tab', { name: '深度调研' });
    const quant = tabs.getByRole('tab', { name: '量化验证' });

    expect(tabs.getAllByRole('tab')).toHaveLength(5);
    expect(watchlist).toHaveAttribute('aria-selected', 'true');
    expect(watchlist).toHaveAttribute('tabindex', '0');
    for (const tab of [market, concepts, research, quant]) {
      expect(tab).toHaveAttribute('aria-selected', 'false');
      expect(tab).toHaveAttribute('tabindex', '-1');
    }

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveAttribute('id', 'landing-product-panel');
    expect(watchlist).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('aria-labelledby', watchlist.id);
    expect(screen.getByRole('heading', { name: '围绕一家公司，把信息看完整。' })).toBeVisible();
    expect(screen.getByRole('link', { name: '探索自选股' })).toHaveAttribute('href', '/super-watchlist');
  });

  it.each(productCases)('keeps the $title screenshot, page CTA and large preview in sync', ({
    title, alt, screenshot, action, href,
  }) => {
    renderLanding();

    const tab = productTabs().getByRole('tab', { name: title });
    fireEvent.click(tab);
    expect(screen.getAllByRole('img')).toHaveLength(2);
    expect(screen.getByRole('img', { name: alt })).toHaveAttribute('src', screenshot);
    expect(screen.getByRole('link', { name: action })).toHaveAttribute('href', href);
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tab.id);

    const largePreview = screen.getByRole('link', { name: `查看${title}界面大图` });
    expect(largePreview).toHaveAttribute('href', screenshot);
    expect(largePreview).toHaveAttribute('target', '_blank');
    expect(largePreview).toHaveAttribute('rel', 'noreferrer');
  });

  it('supports Arrow, Home and End keyboard navigation with focus and panel state in sync', () => {
    renderLanding();

    const tabs = productTabs();
    const market = tabs.getByRole('tab', { name: '市场总览' });
    const watchlist = tabs.getByRole('tab', { name: '自选股' });
    const research = tabs.getByRole('tab', { name: '深度调研' });
    const quant = tabs.getByRole('tab', { name: '量化验证' });

    fireEvent.keyDown(watchlist, { key: 'ArrowRight' });
    expect(research).toHaveFocus();
    expect(research).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', research.id);

    fireEvent.keyDown(research, { key: 'ArrowLeft' });
    expect(watchlist).toHaveFocus();
    expect(watchlist).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(watchlist, { key: 'Home' });
    expect(market).toHaveFocus();
    expect(market).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(market, { key: 'End' });
    expect(quant).toHaveFocus();
    expect(quant).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(quant, { key: 'ArrowRight' });
    expect(market).toHaveFocus();
    expect(market).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(market, { key: 'ArrowLeft' });
    expect(quant).toHaveFocus();
    expect(quant).toHaveAttribute('aria-selected', 'true');
  });

  it('does not restore the removed evidence chain or animated market ribbon', () => {
    renderLanding();

    expect(screen.queryByLabelText('研究证据链')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('平台研究数据范围')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /五个步骤/ })).not.toBeInTheDocument();
  });
});
