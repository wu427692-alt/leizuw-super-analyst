/**
 * Stock search suggestion list.
 */

import type { CSSProperties } from 'react';
import type { StockSuggestion } from '../../types/stockIndex';
import { Badge } from '../common';
import { cn } from '../../utils/cn';

export interface SuggestionsListProps {
  /** Suggestion list */
  suggestions: StockSuggestion[];
  /** Highlighted index */
  highlightedIndex: number;
  /** Selection callback */
  onSelect: (suggestion: StockSuggestion) => void;
  /** Mouse hover callback */
  onMouseEnter: (index: number) => void;
  /** Custom style (for Portal fixed positioning) */
  style?: CSSProperties;
}

export function SuggestionsList({
  suggestions,
  highlightedIndex,
  onSelect,
  onMouseEnter,
  style,
}: SuggestionsListProps) {
  if (suggestions.length === 0) {
    return null;
  }

  return (
    <ul
      id="suggestions-list"
      className="z-[100] border-x border-b rounded-b-lg rounded-t-none max-h-60 overflow-auto text-left"
      style={{
        ...style,
        backgroundColor: 'hsl(var(--card) / 0.85)',
        borderColor: 'var(--border-accent)',
        boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3), -4px 0 15px -3px rgba(0, 0, 0, 0.2), 4px 0 15px -3px rgba(0, 0, 0, 0.2)',
      }}
      role="listbox"
    >
      {suggestions.map((suggestion, index) => (
        <li
          key={suggestion.canonicalCode}
          role="option"
          aria-selected={index === highlightedIndex}
          className={cn(
            // 候选行不允许被挤压换行：名称/代码不折行，徽标不收缩，
            // 空间不足时只截断名称（title 由 aria-label 承担可访问名）。
            'px-3 py-1.5 cursor-pointer flex items-center justify-between gap-2 whitespace-nowrap',
            'hover:bg-[var(--autocomplete-hover-bg)]/25',
            index === highlightedIndex && 'bg-[var(--autocomplete-hover-bg)]/25',
          )}
          onClick={() => onSelect(suggestion)}
          onMouseEnter={() => onMouseEnter(index)}
        >
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0"><MarketBadge market={suggestion.market} /></span>

            <span className="flex min-w-0 items-baseline gap-2">
              <span className="truncate text-sm font-medium text-primary-text">
                {suggestion.nameZh}
              </span>
              <span className="shrink-0 text-[12px] text-secondary-text tabular-nums">
                {suggestion.displayCode}
              </span>
            </span>
          </div>

          <span className="shrink-0"><MatchTypeBadge matchType={suggestion.matchType} /></span>
        </li>
      ))}
    </ul>
  );
}

const MARKET_BADGE_CONFIG = {
  CN: { label: 'A股', className: 'border-danger/25 bg-danger/10 text-danger' },
  HK: { label: '港股', className: 'border-success/25 bg-success/10 text-success' },
  US: { label: '美股', className: 'border-cyan/25 bg-cyan/10 text-cyan' },
  JP: { label: '日股', className: 'border-[var(--line-2)] bg-[var(--bg-3)] text-[var(--text-2)]' },
  KR: { label: '韩股', className: 'border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)]' },
  INDEX: { label: '指数', className: 'border-purple/25 bg-purple/10 text-purple' },
  ETF: { label: 'ETF', className: 'border-warning/25 bg-warning/10 text-warning' },
  BSE: { label: '北交所', className: 'border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning)]' },
} as const;

function MarketBadge({ market }: { market: string }) {
  const config = MARKET_BADGE_CONFIG[market as keyof typeof MARKET_BADGE_CONFIG] ?? {
    label: market || '其他',
    className: 'border-border/55 bg-elevated/75 text-muted-text',
  };

  return (
    <Badge variant="default" size="sm" className={cn('min-w-[3rem] justify-center shadow-none', config.className)}>
      {config.label}
    </Badge>
  );
}

function MatchTypeBadge({ matchType }: { matchType: string }) {
  const configMap = {
    exact: { label: '精确', className: 'border-cyan/25 bg-cyan/10 text-cyan' },
    prefix: { label: '前缀', className: 'border-purple/25 bg-purple/10 text-purple' },
    contains: { label: '包含', className: 'border-warning/25 bg-warning/10 text-warning' },
    fuzzy: { label: '模糊', className: 'border-border/55 bg-elevated/75 text-muted-text' },
  };

  const config = configMap[matchType as keyof typeof configMap] || configMap.fuzzy;

  return (
    <Badge variant="default" size="sm" className={cn('shrink-0 shadow-none', config.className)}>
      {config.label}
    </Badge>
  );
}

export default SuggestionsList;
