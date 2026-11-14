import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { StockLogo } from '../StockLogo';
import {
  normalizeLogoCode,
  stockLogoColor,
  stockLogoInitial,
  stockLogoUrl,
} from '../stockLogoUtils';

afterEach(cleanup);

function logoRoot(container: HTMLElement): HTMLElement | null {
  return container.querySelector<HTMLElement>('[data-stock-logo]');
}

function logoImage(container: HTMLElement): HTMLImageElement | null {
  return container.querySelector<HTMLImageElement>('img');
}

describe('StockLogo', () => {
  it('renders the backend logo lazily with a fixed box and the initial placeholder', () => {
    const { container } = render(<StockLogo symbol="300750" name="宁德时代" size={26} />);
    const img = logoImage(container);
    const root = logoRoot(container);

    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('/api/v1/stocks/300750/logo');
    expect(img?.getAttribute('loading')).toBe('lazy');
    expect(img?.getAttribute('width')).toBe('26');
    expect(img?.getAttribute('height')).toBe('26');

    // 固定宽高 + 统一圆角：图片没回来之前盒子已经占好位置，不会抖动。
    expect(root?.style.width).toBe('26px');
    expect(root?.style.height).toBe('26px');
    expect(root?.style.borderRadius).not.toBe('');
    // 加载中先显示首字，避免出现空框或破图。
    expect(container.querySelector('[data-stock-logo-initial]')?.textContent).toBe('宁');
  });

  it('keeps only the real logo after the image loads', () => {
    const { container } = render(<StockLogo symbol="300750" name="宁德时代" />);
    const img = logoImage(container);
    const root = logoRoot(container);
    expect(img).not.toBeNull();
    expect(root?.style.backgroundColor).not.toBe('');

    fireEvent.load(img as HTMLImageElement);

    expect(logoImage(container)).not.toBeNull();
    expect(container.querySelector('[data-stock-logo-initial]')).toBeNull();
    // logo 自带底板，加载完成后撤销品牌色底，避免出现随机色环。
    expect(root?.style.backgroundColor).toBe('transparent');
  });

  it('falls back to the initial block and drops the broken image when loading fails', () => {
    const { container } = render(<StockLogo symbol="300750" name="宁德时代" />);
    fireEvent.error(logoImage(container) as HTMLImageElement);

    // 破图必须被卸载，而不是留一个加载失败的 <img>。
    expect(logoImage(container)).toBeNull();
    expect(container.querySelector('[data-stock-logo-initial]')?.textContent).toBe('宁');
  });

  it('goes straight to the initial block for markets without a logo source', () => {
    const { container } = render(<StockLogo symbol="hk00700" name="腾讯控股" />);

    expect(logoImage(container)).toBeNull();
    expect(container.querySelector('[data-stock-logo-initial]')?.textContent).toBe('腾');
  });

  it('renders a neutral placeholder for an empty symbol', () => {
    const { container } = render(<StockLogo symbol="" name="" />);

    expect(logoImage(container)).toBeNull();
    expect(container.querySelector('[data-stock-logo-initial]')?.textContent).toBe('?');
  });

  it('keeps one color per company but spreads colors across companies', () => {
    expect(stockLogoColor('300750')).toBe(stockLogoColor('300750'));
    // 同一家公司的不同写法必须同色。
    expect(stockLogoColor('300750')).toBe(stockLogoColor('sz300750'));
    expect(stockLogoColor('300750')).not.toBe(stockLogoColor('600519'));

    const { container } = render(<StockLogo symbol="600519" name="贵州茅台" />);
    expect(logoRoot(container)?.style.backgroundColor).not.toBe('');
  });
});

describe('StockLogo helpers', () => {
  it('normalizes A-share symbols and rejects other markets', () => {
    expect(normalizeLogoCode('300750')).toBe('300750');
    expect(normalizeLogoCode('sz300750')).toBe('300750');
    expect(normalizeLogoCode('600519.SH')).toBe('600519');
    expect(normalizeLogoCode(' 000001 ')).toBe('000001');
    expect(normalizeLogoCode('hk00700')).toBeNull();
    expect(normalizeLogoCode('AAPL')).toBeNull();
    expect(normalizeLogoCode('')).toBeNull();
  });

  it('builds the logo URL only for A-share codes', () => {
    expect(stockLogoUrl('300750')).toBe('/api/v1/stocks/300750/logo');
    expect(stockLogoUrl('sz300750')).toBe('/api/v1/stocks/300750/logo');
    expect(stockLogoUrl('hk00700')).toBeNull();
  });

  it('derives the initial from the company name, then from the code', () => {
    expect(stockLogoInitial('宁德时代', '300750')).toBe('宁');
    expect(stockLogoInitial(' apple ', 'AAPL')).toBe('A');
    expect(stockLogoInitial(undefined, '300750')).toBe('3');
    expect(stockLogoInitial(undefined, 'sz300750')).toBe('3');
    expect(stockLogoInitial('', '')).toBe('?');
  });
});
