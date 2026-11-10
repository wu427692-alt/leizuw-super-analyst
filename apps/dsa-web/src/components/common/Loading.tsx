import React from 'react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface LoadingProps {
  label?: string;
  className?: string;
}

export const Loading: React.FC<LoadingProps> = ({ label, className = '' }) => {
  const { t } = useUiLanguage();

  return (
    <div className={`flex items-center justify-center px-4 py-12 ${className}`} role="status">
      <div className="inline-flex items-center gap-2 text-[length:var(--fs-sm)] leading-[var(--lh-sm)] text-[color:var(--text-3)]">
        <svg className="h-4 w-4 animate-spin text-[color:var(--brand)]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
          <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        {label ?? t('common.loading')}
      </div>
    </div>
  );
};
