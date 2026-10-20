import { describe, expect, it } from 'vitest';
import type { MonitorEvent, StockResearchRelation } from '../../types/investmentMonitor';
import { directResearchFirst, stockResearchRelation } from '../stockResearchRelation';

const report = (id: number, stockRelation?: StockResearchRelation): MonitorEvent => ({
  id, eventType: 'research_report_pdf', symbols: ['300308.SZ'], stockRelation,
} as MonitorEvent);

describe('stock research attribution', () => {
  it('never promotes the recall symbols index into a direct-company report', () => {
    expect(stockResearchRelation(report(1), '300308.SZ')).toEqual({ kind: 'unknown', targetSymbol: '300308.SZ' });
  });

  it.each(['company_direct', 'industry_related', 'other_company_mention', 'unknown'] as const)('preserves %s from the exact stock-scoped response', kind => {
    const relation = { kind, targetSymbol: '300308.SZ', matchField: 'abstract', matchText: '摘要实际提及中际旭创。' };
    expect(stockResearchRelation(report(1, relation), '300308.SZ')).toEqual(relation);
  });

  it('does not reuse another stock relation, excerpt or subject name', () => {
    expect(stockResearchRelation(report(1, { kind: 'company_direct', targetSymbol: '603306.SH', subjectName: '华懋科技', matchText: '不应显示' }), '300308.SZ'))
      .toEqual({ kind: 'unknown', targetSymbol: '300308.SZ' });
  });

  it('rejects contradictory subjects and unknown response kinds', () => {
    expect(stockResearchRelation(report(1, { kind: 'company_direct', targetSymbol: '300308.SZ', subjectSymbol: '300684.SZ' }), '300308.SZ')?.kind).toBe('unknown');
    expect(stockResearchRelation(report(1, { kind: 'made_up' as StockResearchRelation['kind'], targetSymbol: '300308.SZ' }), '300308.SZ')?.kind).toBe('unknown');
    expect(stockResearchRelation(report(1, { kind: 'company_direct', targetSymbol: '300308.SZ', hasSubjectConflict: true }), '300308.SZ')?.kind).toBe('unknown');
  });

  it('does not relabel non-research announcements or opinions', () => {
    expect(stockResearchRelation({ ...report(1), eventType: 'essay' }, '300308.SZ')).toBeNull();
    expect(stockResearchRelation({ ...report(1), eventType: 'announcement' }, '300308.SZ')).toBeNull();
  });

  it('does not crash the whole page on malformed optional API attribution metadata', () => {
    expect(stockResearchRelation(report(1, { kind: 'company_direct', targetSymbol: 300308 } as unknown as StockResearchRelation), '300308.SZ')?.kind).toBe('unknown');
    expect(stockResearchRelation(report(1, { kind: 'other_company_mention', targetSymbol: '300308.SZ', subjectName: { bad: true }, matchText: ['bad'] } as unknown as StockResearchRelation), '300308.SZ'))
      .toMatchObject({ kind: 'other_company_mention', subjectName: null, matchText: null });
  });

  it('prioritizes direct research without mutating input or changing ordering within groups', () => {
    const rows = [report(1), report(2, { kind: 'company_direct', targetSymbol: '300308.SZ' }), report(3), report(4, { kind: 'company_direct', targetSymbol: '300308.SZ' })];
    expect(directResearchFirst(rows, '300308.SZ').map(row => row.id)).toEqual([2, 4, 1, 3]);
    expect(rows.map(row => row.id)).toEqual([1, 2, 3, 4]);
  });
});
