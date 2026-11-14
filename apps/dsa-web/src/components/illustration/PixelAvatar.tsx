/**
 * 像素头像：给角色列表和股票行一个能认出来的小人。
 *
 * 14×14 的头肩像，发型、发色、衣服颜色由 seed 哈希决定（同一个 seed 恒定同一张脸）。
 * 颜色走 --pixel-* 变量，深浅主题各一套，不用图片资源。
 */
import type React from 'react';

// 14 宽 × 14 高：头发 3 种轮廓、两种刘海，脸留出 1px 的眼睛位
const HAIR_STYLES = [
  [
    '.....hhhh.....',
    '....hhhhhh....',
    '...hhhhhhhh...',
    '...hhsssshh...',
    '...hsssssssh..',
  ],
  [
    '....hhhhhh....',
    '...hhhhhhhh...',
    '..hhhhhhhhhh..',
    '..hhsssssshh..',
    '..hssssssssh..',
  ],
  [
    '...hhhhhhhh...',
    '..hhhhhhhhhh..',
    '..hhhhhhhhhh..',
    '..hssssssssh..',
    '..hssssssssh..',
  ],
];

const FACE = [
  '..ssssssss....',
  '..ssessess....',
  '..ssssssss....',
  '...ssssss.....',
  '....ssss......',
];

const BODY = [
  '....bbbb......',
  '...bbbbbb.....',
  '..bbbbbbbb....',
  '.sbbbbbbbbs...',
  '.sbbbbbbbbs...',
];

const HAIR_TONES = ['var(--pixel-hair)', 'var(--pixel-hair-2)', 'var(--pixel-hair-3)', 'var(--pixel-hair-4)', 'var(--pixel-hair-5)'];
const CLOTH_TONES = [
  'var(--pixel-cloth-1)', 'var(--pixel-cloth-2)', 'var(--pixel-cloth-3)',
  'var(--pixel-cloth-4)', 'var(--pixel-cloth-5)',
];

function hash(text: string): number {
  let value = 0;
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) >>> 0;
  return value;
}

interface PixelAvatarProps {
  seed: string;
  size?: number;
  className?: string;
}

export const PixelAvatar: React.FC<PixelAvatarProps> = ({ seed, size = 32, className }) => {
  const h = hash(seed || '?');
  const rows = [
    ...HAIR_STYLES[h % HAIR_STYLES.length],
    ...FACE,
    ...BODY,
  ];
  const fill: Record<string, string> = {
    h: HAIR_TONES[(h >> 3) % HAIR_TONES.length],
    s: 'var(--pixel-skin)',
    e: 'var(--pixel-eye)',
    b: CLOTH_TONES[(h >> 6) % CLOTH_TONES.length],
  };

  return (
    <svg
      className={['pixel-avatar', className].filter(Boolean).join(' ')}
      width={size}
      height={size}
      viewBox="0 0 14 14"
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      {rows.flatMap((row, y) => [...row].map((ch, x) => (
        ch === '.' ? null : <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={fill[ch]} />
      )))}
    </svg>
  );
};

export default PixelAvatar;
