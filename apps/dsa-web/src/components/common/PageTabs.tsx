import type React from 'react';
import { cn } from '../../utils/cn';

/**
 * 页面级主 Tab（`docs/ui-design-system.md` §10）。
 *
 * 用途：把互斥视图从同一屏里拆开，默认只渲染当前 Tab，从而控制单页信息量。
 * 只有一种选中态：`--text-1` 文字 + 2px 品牌下划线，和其它容器内的选中态不混用。
 */
export type PageTabItem = {
  /** 稳定标识，写进 URL 或本地记忆 */
  key: string;
  label: string;
  /** 右侧计数/状态，例如条数；不上色，避免和涨跌色混用 */
  badge?: React.ReactNode;
  disabled?: boolean;
};

export type PageTabsProps = {
  items: PageTabItem[];
  value: string;
  onChange: (key: string) => void;
  /** 右侧操作槽，最多放 1 个次要操作 */
  extra?: React.ReactNode;
  className?: string;
  ariaLabel?: string;
};

export const PageTabs: React.FC<PageTabsProps> = ({
  items, value, onChange, extra, className, ariaLabel = '视图切换',
}) => (
  <div className={cn('page-tabs', className)} role="tablist" aria-label={ariaLabel}>
    <div className="page-tabs__list">
      {items.map(item => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={item.disabled}
            className={cn('page-tabs__item', active && 'is-active')}
            onClick={() => { if (!active && !item.disabled) onChange(item.key); }}
          >
            <span>{item.label}</span>
            {item.badge != null ? <em className="page-tabs__badge">{item.badge}</em> : null}
          </button>
        );
      })}
    </div>
    {extra ? <div className="page-tabs__extra">{extra}</div> : null}
  </div>
);

export default PageTabs;
