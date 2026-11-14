import { useEffect, useState } from 'react';

const MOBILE_QUERY = '(max-width: 1023px)';

/**
 * 手机端判定。首帧即可拿到正确值（同步读取 matchMedia），
 * 后续跟随视口变化；服务端渲染场景退回桌面布局。
 */
function useMediaQuery(mediaQuery: string): boolean {
  const [matches, setMatches] = useState(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(mediaQuery).matches
      : false
  ));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia(mediaQuery);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, [mediaQuery]);

  return matches;
}

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY);
}

/** 手机竖屏（<768px）：乐小研 v2 高保真版式只在这一档生效，平板保持原有手机布局。 */
export function useIsPhone(): boolean {
  return useMediaQuery('(max-width: 767px)');
}
