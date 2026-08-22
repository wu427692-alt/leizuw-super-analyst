import type React from 'react';
import { cn } from '../../utils/cn';

interface AppPageProps {
  children: React.ReactNode;
  className?: string;
}

export const AppPage: React.FC<AppPageProps> = ({ children, className = '' }) => {
  return (
    <main className={cn('app-page mx-auto min-h-full w-full max-w-7xl px-2 pb-6 pt-2 sm:px-4 sm:pb-8 sm:pt-4 md:px-6 lg:px-8', className)}>
      {children}
    </main>
  );
};
