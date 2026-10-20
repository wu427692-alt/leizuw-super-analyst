import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

export type Section = 'overview' | 'fundamental' | 'capital' | 'institution' | 'essay' | 'consensus' | 'messages' | 'comments' | 'evidence';
export type PrimarySection = 'overview' | 'fundamental' | 'consensus' | 'evidence';
export const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: 'overview', label: '全景' }, { key: 'fundamental', label: '财务估值' },
  { key: 'capital', label: '资金筹码' }, { key: 'institution', label: '公告研报' },
  { key: 'essay', label: '小作文' }, { key: 'consensus', label: '一致预期' },
  { key: 'messages', label: '新闻与提示' }, { key: 'comments', label: '股评监控' },
  { key: 'evidence', label: '研究资料' },
];
export const PRIMARY_SECTIONS: Array<{ key: PrimarySection; label: string; members: Section[] }> = [
  { key: 'overview', label: '总览', members: ['overview'] },
  { key: 'fundamental', label: '财务与资金', members: ['fundamental', 'capital'] },
  { key: 'consensus', label: '一致预期', members: ['consensus'] },
  { key: 'evidence', label: '资料库', members: ['evidence', 'institution', 'essay', 'messages', 'comments'] },
];
export const MATERIAL_FILTERS = [
  { value: 'all', label: '研究资料' }, { value: 'announcements', label: '公司公告' },
  { value: 'research', label: '券商研究' }, { value: 'company_direct', label: '本公司研究' },
  { value: 'industry_related', label: '行业研究' }, { value: 'other_company_mention', label: '其他公司资料' },
  { value: 'unknown', label: '待确认研报' },
  { value: 'snapshots', label: '数据快照' },
] as const;
export type MaterialFilter = typeof MATERIAL_FILTERS[number]['value'];
export const MESSAGE_FILTERS = [
  { value: 'news', label: '相关新闻', empty: '暂无已载入新闻' },
  { value: 'opinions', label: '机构观点', empty: '暂无已载入机构观点' },
  { value: 'enterprise', label: '企业提示', empty: '暂无已载入企业提示' },
  { value: 'all', label: '全部消息', empty: '暂无已载入消息' },
] as const;
export type MessageFilter = typeof MESSAGE_FILTERS[number]['value'];

type ReadingPosition = {
  section: Section;
  materialFilter: MaterialFilter;
  messageFilter: MessageFilter;
  query: string;
  page: number;
};
type ReadingMemory = {
  symbol: string;
  positions: Partial<Record<Section, ReadingPosition>>;
  primary: Partial<Record<PrimarySection, Section>>;
};

const defaultPosition = (section: Section): ReadingPosition => ({ section, materialFilter: 'all', messageFilter: 'news', query: '', page: 1 });
const validPage = (value: number) => Number.isSafeInteger(value) && value > 0 ? value : 1;
const readPage = (value: string | null) => value && /^[1-9]\d*$/.test(value) ? validPage(Number(value)) : 1;

/** URL owns the visible position; memory only remembers other tabs of this stock. */
export function useWatchlistNavigation(symbols: readonly string[]) {
  const [params, setParams] = useSearchParams();
  const requestedSymbol = params.get('symbol');
  const activeSymbol = symbols.find(symbol => symbol === requestedSymbol) ?? symbols[0] ?? '';
  const section = SECTIONS.find(item => item.key === params.get('section'))?.key ?? 'overview';
  const primarySection = PRIMARY_SECTIONS.find(item => item.members.includes(section))!;
  const materialFilter = MATERIAL_FILTERS.find(item => item.value === params.get('material'))?.value ?? 'all';
  const messageFilter = MESSAGE_FILTERS.find(item => item.value === params.get('message'))?.value ?? 'news';
  const query = params.get('query') ?? '';
  const page = readPage(params.get('page'));
  const position = useMemo(() => ({ section, materialFilter, messageFilter, query, page }), [section, materialFilter, messageFilter, query, page]);
  const memory = useRef<ReadingMemory>({ symbol: '', positions: {}, primary: {} });

  const remember = useCallback((value: ReadingPosition) => {
    if (!activeSymbol) return;
    if (memory.current.symbol !== activeSymbol) memory.current = { symbol: activeSymbol, positions: {}, primary: {} };
    memory.current.positions[value.section] = value;
    memory.current.primary[PRIMARY_SECTIONS.find(item => item.members.includes(value.section))!.key] = value.section;
  }, [activeSymbol]);

  useEffect(() => {
    if (activeSymbol && requestedSymbol !== activeSymbol) {
      // First resolution preserves deep links. If a previously resolved stock
      // disappears, its keyword/page must not become the fallback stock's state.
      const changedResolvedStock = Boolean(memory.current.symbol && memory.current.symbol !== activeSymbol);
      setParams(previous => {
        if (previous.get('symbol') !== requestedSymbol) return previous;
        const next = new URLSearchParams(previous);
        next.set('symbol', activeSymbol);
        if (changedResolvedStock) { next.delete('query'); next.delete('page'); }
        return next;
      }, { replace: true });
    }
  }, [activeSymbol, requestedSymbol, setParams]);
  useEffect(() => {
    // Do not attribute an unresolved/removed symbol's URL to the fallback stock.
    if (requestedSymbol === activeSymbol) remember(position);
  }, [activeSymbol, requestedSymbol, position, remember]);

  const commit = (value: ReadingPosition, replace = false) => {
    const next = new URLSearchParams(params);
    if (value.section === 'overview') next.delete('section'); else next.set('section', value.section);
    if (value.materialFilter === 'all') next.delete('material'); else next.set('material', value.materialFilter);
    if (value.messageFilter === 'news') next.delete('message'); else next.set('message', value.messageFilter);
    if (value.query) next.set('query', value.query); else next.delete('query');
    if (value.page === 1) next.delete('page'); else next.set('page', String(value.page));
    if (next.toString() !== params.toString()) setParams(next, { replace });
  };
  const selectSection = (value: Section) => {
    if (value === section) return;
    remember(position);
    commit(memory.current.positions[value] ?? defaultPosition(value));
  };
  const selectPrimary = (value: PrimarySection) => {
    if (value === primarySection.key) return;
    remember(position);
    const target = memory.current.primary[value] ?? value;
    commit(memory.current.positions[target] ?? defaultPosition(target));
  };
  const openSection = (value: Section, material: MaterialFilter = 'all') => {
    remember(position);
    commit({ ...defaultPosition(value), materialFilter: material });
  };
  const selectSymbol = (symbol?: string, replace = false) => {
    if (symbol === activeSymbol) return;
    const next = new URLSearchParams(params);
    if (symbol) next.set('symbol', symbol); else next.delete('symbol');
    next.delete('query');
    next.delete('page');
    setParams(next, { replace });
  };

  return {
    activeSymbol, requestedSymbol, section, primarySection, materialFilter, messageFilter, query, page,
    selectSymbol, selectPrimary, selectSection, openSection,
    selectMaterial: (material: MaterialFilter) => commit({ ...position, materialFilter: material, page: 1 }),
    selectMessage: (message: MessageFilter) => commit({ ...position, messageFilter: message, page: 1 }),
    setQuery: (value: string) => commit({ ...position, query: value, page: 1 }, true),
    setPage: (value: number) => commit({ ...position, page: validPage(value) }),
    clearFilters: () => commit({ ...position, materialFilter: 'all', query: '', page: 1 }),
  };
}
