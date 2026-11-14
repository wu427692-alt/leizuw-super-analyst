import type React from 'react';
import { cn } from '../../utils/cn';

interface AppPageProps {
  children: React.ReactNode;
  className?: string;
}

export const AppPage: React.FC<AppPageProps> = ({ children, className = '' }) => {
  // 页面根容器不是地标：应用外壳（Shell / AdminShell）已经提供了全站唯一的 <main>，
  // 这里再套一个 <main> 会让每页出现两个 main landmark（WAI-ARIA 不推荐）。
  // `app-page` 类名与 `data-research-workspace` 的样式选择器都保留，视觉不受影响。
  return (
    <div
      data-research-workspace="true"
      className={cn('app-page mx-auto min-h-full w-full max-w-7xl px-2 pb-6 pt-2 sm:px-4 sm:pb-8 sm:pt-4 md:px-5 lg:px-6', className)}
    >
      {children}
    </div>
  );
};
