import type React from 'react';
import { Languages } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

type UiLanguageToggleVariant = 'default' | 'nav' | 'rail';

interface UiLanguageToggleProps {
  variant?: UiLanguageToggleVariant;
  collapsed?: boolean;
  wrapperClassName?: string;
  triggerClassName?: string;
  /** 保留以兼容旧调用方；语言切换没有选中态，不使用。 */
  triggerActiveClassName?: string;
  iconClassName?: string;
  labelClassName?: string;
}

export const UiLanguageToggle: React.FC<UiLanguageToggleProps> = ({
  variant = 'default',
  collapsed = false,
  wrapperClassName,
  triggerClassName,
  iconClassName,
  labelClassName,
}) => {
  const { language, setLanguage, t } = useUiLanguage();
  const nextLanguage = language === 'zh' ? 'en' : 'zh';
  const isNavVariant = variant === 'nav';
  const isRailVariant = variant === 'rail';
  const label = language === 'zh' ? t('language.uiLanguage') : t('language.current');

  return (
    <div className={cn('relative', isRailVariant ? 'w-full' : '', wrapperClassName)}>
      <button
        type="button"
        onClick={() => setLanguage(nextLanguage)}
        className={cn(
          triggerClassName
            ? triggerClassName
            : isRailVariant
              ? 'flex h-10 w-full items-center justify-start gap-2 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-body)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1'
              : isNavVariant
                ? 'group relative flex h-10 w-full select-none items-center justify-start gap-2 rounded-[var(--radius-control)] px-3 text-[length:var(--fs-body)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1'
                : 'inline-flex h-9 min-w-9 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-line-1 bg-bg-2 px-2 text-[length:var(--fs-sm)] text-text-2 transition-colors hover:bg-bg-3 hover:text-text-1',
          // 语言切换是动作而非当前页，不套用选中态（_triggerActiveClassName 仅为接口兼容）
          isNavVariant && collapsed ? 'justify-center px-2' : ''
        )}
        aria-label={t('language.toggle')}
        title={t('language.toggle')}
      >
        <Languages aria-hidden="true" className={iconClassName ?? cn('shrink-0', isRailVariant ? 'h-[18px] w-[18px]' : isNavVariant ? 'h-5 w-5' : 'h-4 w-4')} />
        {isRailVariant ? (
          <span className={labelClassName}>{t('language.uiLanguage')}</span>
        ) : isNavVariant ? (
          collapsed ? null : <span className="truncate">{label}</span>
        ) : (
          <span className="hidden sm:inline">{label}</span>
        )}
      </button>
    </div>
  );
};
