import type React from 'react';
import { cn } from '../../utils/cn';

interface StatCardProps {
  /** Metric label, such as "Total Return". */
  label: string;
  /** Metric value, including numbers or percentages. */
  value: React.ReactNode;
  /** Supporting text, such as "Up 5% vs last month". */
  hint?: React.ReactNode;
  /** Optional trailing icon. */
  icon?: React.ReactNode;
  /** Tone variant that affects the border color. */
  tone?: 'default' | 'primary' | 'success' | 'warning' | 'danger';
  /** Optional extra className. */
  className?: string;
}

const toneStyles = {
  default: 'border-subtle',
  primary: 'border-cyan/18',
  success: 'border-success/18',
  warning: 'border-warning/18',
  danger: 'border-danger/18',
};

export const StatCard: React.FC<StatCardProps> = ({
  label,
  value,
  hint,
  icon,
  tone = 'default',
  className = '',
}) => {
  return (
    <div className={cn('research-stat-card border bg-card/90 p-4', toneStyles[tone], className)}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[length:var(--fs-caption)] font-medium leading-[var(--lh-caption)] text-[color:var(--text-3)]">{label}</p>
          <div className="mt-1 text-[length:var(--fs-num-lg)] font-semibold leading-[var(--lh-num-lg)] text-[color:var(--text-1)] tabular-nums">{value}</div>
          {hint ? <div className="mt-1 text-[length:var(--fs-sm)] leading-[var(--lh-sm)] text-[color:var(--text-3)]">{hint}</div> : null}
        </div>
        {icon ? <div className="text-cyan">{icon}</div> : null}
      </div>
    </div>
  );
};
