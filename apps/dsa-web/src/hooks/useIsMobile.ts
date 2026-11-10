import { useEffect, useState } from 'react';

const MOBILE_QUERY = '(max-width: 1023px)';

/**
 * 手机端判定。首帧即可拿到正确值（同步读取 matchMedia），
 * 后续跟随视口变化；服务端渲染场景退回桌面布局。
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(MOBILE_QUERY).matches
      : false
  ));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia(MOBILE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setIsMobile(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  return isMobile;
}
