import { useState } from 'react';
import type React from 'react';
import { cn } from '../../utils/cn';
import { stockLogoColor, stockLogoInitial, stockLogoUrl } from './stockLogoUtils';

export interface StockLogoProps {
  /** 股票代码，如 300750 / sz300750 / hk00700 */
  symbol: string;
  /** 公司名，用作首字降级文案 */
  name?: string;
  /** 边长（px），图片与色块共用，避免布局抖动 */
  size?: number;
  className?: string;
}

type ImageState = 'loading' | 'loaded' | 'error';

type FrameProps = StockLogoProps & { src: string | null; resolvedSize: number };

/**
 * 一层壳：固定尺寸 + 首字色块始终垫底，真实 logo 盖在上面。
 * 拆成内部组件并让外层用 key 强制重挂载，符号变化时图片状态自动复位。
 */
const StockLogoFrame: React.FC<FrameProps> = ({ symbol, name, className, src, resolvedSize }) => {
  const [imageState, setImageState] = useState<ImageState>('loading');

  const initial = stockLogoInitial(name, symbol);
  const background = stockLogoColor(symbol);
  const radius = Math.max(4, Math.round(resolvedSize * 0.27));
  const showImage = Boolean(src) && imageState !== 'error';
  const showInitial = !showImage || imageState !== 'loaded';

  return (
    <span
      className={cn('shrink-0 overflow-hidden', className)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flex: '0 0 auto',
        position: 'relative',
        width: resolvedSize,
        height: resolvedSize,
        borderRadius: radius,
        // 真实 logo 自带白色圆形底板，加载完成后去掉色块底色，避免随机色环干扰观感；
        // 加载中/失败时保留品牌色，保证任何时刻都有可读的占位块。
        backgroundColor: imageState === 'loaded' ? 'transparent' : background,
        color: '#fff',
        fontWeight: 600,
        lineHeight: 1,
        userSelect: 'none',
      }}
      aria-hidden="true"
      data-stock-logo={symbol}
    >
      {showInitial ? (
        <span
          data-stock-logo-initial=""
          style={{ fontSize: Math.max(10, Math.round(resolvedSize * 0.48)), letterSpacing: 0 }}
        >
          {initial}
        </span>
      ) : null}
      {showImage ? (
        <img
          src={src ?? undefined}
          alt=""
          width={resolvedSize}
          height={resolvedSize}
          loading="lazy"
          decoding="async"
          draggable={false}
          onLoad={() => setImageState('loaded')}
          onError={() => setImageState('error')}
          style={{
            position: 'absolute',
            inset: 0,
            display: 'block',
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            borderRadius: 'inherit',
            opacity: imageState === 'loaded' ? 1 : 0,
            transition: 'opacity 120ms ease',
          }}
        />
      ) : null}
    </span>
  );
};

/**
 * 公司标识：真实 logo → 加载失败 → 公司名首字色块。
 *
 * 三层降级：
 *   1. 真实 logo：/api/v1/stocks/{code}/logo（后端从同花顺公司资料页抓取并本地缓存）；
 *   2. 图片加载失败：收到 error 事件后卸载 <img>，保留首字色块；
 *   3. 没有 logo 可用（非 A 股代码 / 代码为空）：直接渲染首字色块。
 *
 * 首字色块的颜色由 symbol 的稳定 hash 决定（同一代码永远同色），
 * 尺寸固定、圆角统一，图片始终 loading="lazy"，因此不会出现破图或跳动。
 */
export const StockLogo: React.FC<StockLogoProps> = ({ symbol, name, size = 26, className }) => {
  const src = stockLogoUrl(symbol);
  return (
    <StockLogoFrame
      key={`${src ?? 'initial'}|${symbol}`}
      symbol={symbol}
      name={name}
      className={className}
      src={src}
      resolvedSize={size}
    />
  );
};

export default StockLogo;
