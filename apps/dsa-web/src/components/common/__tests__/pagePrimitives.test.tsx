import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PageTabs } from '../PageTabs';
import { Metric, MetricRow } from '../Metric';
import { BarMeter, HeatCell, ShareBar, Sparkline } from '../InlineViz';

/**
 * 页面级原语的回归测试（`docs/ui-design-system.md` §10–§12）。
 *
 * 这些原语是「控制单页信息量 + 数字排版 + 行内可视化」的落地点，
 * 一旦行为漂移，全站十几个页面会一起变形，所以单独锁住。
 */

describe('PageTabs', () => {
  const items = [
    { key: 'overview', label: '概览', badge: 12 },
    { key: 'detail', label: '明细' },
    { key: 'off', label: '停用', disabled: true },
  ];

  it('把当前 Tab 标成选中，供样式与无障碍同时识别', () => {
    render(<PageTabs items={items} value="overview" onChange={() => {}} />);
    const active = screen.getByRole('tab', { name: /概览/ });
    expect(active).toHaveAttribute('aria-selected', 'true');
    expect(active.className).toContain('is-active');
  });

  it('点击其它 Tab 时回调新 key', () => {
    const onChange = vi.fn();
    render(<PageTabs items={items} value="overview" onChange={onChange} />);
    screen.getByRole('tab', { name: '明细' }).click();
    expect(onChange).toHaveBeenCalledWith('detail');
  });

  it('点击当前 Tab 不重复触发回调，避免无意义重渲染', () => {
    const onChange = vi.fn();
    render(<PageTabs items={items} value="overview" onChange={onChange} />);
    screen.getByRole('tab', { name: /概览/ }).click();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('禁用 Tab 不响应点击', () => {
    const onChange = vi.fn();
    render(<PageTabs items={items} value="overview" onChange={onChange} />);
    screen.getByRole('tab', { name: '停用' }).click();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('Metric', () => {
  it('空值统一显示破折号，不显示 0 或 N/A', () => {
    render(<Metric label="成交额" value={null} />);
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('涨跌方向决定变化量颜色，而不是调用方随手传色', () => {
    const { container, rerender } = render(<Metric label="涨跌" value="12.3" delta="+2.1%" trend="up" />);
    const up = container.querySelector('.metric__value em') as HTMLElement;
    expect(up.style.color).toBe('var(--up)');

    rerender(<Metric label="涨跌" value="12.3" delta="-2.1%" trend="down" />);
    const down = container.querySelector('.metric__value em') as HTMLElement;
    expect(down.style.color).toBe('var(--down)');
  });

  it('没有变化量时不渲染空 em', () => {
    const { container } = render(<Metric label="市盈率" value="18.4" />);
    expect(container.querySelector('.metric__value em')).toBeNull();
  });

  it('MetricRow 按列数排版，并把每项降级为行内密度', () => {
    const { container } = render(
      <MetricRow columns={3} items={[
        { label: '总量', value: '1.2万亿' },
        { label: '涨', value: '3200' },
        { label: '跌', value: '1800' },
      ]} />,
    );
    expect(container.querySelector('.metric-row--3')).toBeTruthy();
    expect(container.querySelectorAll('.metric--inline')).toHaveLength(3);
  });
});

describe('InlineViz', () => {
  it('Sparkline 少于两个点时不画线，避免假趋势', () => {
    render(<Sparkline values={[5]} ariaLabel="单点" />);
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('Sparkline 按首尾涨跌自动取色，且面积填充不超过规范上限', () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} />);
    const paths = container.querySelectorAll('path');
    expect(paths).toHaveLength(2);
    const area = paths[0];
    expect(Number(area.getAttribute('fill-opacity'))).toBeLessThanOrEqual(0.12);
    expect(area.getAttribute('stroke')).toBe('none');
    expect(paths[1].getAttribute('stroke')).toBe('var(--up)');
  });

  it('BarMeter 把值压进 0..100%，超上限时封顶', () => {
    const { container } = render(<BarMeter value={50} max={100} />);
    const fill = container.querySelector('.viz-barmeter > span') as HTMLElement;
    // jsdom 会把 50.0% 归一成 50%，按数值比较而不是字符串
    expect(parseFloat(fill.style.width)).toBeCloseTo(50, 1);

    const { container: over } = render(<BarMeter value={150} max={100} />);
    const capped = over.querySelector('.viz-barmeter > span') as HTMLElement;
    expect(parseFloat(capped.style.width)).toBeCloseTo(100, 1);
  });

it('BarMeter 在 center 基线下保留符号：负值向左生长并自动用跌色', () => {
    const { container } = render(<BarMeter value={-40} max={100} baseline="center" />);
    expect(container.querySelector('.viz-barmeter--center')).toBeTruthy();
    const fill = container.querySelector('.viz-barmeter > span') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeCloseTo(40, 1);
    expect(fill.className).toContain('is-negative');
    // 调用方不需要再自己取绝对值并覆盖颜色
    expect(fill.style.background).toBe('var(--down)');

    const { container: pos } = render(<BarMeter value={40} max={100} baseline="center" />);
    const upFill = pos.querySelector('.viz-barmeter > span') as HTMLElement;
    expect(upFill.className).not.toContain('is-negative');
    expect(upFill.style.background).toBe('var(--up)');
  });

  it('BarMeter 对负数用跌色', () => {
    const { container } = render(<BarMeter value={-20} max={100} />);
    const fill = container.querySelector('.viz-barmeter > span') as HTMLElement;
    expect(fill.style.background).toBe('var(--down)');
  });

  it('ShareBar 最多 5 段，并按占比分配宽度', () => {
    const { container } = render(
      <ShareBar segments={[
        { key: 'a', value: 50 }, { key: 'b', value: 30 }, { key: 'c', value: 20 },
        { key: 'd', value: 10 }, { key: 'e', value: 10 }, { key: 'f', value: 5 },
      ]} />,
    );
    const segments = container.querySelectorAll('.viz-sharebar > span');
    expect(segments).toHaveLength(5);
    expect((segments[0] as HTMLElement).style.width).toMatch(/%$/);
  });

  it('ShareBar 全零时给占位，不画空条', () => {
    const { container } = render(<ShareBar segments={[{ key: 'a', value: 0 }]} />);
    expect(container.querySelector('.viz-sharebar')).toBeNull();
  });

  it('HeatCell 只使用涨跌色四档透明度，不引入新色', () => {
    const { container } = render(<HeatCell intensity={0.6} />);
    const cell = container.querySelector('.viz-heatcell') as HTMLElement;
    expect(cell.style.background).toBe('var(--up)');
    expect(Number(cell.style.opacity)).toBeCloseTo(0.55, 2);
  });

  it('HeatCell 负值用跌色', () => {
    const { container } = render(<HeatCell intensity={-0.9} />);
    const cell = container.querySelector('.viz-heatcell') as HTMLElement;
    expect(cell.style.background).toBe('var(--down)');
  });
});
