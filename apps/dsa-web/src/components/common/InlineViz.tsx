import type React from 'react';
import { cn } from '../../utils/cn';

/**
 * 行内可视化原语（`docs/ui-design-system.md` §11）。
 *
 * 设计约束：图表是列表的一列，不是独立一块。这些组件全部无坐标轴、无图例，
 * 只做「趋势 / 规模 / 占比 / 密度」四种编码，尺寸由调用方定，内部不设外边距。
 */

const CHART_UP = 'var(--up)';
const CHART_DOWN = 'var(--down)';
const CHART_FLAT = 'var(--flat)';

const trendColor = (values: number[]): string => {
  if (values.length < 2) return CHART_FLAT;
  const delta = values[values.length - 1] - values[0];
  if (delta > 0) return CHART_UP;
  if (delta < 0) return CHART_DOWN;
  return CHART_FLAT;
};

/** 把任意数值序列压成 0..1 比例，全等时落 0.5 避免画成贴底。 */
const normalize = (values: number[]): number[] => {
  const finite = values.filter(v => Number.isFinite(v));
  if (!finite.length) return values.map(() => 0.5);
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  if (max === min) return values.map(() => 0.5);
  return values.map(v => (Number.isFinite(v) ? (v - min) / (max - min) : 0.5));
};

export type SparklineProps = {
  values: number[];
  /** 宽，默认 72；高默认 18 */
  width?: number;
  height?: number;
  /** 不传则按首尾涨跌自动取色 */
  color?: string;
  /** 面积填充，默认 0.12（规范上限） */
  fillOpacity?: number;
  className?: string;
  ariaLabel?: string;
};

/**
 * 时序趋势线。线宽 1.2，面积填充 ≤0.12，不画坐标轴。
 * 少于 2 个点时不画线，只留占位，避免出现「假趋势」。
 */
