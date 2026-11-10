import React from 'react';
import { cn } from '../../utils/cn';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'history';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
  glow?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

/* Tag 只有三种长相：status（语义色浅底）、neutral（中性底）、brand（品牌浅底）。
   info/history 是历史遗留名，按语义归入 status 与 neutral，不再另起颜色。 */
const variantStyles: Record<BadgeVariant, string> = {
  default: 'border-transparent bg-[var(--bg-2)] text-[var(--text-3)]',
  success: 'border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success)]',
  warning: 'border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning)]',
  danger: 'border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)]',
  info: 'border-transparent bg-[var(--brand-tint-08)] text-[var(--brand-text)]',
  history: 'border-transparent bg-[var(--bg-2)] text-[var(--text-3)]',
};

const glowStyles: Record<BadgeVariant, string> = {
  default: '',
  success: '',
  warning: '',
  danger: '',
  info: '',
  history: '',
};

/**
 * Badge component with multiple variants and optional glow styling.
 */
export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  size = 'sm',
  glow = false,
  className = '',
  style,
  ...rest
}) => {
  const sizeStyles = size === 'sm'
    ? 'h-5 px-2 text-[length:var(--fs-caption)] leading-[16px]'
    : 'h-7 px-2 text-[length:var(--fs-sm)] leading-[20px]';

  return (
    <span
      {...rest}
      style={style}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-[var(--radius-tag)] border font-medium',
        sizeStyles,
        variantStyles[variant],
        glow && glowStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  );
};
