import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { MobileMoreSheet } from '../MobileMoreSheet';

describe('MobileMoreSheet', () => {
  it('lists every non-primary module in the same list the sidebar uses', () => {
    render(
      <MemoryRouter initialEntries={['/home']}>
        <MobileMoreSheet isOpen onClose={() => {}} excludePaths={['/meeting', '/app', '/super-watchlist', '/home']} />
      </MemoryRouter>,
    );

    for (const label of ['题材图谱', '深度研究', '任务中心', 'AI 热点', '机构纪要与录音', '量化研究', '数据下载', 'AI 问股', '使用指南', '管理员']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    // 底部标签栏已有的入口不重复出现在弹层里
    expect(screen.queryByRole('link', { name: '投研团队' })).not.toBeInTheDocument();
  });

  it('renders nothing while closed', () => {
    render(
      <MemoryRouter>
        <MobileMoreSheet isOpen={false} onClose={() => {}} excludePaths={[]} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
