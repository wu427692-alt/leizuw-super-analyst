import type { MonitorEvent, StockResearchRelation } from '../types/investmentMonitor';

const RESEARCH_TYPES = new Set([
  'research_report_pdf', 'institution_forecast', 'institution_survey', 'broker_recommendation',
]);
const RELATION_KINDS = new Set(['company_direct', 'industry_related', 'other_company_mention', 'unknown']);

export const isResearchEventType = (eventType: string) => RESEARCH_TYPES.has(eventType);

export const RESEARCH_RELATION_LABELS: Record<StockResearchRelation['kind'], string> = {
  company_direct: '本公司研究', industry_related: '行业研究',
  other_company_mention: '其他公司资料', unknown: '关联待确认',
};

export const RESEARCH_MATCH_FIELDS: Record<string, string> = {
  subject_code: '原始标的代码', title: '标题', abstract: '摘要',
  industry: '行业字段', source_contract: '数据源标的',
};

export function stockResearchRelation(event: MonitorEvent, symbol: string): StockResearchRelation | null {
  if (!isResearchEventType(event.eventType)) return null;
  const target = symbol.trim().toUpperCase();
  const relation = event.stockRelation;
  // The broad symbols index only proves recall, never that the report covers
  // this company directly. Old/mismatched responses must not imply relevance.
  if (!relation || typeof relation.targetSymbol !== 'string' || relation.targetSymbol.trim().toUpperCase() !== target
    || !RELATION_KINDS.has(relation.kind) || relation.hasSubjectConflict
    || (relation.kind === 'company_direct' && relation.subjectSymbol
      && (typeof relation.subjectSymbol !== 'string' || relation.subjectSymbol.trim().toUpperCase() !== target))) return { kind: 'unknown', targetSymbol: target };
  const safe = { ...relation };
  for (const key of ['subjectSymbol', 'subjectName', 'matchField', 'matchText'] as const) {
    if (safe[key] != null && typeof safe[key] !== 'string') safe[key] = null;
  }
  return safe;
}

export function directResearchFirst(events: MonitorEvent[], symbol: string): MonitorEvent[] {
  return [...events].sort((a, b) => Number(stockResearchRelation(b, symbol)?.kind === 'company_direct')
    - Number(stockResearchRelation(a, symbol)?.kind === 'company_direct'));
}
