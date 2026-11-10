import type { CSSProperties } from 'react';

/** 图表专用：序列按序号取色，不按业务含义；单序列用 CHART_SERIES[0]。品牌橙不进图表。 */
export const CHART_SERIES = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)'] as const;

export const CHART_UP = 'var(--up)';
export const CHART_DOWN = 'var(--down)';
export const CHART_FLAT = 'var(--flat)';
export const CHART_GRID = 'var(--line-1)';
export const CHART_AXIS = 'var(--text-3)';

/** Recharts Tooltip 的统一外观。 */
export const CHART_TOOLTIP_STYLE: CSSProperties = {
  background: 'var(--bg-1)',
  border: '1px solid var(--line-2)',
  borderRadius: 8,
  boxShadow: 'var(--shadow-pop)',
  color: 'var(--text-1)',
  fontSize: 12,
  lineHeight: '18px',
};

export const CHART_TOOLTIP_LABEL_STYLE: CSSProperties = { color: 'var(--text-2)', fontSize: 12 };