export const Sparkline: React.FC<SparklineProps> = ({
  values, width = 72, height = 18, color, fillOpacity = 0.12, className, ariaLabel,
}) => {
  const points = values.filter(v => Number.isFinite(v));
  if (points.length < 2) {
    return <span className={cn('viz-empty', className)} style={{ width, height }} aria-hidden="true">—</span>;
  }
  const ratios = normalize(points);
  const stroke = color ?? trendColor(points);
  const stepX = width / (ratios.length - 1);
  const coords = ratios.map((ratio, index) => {
    // 上下各留 1.2px，保证线宽不被裁掉。
    const x = index * stepX;
    const y = height - 1.2 - ratio * (height - 2.4);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = `M${coords.join(' L')}`;
  const area = `${line} L${width.toFixed(2)},${height} L0,${height} Z`;

  return (
    <svg
      className={cn('viz-sparkline', className)}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel ?? '趋势'}
      preserveAspectRatio="none"
    >
      <path d={area} fill={stroke} fillOpacity={fillOpacity} stroke="none" />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
};

export type BarMeterProps = {
  /** 当前值；可为负（如行业涨跌幅） */
  value: number;
  /** 参照最大值；不传则按 1 处理（值需自己归一） */
  max?: number;
  /** 负值用跌色、正值用涨色 */
  tone?: 'auto' | 'accent' | 'neutral';
  height?: number;
  /** 对数压缩，防止头部把其余条压成 0（规模对比场景必须开） */
  log?: boolean;
  /**
   * 零点位置。`start` 从左边生长（默认，适合规模对比）；
   * `center` 以中线为零轴、负值向左生长（适合涨跌幅这类有符号数据，
   * 调用方不必再自己取 abs 并覆盖颜色）。
   */
  baseline?: 'start' | 'center';
  className?: string;
  ariaLabel?: string;
};

/**
 * 行内规模对比条。高 3～4px，长度 ∝ 值。
 * 头部数值远大于中位数时用 `log`，否则后几名会缩成看不见的碎片。
 */
export const BarMeter: React.FC<BarMeterProps> = ({
  value, max, tone = 'auto', height = 4, log = false, baseline = 'start', className, ariaLabel,
}) => {
  const safeMax = max && Number.isFinite(max) && max > 0 ? max : 1;
  // 有符号比例：正值 0..1，负值 -1..0。start 基线只取正向长度，
  // center 基线保留符号，由 CSS 决定向哪一侧生长。
  const signedRatio = (() => {
    if (!Number.isFinite(value)) return 0;
    if (log) {
      const sign = value < 0 ? -1 : 1;
      const mag = Math.log10(1 + Math.abs(value)) / Math.log10(1 + Math.abs(safeMax || 1));
      return Math.max(-1, Math.min(1, sign * mag));
    }
    return Math.max(-1, Math.min(1, value / safeMax));
  })();
  const ratio = baseline === 'center' ? Math.abs(signedRatio) : Math.max(0, signedRatio);
  const color = tone === 'neutral' ? 'var(--text-4)' : tone === 'accent' ? 'var(--brand)' : (value < 0 ? CHART_DOWN : CHART_UP);

  return (
    <span
      className={cn('viz-barmeter', baseline === 'center' && 'viz-barmeter--center', className)}
      style={{ height }}
      role="img"
      aria-label={ariaLabel ?? '规模占比'}
    >
      <span
        className={cn(baseline === 'center' && signedRatio < 0 && 'is-negative')}
        style={{ width: `${(ratio * 100).toFixed(1)}%`, background: color }}
      />
    </span>
  );
};

export type ShareBarSegment = { key: string; value: number; color?: string; label?: string };

export type ShareBarProps = {
  segments: ShareBarSegment[];
  height?: number;
  className?: string;
  ariaLabel?: string;
};

/**
 * 构成占比条。单条 3px 堆叠，段间不留缝，最多 5 段
 * （超过 5 段时末段合并为「其它」由调用方处理）。
 */
export const ShareBar: React.FC<ShareBarProps> = ({ segments, height = 3, className, ariaLabel }) => {
  const visible = segments.filter(s => Number.isFinite(s.value) && s.value > 0).slice(0, 5);
  const total = visible.reduce((sum, s) => sum + s.value, 0);
  if (!visible.length || total <= 0) {
    return <span className={cn('viz-empty', className)} style={{ height }} aria-hidden="true" />;
  }
  return (
    <span className={cn('viz-sharebar', className)} style={{ height }} role="img" aria-label={ariaLabel ?? '构成占比'}>
      {visible.map((segment, index) => (
        <span
          key={segment.key}
          style={{
            width: `${((segment.value / total) * 100).toFixed(2)}%`,
            background: segment.color ?? `var(--chart-${Math.min(index + 1, 5)})`,
          }}
          // 治理规则禁止原生 title（触屏无 tooltip、且不可键盘访问）；
          // 段的语义已经由外层 ShareBar 的 aria-label 与图例文字承担。
          aria-hidden="true"
        />
      ))}
    </span>
  );
};

export type HeatCellProps = {
  /** 0..1 强度；负数走跌色 */
  intensity: number;
  size?: number;
  className?: string;
  ariaLabel?: string;
};

/**
 * 密度格。色阶只用涨跌色 4 档透明度（0.12/0.3/0.55/0.8），不引入新色。
 */
export const HeatCell: React.FC<HeatCellProps> = ({ intensity, size = 10, className, ariaLabel }) => {
  const safe = Number.isFinite(intensity) ? Math.max(-1, Math.min(1, intensity)) : 0;
  const level = Math.abs(safe);
  const opacity = level === 0 ? 0 : level < 0.25 ? 0.12 : level < 0.5 ? 0.3 : level < 0.75 ? 0.55 : 0.8;
  const color = safe < 0 ? CHART_DOWN : CHART_UP;

  return (
    <span
      className={cn('viz-heatcell', className)}
      style={{
        width: size,
        height: size,
        background: opacity ? color : 'var(--bg-3)',
        opacity: opacity || 1,
      }}
      role="img"
      aria-label={ariaLabel ?? `强度 ${(safe * 100).toFixed(0)}%`}
    />
  );
};
