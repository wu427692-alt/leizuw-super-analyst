import type React from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '../../utils/cn';

type MobileListRowProps = {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** 右侧主数值（已格式化，带涨跌色即可用 tone 控制） */
  value?: React.ReactNode;
  /** 主数值下方的次要信息 */
  valueNote?: React.ReactNode;
  tone?: 'up' | 'down' | 'flat' | 'default';
  leading?: React.ReactNode;
  trailing?: React.ReactNode;
  onClick?: () => void;
  className?: string;
};

const TONE_CLASS = {
  up: 'text-[color:var(--up)]',
  down: 'text-[color:var(--down)]',
  flat: 'text-[color:var(--flat)]',
  default: 'text-[color:var(--text-1)]',
} as const;

/**
 * 手机端列表行：左侧标题/副标题，右侧数值，整行 60px 起，可点则显示箭头。
 * 表格类内容在手机上统一降级成这种行，而不是横向滚动表格。
 */
export const MobileListRow: React.FC<MobileListRowProps> = ({
  title,
  subtitle,
  value,
  valueNote,
  tone = 'default',
  leading,
  trailing,
  onClick,
  className,
}) => {
  const content = (
    <>
      {leading ? <span className="mobile-row__leading">{leading}</span> : null}
      <span className="mobile-row__main">
        <strong>{title}</strong>
        {subtitle ? <small>{subtitle}</small> : null}
      </span>
      {value !== undefined ? (
        <span className={cn('mobile-row__value', TONE_CLASS[tone])}>
          <b>{value}</b>
          {valueNote ? <small>{valueNote}</small> : null}
        </span>
      ) : null}
      {trailing}
      {onClick ? <ChevronRight aria-hidden="true" className="mobile-row__chevron" /> : null}
    </>
  );

  if (onClick) {
    return (
      <button type="button" className={cn('mobile-row', className)} onClick={onClick}>
        {content}
      </button>
    );
  }

  return <div className={cn('mobile-row', className)}>{content}</div>;
};

type MobileGroupProps = {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

/** 手机端分组容器：标题 + 一张卡，内部行与行之间只用分隔线。 */
export const MobileGroup: React.FC<MobileGroupProps> = ({ title, action, children, className }) => (
  <section className={cn('mobile-group', className)}>
    {title || action ? (
      <header className="mobile-group__head">
        {title ? <h2>{title}</h2> : <span />}
        {action}
      </header>
    ) : null}
    <div className="mobile-group__body">{children}</div>
  </section>
);

type SkeletonProps = { rows?: number; className?: string };

/** 骨架屏：形状与真实内容一致，避免居中转圈。 */
export const MobileSkeleton: React.FC<SkeletonProps> = ({ rows = 3, className }) => (
  <div className={cn('mobile-skeleton', className)} aria-hidden="true">
    {Array.from({ length: rows }).map((_, index) => (
      <div key={index} className="mobile-skeleton__line" />
    ))}
  </div>
);
