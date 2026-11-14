/**
 * 像素小人插画。
 *
 * 定位：弱细节、弱存在感的点缀，不是主视觉。低饱和三色 + 一个品牌色像素，
 * 整体不透明度 0.8。用在页面头部右侧、空状态，不放进数据区。
 *
 * 用 SVG <rect> 画，crispEdges 保证任意缩放都是清晰像素；颜色走 --pixel-* 变量，
 * 深浅主题各给一组值，不需要两套图。
 */
import type React from 'react';

// 10 x 12 网格。字符含义：
//   h 头发  s 皮肤  e 眼睛/眼镜  b 衣服  t 领带/强调色  l 腿  . 空
const FIGURES: Record<string, string[]> = {
  analyst: [
    '...hhhh...',
    '..hhhhhh..',
    '..hssssh..',
    '..seesss..',
    '..ssssss..',
    '...ssss...',
    '..bbttbb..',
    '.bbbttbbb.',
    '.sbbbbbbs.',
    '..bbbbbb..',
    '..ll..ll..',
    '..ll..ll..',
  ],
  reader: [
    '...hhhh...',
    '..hhhhhh..',
    '..hsssss..',
    '..sesess..',
    '..ssssss..',
    '...ssss...',
    '..bbbbbb..',
    '.bbtttttb.',
    '.sbtttttbs',
    '..bbbbbb..',
    '..ll..ll..',
    '..ll..ll..',
  ],
  chief: [
    '..hhhhhh..',
    '.hhhhhhhh.',
    '..hssssh..',
    '..eeeeee..',
    '..ssssss..',
    '...ssss...',
    '..bbttbb..',
    '.bbbttbbb.',
    '.sbbttbbs.',
    '..bbbbbb..',
    '..ll..ll..',
    '..ll..ll..',
  ],
};

// 低饱和配色：只用肤色/深发/灰衣三种，强调像素单独用品牌色。
// 颜色走变量，深浅主题各自在 design-tokens.css 里给值。
const PALETTE: Record<string, string> = {
  h: 'var(--pixel-hair)',
  s: 'var(--pixel-skin)',
  e: 'var(--pixel-eye)',
  b: 'var(--pixel-cloth)',
  l: 'var(--pixel-leg)',
};

export type PixelFigure = keyof typeof FIGURES;

interface PixelPersonProps {
  figure?: PixelFigure;
  /** 单个像素的边长（px） */
  pixel?: number;
  /** 唯一的强调像素（领带/书本）是否用品牌色 */
  accent?: boolean;
  className?: string;
}

export const PixelPerson: React.FC<PixelPersonProps> = ({ figure = 'analyst', pixel = 4, accent = true, className }) => {
  const rows = FIGURES[figure];
  const w = rows[0].length;
  const h = rows.length;
  const rects: React.ReactNode[] = [];
  rows.forEach((row, y) => {
    [...row].forEach((ch, x) => {
      if (ch === '.') return;
      if (ch === 't') {
        rects.push(
          <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1}
            fill={accent ? 'var(--brand, #FF4F00)' : 'var(--pixel-cloth)'} />,
        );
        return;
      }
      rects.push(<rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={PALETTE[ch] ?? 'currentColor'} />);
    });
  });
  return (
    <svg
      className={className}
      width={w * pixel}
      height={h * pixel}
      viewBox={`0 0 ${w} ${h}`}
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      {rects}
    </svg>
  );
};

interface PixelCrewProps {
  /** 人数，1–3 */
  count?: 1 | 2 | 3;
  pixel?: number;
  className?: string;
}

/** 一小排像素研究员，站在一条细地平线上。 */
export const PixelCrew: React.FC<PixelCrewProps> = ({ count = 3, pixel = 3, className }) => {
  const order: PixelFigure[] = ['reader', 'chief', 'analyst'];
  const crew = count === 1 ? ['chief' as PixelFigure] : order.slice(0, count);
  return (
    <div className={['pixel-crew', className].filter(Boolean).join(' ')} aria-hidden="true">
      {crew.map((figure, index) => (
        <PixelPerson key={figure} figure={figure} pixel={pixel} accent={figure === 'chief'}
          className={index === 1 ? 'pixel-crew__lead' : undefined} />
      ))}
    </div>
  );
};

export default PixelCrew;
