import { useRef } from 'react';
import { cn } from '../../utils/cn';

type SegmentedChipsProps<T extends string> = {
  items: Array<{ value: T; label: string; badge?: string | number }>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
};

/**
 * 手机端页内分段控件：横向可滚动的 chip 组，选中项品牌描边 + 浅底。
 * 只用于页内视图切换；页面级导航仍由底部 tab 承担。
 */
export function SegmentedChips<T extends string>({ items, value, onChange, ariaLabel, className }: SegmentedChipsProps<T>) {
  const listRef = useRef<HTMLDivElement>(null);

  return (
    <div ref={listRef} className={cn('mobile-chips', className)} role="tablist" aria-label={ariaLabel}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            className={cn('mobile-chip', active && 'is-active')}
            onClick={() => onChange(item.value)}
          >
            <span>{item.label}</span>
            {item.badge !== undefined ? <b>{item.badge}</b> : null}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedChips;
