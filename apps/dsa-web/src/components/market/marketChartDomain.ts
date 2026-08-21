export type PercentRangeRow = {
  stageLowPercent?: number;
  stageHighPercent?: number;
};

type IntradayBaseRow = { close?: number | null };

/**
 * Resolve the price represented by the intraday zero axis.
 * A one-day chart must never silently substitute its first/current price when
 * the upstream previous close is missing; showing no percentage is safer than
 * displaying a fabricated zero axis.
 */
export function resolveIntradayBasePrice(
  preClose: number | null | undefined,
  rows: IntradayBaseRow[],
  range: '1d' | '5d',
): number | null {
  if (range === '1d') {
    return preClose != null && Number.isFinite(preClose) && preClose > 0 ? preClose : null;
  }
  const first = rows.find((row) => row.close != null && Number.isFinite(row.close) && row.close > 0)?.close;
  return first != null ? first : null;
}

export function adaptivePercentDomain(rows: PercentRangeRow[]): [number, number] {
  const values = rows.flatMap((row) => [row.stageLowPercent, row.stageHighPercent])
    .filter((value): value is number => value != null && Number.isFinite(value));
  if (!values.length) return [-0.1, 0.1];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low;
  const padding = Math.max(span * 0.08, Math.max(Math.abs(low), Math.abs(high)) * 0.015, 0.02);
  return [low - padding, high + padding];
}

type TooltipChangeRow = {
  close?: number | null;
  changePercent?: number | null;
};

/** Resolve the percentage shown for the exact hovered bar. */
export function tooltipChangePercent(
  row: TooltipChangeRow,
  basePrice: number | null | undefined,
  intraday: boolean,
): number | null {
  if (!intraday) {
    return row.changePercent != null && Number.isFinite(row.changePercent)
      ? row.changePercent
      : null;
  }
  if (row.close != null && Number.isFinite(row.close) && basePrice && Number.isFinite(basePrice)) {
    return ((row.close - basePrice) / basePrice) * 100;
  }
  return row.changePercent != null && Number.isFinite(row.changePercent)
    ? row.changePercent
    : null;
}
