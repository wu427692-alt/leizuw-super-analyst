import { describe, expect, it } from 'vitest';
import { adaptivePercentDomain, resolveIntradayBasePrice, tooltipChangePercent } from '../marketChartDomain';

describe('MarketTimeframeChart intraday axis', () => {
  it('uses prior trading-day close as the one-day zero axis after the session closes', () => {
    expect(resolveIntradayBasePrice(70.11, [{ close: 74.28 }], '1d')).toBe(70.11);
  });

  it('does not replace a missing prior close with the current session price', () => {
    expect(resolveIntradayBasePrice(null, [{ close: 74.28 }], '1d')).toBeNull();
  });

  it('fits a positive visible range without forcing zero or a symmetric ten-percent scale', () => {
    const domain = adaptivePercentDomain([
      { stageLowPercent: 2.1, stageHighPercent: 2.6 },
      { stageLowPercent: 2.4, stageHighPercent: 3.2 },
    ]);

    expect(domain[0]).toBeGreaterThan(1.9);
    expect(domain[1]).toBeLessThan(3.4);
    expect(domain[0]).toBeGreaterThan(0);
  });

  it('fits a negative visible range without adding unused positive space', () => {
    const domain = adaptivePercentDomain([
      { stageLowPercent: -4.7, stageHighPercent: -3.8 },
      { stageLowPercent: -4.1, stageHighPercent: -3.2 },
    ]);

    expect(domain[0]).toBeGreaterThan(-5);
    expect(domain[1]).toBeLessThan(-3);
  });
});

describe('MarketTimeframeChart tooltip facts', () => {
  it('uses the API period change for K-line bars instead of open-to-close movement', () => {
    expect(tooltipChangePercent({ close: 102, changePercent: -1.25 }, null, false)).toBe(-1.25);
  });

  it('uses the displayed intraday baseline for an exact hovered minute', () => {
    expect(tooltipChangePercent({ close: 101, changePercent: 99 }, 100, true)).toBeCloseTo(1);
  });

  it('does not invent a percentage when neither source value nor baseline exists', () => {
    expect(tooltipChangePercent({ close: 101 }, null, true)).toBeNull();
  });
});
