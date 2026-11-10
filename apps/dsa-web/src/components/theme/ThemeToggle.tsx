import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { Check, Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';

type ThemeOption = 'light' | 'dark' | 'system';
type ThemeToggleVariant = 'default' | 'nav' | 'rail';

const THEME_OPTIONS: Array<{
  value: ThemeOption;
  labelKey: UiTextKey;
  icon: typeof Sun;
}> = [
  { value: 'light', labelKey: 'theme.light', icon: Sun },
  { value: 'dark', labelKey: 'theme.dark', icon: Moon },
  { value: 'system', labelKey: 'theme.system', icon: Monitor },
];

function resolveThemeLabel(theme: string | undefined, t: (key: UiTextKey) => string) {
  switch (theme) {
    case 'light':
      return t('theme.light');
    case 'dark':
      return t('theme.dark');
    default:
      return t('theme.system');
  }
}

interface ThemeToggleProps {
  variant?: ThemeToggleVariant;
  collapsed?: boolean;
  wrapperClassName?: string;
  triggerClassName?: string;
  triggerActiveClassName?: string;
  iconClassName?: string;
  labelClassName?: string;
}

export const ThemeToggle: React.FC<ThemeToggleProps> = ({
  variant = 'default',
  collapsed = false,
  wrapperClassName,
  triggerClassName,
  triggerActiveClassName,
  iconClassName,
  labelClassName,
}) => {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [open]);

  const activeTheme = (theme as ThemeOption | undefined) ?? 'system';
  const visualTheme = resolvedTheme ?? 'dark';
  const TriggerIcon = visualTheme === 'light' ? Sun : Moon;
  const isNavVariant = variant === 'nav';
  const isRailVariant = variant === 'rail';

  return (
    <div className={cn('relative', isRailVariant ? 'w-full' : '', wrapperClassName)} ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        data-state={open ? 'open' : 'closed'}
        className={cn(
          triggerClassName
            ? triggerClassName
            : isRailVariant
              ? 'flex h-10 w-full items-center justify-start gap-2 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-body)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1 data-[state=open]:bg-bg-3 data-[state=open]:text-text-1'
              : isNavVariant
                ? 'group relative flex h-10 w-full select-none items-center justify-start gap-2 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-body)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1 data-[state=open]:bg-bg-3 data-[state=open]:text-text-1'
                : 'inline-flex h-9 min-w-9 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-line-1 bg-bg-2 px-2 text-[length:var(--fs-sm)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1',
          triggerClassName && open ? triggerActiveClassName : '',
          isNavVariant && collapsed ? 'justify-center px-2' : ''
        )}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('theme.toggle')}
      >
        <TriggerIcon className={iconClassName ?? cn('shrink-0', isRailVariant ? 'h-[18px] w-[18px]' : isNavVariant ? 'h-5 w-5' : 'h-4 w-4')} />
        {isRailVariant ? (
          <span className={labelClassName}>{t('theme.theme')}</span>
        ) : isNavVariant ? (
          collapsed ? null : <span className="truncate">{t('theme.theme')}</span>
        ) : (
          <span className="hidden sm:inline">{resolveThemeLabel(activeTheme, t)}</span>
        )}
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t('theme.menu')}
          className={cn(
            'z-[100] min-w-[8rem] rounded-[var(--radius-control)] border border-line-2 bg-bg-1 p-1 shadow-pop',
            isNavVariant || isRailVariant
              ? 'absolute bottom-full left-0 mb-2 w-max min-w-[9rem]'
              : 'absolute right-0 mt-2'
          )}
        >
          {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => {
            const isActive = activeTheme === value;
            return (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                onClick={() => {
                  setTheme(value);
                  setOpen(false);
                }}
                className={cn(
                  'flex h-9 w-full items-center justify-between rounded-[var(--radius-tag)] px-3 text-[length:var(--fs-sm)] transition-colors',
                  isActive
                    ? 'bg-bg-3 text-text-1'
                    : 'text-text-2 hover:bg-bg-3 hover:text-text-1'
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4" />
                  {t(labelKey)}
                </span>
                {isActive ? <Check aria-hidden="true" className="h-4 w-4 text-brand" /> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
};
