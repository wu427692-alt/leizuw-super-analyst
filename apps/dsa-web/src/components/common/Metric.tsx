import type React from 'react';
import { cn } from '../../utils/cn';

/**
 * 数字排版原语（`docs/ui-design-system.md` §11）。
 *
 * 三级层次固定，不允许出现第四级：
 *   主数字 `--fs-h2` 600 → 变化量 `--fs-body` 500 带涨跌色 → 辅助 `--fs-caption` `--text-3`。
 * 颜色只表达涨跌与状态，不表达重要性。
 */

export type MetricTrend = 'up' | 'down' | 'flat';

export type MetricProps = {
  label: string;
  /** 主值，已格式化好的字符串；空值传 null 显示 — */
  value: string | number | null | undefined;
  /** 变化量，如「+2.31%」；由 `trend` 决定颜色 */
  delta?: string | number | null;
  trend?: MetricTrend;
  /** 辅助说明，最多一行 */
  hint?: string;
  /** 行内可视化插槽，通常放 Sparkline / BarMeter */
  viz?: React.ReactNode;
  /** inline 用于表格行内，block 用于指标卡 */
  layout?: 'inline' | 'block';
  className?: string;
  ariaLabel?: string;
};

const TREND_COLOR: Record<MetricTrend, string> = {
  up: 'var(--up)',
  down: 'var(--down)',
  flat: 'var(--text-3)',
};

const renderValue = (value: MetricProps['value']): string => {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
};

/**
 * 单个指标。数字一律 tabular-nums 右对齐，避免刷新时宽度跳动。
 * 空值统一显示 `—`（`--text-4`），禁止 `0` / `N/A` / `null` / 空白。
 */
export const Metric: React.FC<MetricProps> = ({
  label, value, delta, trend = 'flat', hint, viz, layout = 'block', className, ariaLabel,
}) => {
  const empty = value === null || value === undefined || value === '';
  const deltaEmpty = delta === null || delta === undefined || delta === '';

  return (
    <div
      className={cn('metric', `metric--${layout}`, empty && 'is-empty', className)}
      aria-label={ariaLabel ?? label}
    >
      <span className="metric__label">{label}</span>
      <span className="metric__value">
        <b>{renderValue(value)}</b>
        {!deltaEmpty ? (
          <em style={{ color: TREND_COLOR[trend] }}>{String(delta)}</em>
        ) : null}
        {viz ? <span className="metric__viz">{viz}</span> : null}
      </span>
      {hint ? <span className="metric__hint">{hint}</span> : null}
    </div>
  );
};

export type MetricRowProps = {
  items: MetricProps[];
  /** 等分列数；内容型指标不超过 4 列，超过改换行 */
  columns?: 2 | 3 | 4;
  className?: string;
};

/**
 * 一组并列指标。用于替代「每个指标一张 70px 高卡片」的铺张写法，
 * 压缩到单行或两行的紧凑网格（规范 §12：禁止卡片里套卡片）。
 */
export const MetricRow: React.FC<MetricRowProps> = ({ items, columns = 3, className }) => (
  <div className={cn('metric-row', `metric-row--${columns}`, className)}>
    {items.map((item, index) => <Metric key={item.label ?? index} {...item} layout="inline" />)}
  </div>
);

export default Metric;
