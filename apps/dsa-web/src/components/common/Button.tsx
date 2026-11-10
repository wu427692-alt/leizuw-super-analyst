import React from 'react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'gradient' | 'danger' | 'danger-subtle' | 'settings-primary' | 'settings-secondary' | 'action-primary' | 'action-secondary' | 'home-action-ai' | 'home-action-report';
  size?: 'xsm' | 'sm' | 'md' | 'lg' | 'xl';
  isLoading?: boolean;
  /** Custom loading text. */
  loadingText?: string;
  glow?: boolean;
}

/* 只有四档高度：28 / 32 / 36 / 44 */
const BUTTON_SIZE_STYLES = {
  xsm: 'h-7 rounded-[var(--radius-control)] px-2 text-[length:var(--fs-caption)]',
  sm: 'h-8 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-sm)]',
  md: 'h-9 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-sm)]',
  lg: 'h-9 rounded-[var(--radius-control)] px-4 text-[length:var(--fs-sm)]',
  xl: 'h-11 rounded-[var(--radius-control)] px-4 text-[length:var(--fs-body)]',
} as const;

const ACTION_AI_STYLES = 'bg-[var(--home-action-ai-bg)] border border-[var(--home-action-ai-border)] text-[var(--home-action-ai-text)] hover:bg-[var(--home-action-ai-hover-bg)]';
const ACTION_REPORT_STYLES = 'bg-[var(--home-action-report-bg)] border border-[var(--home-action-report-border)] text-[var(--home-action-report-text)] hover:bg-[var(--home-action-report-hover-bg)]';

const BUTTON_VARIANT_STYLES = {
  primary: 'border bg-[var(--brand)] text-[color:var(--brand-on)] border-[var(--brand)] hover:bg-[var(--brand-hover)] hover:border-[var(--brand-hover)]',
  secondary: 'border bg-transparent text-[color:var(--text-2)] border-[var(--line-2)] hover:bg-[var(--bg-3)] hover:text-[color:var(--text-1)]',
  'settings-primary': 'border settings-button-primary hover:brightness-105 hover:shadow-xl',
  'settings-secondary': 'border settings-button-secondary hover:translate-y-[-1px]',
  outline: 'border bg-transparent text-[color:var(--text-2)] border-[var(--line-2)] hover:bg-[var(--bg-3)]',
  ghost: 'border border-transparent bg-transparent text-[color:var(--text-2)] hover:bg-[var(--bg-3)] hover:text-[color:var(--text-1)]',
  // 渐变与发光按钮并入 primary，避免同一语义多种长相
  gradient: 'border bg-[var(--brand)] text-[color:var(--brand-on)] border-[var(--brand)] hover:bg-[var(--brand-hover)]',
  danger: 'border bg-[var(--danger)] text-[color:var(--brand-on)] border-[var(--danger)] hover:brightness-110',
  'danger-subtle': 'border bg-[var(--danger-bg)] text-[color:var(--danger)] border-[var(--danger-border)] hover:brightness-105',
  'action-primary': ACTION_AI_STYLES,
  'action-secondary': ACTION_REPORT_STYLES,
  'home-action-ai': ACTION_AI_STYLES,
  'home-action-report': ACTION_REPORT_STYLES,
} as const;

/**
 * Button component with multiple variants and terminal-inspired styling.
 */
export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  loadingText,
  glow = false,
  className = '',
  disabled,
  type = 'button',
  ...props
}) => {
  const { t } = useUiLanguage();
  const glowStyles = glow ? '' : '';  // 暗色下不再使用外发光

  return (
    <button
      type={type}
      aria-busy={isLoading || undefined}
      data-variant={variant}
      className={cn(
        'inline-flex cursor-pointer items-center justify-center gap-2 font-medium transition-all duration-200',
        'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan/15 focus-visible:ring-offset-0',
        'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 disabled:transform-none',
        BUTTON_SIZE_STYLES[size],
        BUTTON_VARIANT_STYLES[variant],
        glowStyles,
        className,
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <span className="flex items-center justify-center gap-2">
          <svg
            className="h-4 w-4 animate-spin text-current"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          {loadingText ?? t('common.processing')}
        </span>
      ) : (
        children
      )}
    </button>
  );
};
