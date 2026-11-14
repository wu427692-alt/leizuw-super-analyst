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
  // 桌面端的外壳（Shell）已经给了 24px 页边与 24px 顶部留白（--page-x / lg:px-6 lg:py-6），
  // 这里再叠一层桌面内边距会造成「左右 48px、顶部 40px」的双重留白，内容区被压窄。
  // 所以：桌面端只保留底部留白，横向与顶部交给外壳；移动端外壳没有内边距，仍由本组件提供。
  // 默认最大宽对齐 §5 的 1680，而不是 1280（1440 视口下 1280 会白扔 160px 给两侧）。
  return (
    <div
      data-research-workspace="true"
      className={cn('app-page mx-auto min-h-full w-full max-w-[1680px] px-2 pb-6 pt-2 sm:px-4 sm:pb-8 sm:pt-4 md:px-5 lg:px-0 lg:pb-8 lg:pt-0', className)}
    >
      {children}
    </div>
  );
};
