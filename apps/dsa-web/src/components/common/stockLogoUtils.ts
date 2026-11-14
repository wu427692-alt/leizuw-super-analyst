import { API_BASE_URL } from '../../utils/constants';

/** 是否真的去请求远端 logo：目前只有 6 位 A 股代码有公司 logo 数据源。 */
const A_SHARE_SYMBOL_PATTERN = /^(?:sh|sz|bj)?(\d{6})$/i;
const MARKET_SUFFIX_PATTERN = /\.(?:sh|sz|bj)$/i;

/** 「暂无 logo」的色块配色：同一 symbol 永远同色，且色相足够散开。 */
export function stockLogoColor(symbol: string): string {
  // 300750 / sz300750 / 600519.SH 指向同一家公司，色块必须同色。
  const seed = (normalizeLogoCode(symbol) ?? (symbol || '').trim()).toUpperCase();
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  // 色相决定「是哪家公司」，饱和度/亮度只做轻微扰动，保证白字可读。
  const hue = hash % 360;
  const saturation = 42 + ((hash >>> 8) % 16);
  const lightness = 34 + ((hash >>> 16) % 12);
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
}

/** 色块上显示的首字：优先公司名首字，没有名字时退回代码首字符。 */
export function stockLogoInitial(name: string | undefined, symbol: string): string {
  const display = (name || '').trim();
  if (display) {
    const [first] = Array.from(display);
    return first ? first.toUpperCase() : '?';
  }
  const [first] = Array.from((normalizeLogoCode(symbol) ?? (symbol || '')).trim());
  return first ? first.toUpperCase() : '?';
}

/** 把 300750 / sz300750 / 600519.SH 统一成 6 位 A 股代码；其它市场返回 null。 */
export function normalizeLogoCode(symbol: string): string | null {
  const text = (symbol || '').trim().replace(MARKET_SUFFIX_PATTERN, '');
  const match = text.match(A_SHARE_SYMBOL_PATTERN);
  return match ? match[1] : null;
}

/** 后端 logo 地址：后端抓取并缓存公司 logo，返回 404 时前端降级为首字色块。 */
export function stockLogoUrl(symbol: string): string | null {
  const code = normalizeLogoCode(symbol);
  if (!code) return null;
  // 与 apiClient 的 baseURL 保持同一口径；默认同源。
  const base = (API_BASE_URL || '').replace(/\/+$/, '');
  return `${base}/api/v1/stocks/${code}/logo`;
}
