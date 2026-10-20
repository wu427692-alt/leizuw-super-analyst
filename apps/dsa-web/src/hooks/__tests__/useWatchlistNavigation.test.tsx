import { useEffect, type ReactNode } from 'react';
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useWatchlistNavigation } from '../useWatchlistNavigation';

const symbols = ['603306.SH', '300308.SZ'];
const start = '/super-watchlist?symbol=603306.SH';
const change = async (action: () => void) => { await act(async () => { action(); }); };
function setup(url = start, initialSymbols: string[] = symbols, previous?: string) {
  const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={previous ? [previous, url] : [url]} initialIndex={previous ? 1 : 0}>{children}</MemoryRouter>;
  return renderHook(({ items }) => ({
    navigation: useWatchlistNavigation(items), location: useLocation(), navigate: useNavigate(),
  }), { initialProps: { items: initialSymbols }, wrapper });
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('watchlist URL reading position', () => {
  it('keeps old links working without adding default navigation parameters', () => {
    const { result } = setup();
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[0], requestedSymbol: symbols[0], section: 'overview', materialFilter: 'all', messageFilter: 'news', query: '', page: 1 });
    expect(result.current.location.search).toBe('?symbol=603306.SH');
  });

  it('restores a complete deep link without waiting for or writing a data source', () => {
    const { result } = setup(`${start}&section=institution&material=company_direct&message=enterprise&query=${encodeURIComponent('利润 & 现金流')}&page=3`);
    expect(result.current.navigation).toMatchObject({ section: 'institution', primarySection: { key: 'evidence' }, materialFilter: 'company_direct', messageFilter: 'enterprise', query: '利润 & 现金流', page: 3 });
  });

  it.each(['0', '-1', '1.5', 'no', 'Infinity', '9007199254740992', '1e2'])('defaults malformed page %s safely without changing the stock or filters', value => {
    const { result } = setup(`${start}&section=messages&message=enterprise&query=新闻&page=${value}`);
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[0], section: 'messages', messageFilter: 'enterprise', query: '新闻', page: 1 });
  });

  it('defaults unknown section and filter enums to the existing safe scopes', () => {
    const { result } = setup(`${start}&section=bad&material=bad&message=bad`);
    expect(result.current.navigation).toMatchObject({ section: 'overview', materialFilter: 'all', messageFilter: 'news' });
  });

  it('does not touch the URL before symbols arrive and preserves a valid linked query when they do', async () => {
    const url = '/super-watchlist?symbol=300308.SZ&section=messages&message=enterprise&query=信用&page=2';
    const { result, rerender } = setup(url, []);
    expect(result.current.navigation.activeSymbol).toBe('');
    expect(result.current.location.search).toBe(url.slice(url.indexOf('?')));
    await change(() => rerender({ items: symbols }));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[1], query: '信用', page: 2, messageFilter: 'enterprise' });
    expect(result.current.location.search).toBe(url.slice(url.indexOf('?')));
  });

  it.each([false, true])('clears a removed resolved stock reading position on fallback, including an empty-list gap (%s)', async emptyGap => {
    const { result, rerender } = setup(`${start}&section=institution&material=announcements&message=enterprise&query=华懋&page=2`, symbols, '/before');
    if (emptyGap) {
      await change(() => rerender({ items: [] }));
      expect(result.current.navigation.activeSymbol).toBe('');
      expect(result.current.navigation).toMatchObject({ query: '华懋', page: 2 });
    }
    await change(() => rerender({ items: [symbols[1]] }));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[1], requestedSymbol: symbols[1], section: 'institution', materialFilter: 'announcements', messageFilter: 'enterprise', query: '', page: 1 });
    const params = new URLSearchParams(result.current.location.search);
    expect(params.has('query')).toBe(false);
    expect(params.has('page')).toBe(false);
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    await change(() => result.current.navigation.selectPrimary('evidence'));
    expect(result.current.navigation).toMatchObject({ section: 'institution', query: '', page: 1 });
    await change(() => result.current.navigate(-1));
    await change(() => result.current.navigate(-1));
    await change(() => result.current.navigate(-1));
    expect(result.current.location.pathname).toBe('/before');
  });

  it.each(['', '&symbol=removed'])('normalizes the resolved stock by replace without erasing the linked reading position (%s)', async stock => {
    const { result } = setup(`/super-watchlist?section=institution&material=announcements&query=半年报&page=2${stock}`, symbols, '/before');
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[0], requestedSymbol: symbols[0], query: '半年报', page: 2, materialFilter: 'announcements' });
    await change(() => result.current.navigate(-1));
    expect(result.current.location.pathname).toBe('/before');
  });

  it('uses replace for typing and push for pagination, so history restores a position rather than each keystroke', async () => {
    const { result } = setup(`${start}&section=institution&material=announcements&keep=1`, symbols, '/before');
    await change(() => result.current.navigation.setQuery('新'));
    await change(() => result.current.navigation.setQuery('新闻 & 订单'));
    await change(() => result.current.navigation.setPage(2));
    expect(result.current.navigation).toMatchObject({ query: '新闻 & 订单', page: 2 });
    expect(new URLSearchParams(result.current.location.search).get('keep')).toBe('1');
    await change(() => result.current.navigate(-1));
    expect(result.current.navigation).toMatchObject({ query: '新闻 & 订单', page: 1 });
    await change(() => result.current.navigate(1));
    expect(result.current.navigation.page).toBe(2);
    await change(() => result.current.navigate(-1));
    await change(() => result.current.navigate(-1));
    expect(result.current.location.pathname).toBe('/before');
  });

  it('restores the last library subtab, types, query and page after a financial detour', async () => {
    const { result } = setup(`${start}&section=institution&material=announcements&query=新闻&page=2`);
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    expect(result.current.navigation.section).toBe('fundamental');
    await change(() => result.current.navigation.selectSection('capital'));
    await change(() => result.current.navigation.selectPrimary('evidence'));
    expect(result.current.navigation).toMatchObject({ section: 'institution', materialFilter: 'announcements', query: '新闻', page: 2 });
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    expect(result.current.navigation.section).toBe('capital');
  });

  it('remembers separate subtab positions without carrying a keyword into a first-time tab', async () => {
    const { result } = setup(`${start}&section=institution&material=research&query=盈利&page=3`);
    await change(() => result.current.navigation.selectSection('messages'));
    expect(result.current.navigation).toMatchObject({ query: '', page: 1, messageFilter: 'news' });
    await change(() => result.current.navigation.selectMessage('enterprise'));
    await change(() => result.current.navigation.setQuery('风险'));
    await change(() => result.current.navigation.setPage(2));
    await change(() => result.current.navigation.selectSection('institution'));
    expect(result.current.navigation).toMatchObject({ materialFilter: 'research', query: '盈利', page: 3 });
    await change(() => result.current.navigation.selectSection('messages'));
    expect(result.current.navigation).toMatchObject({ messageFilter: 'enterprise', query: '风险', page: 2 });
  });

  it('makes the active primary, subtab and symbol controls no-ops, including browser history', async () => {
    const { result } = setup(`${start}&section=institution&material=announcements&query=公告&page=2`, symbols, '/before');
    const key = result.current.location.key;
    await change(() => result.current.navigation.selectPrimary('evidence'));
    await change(() => result.current.navigation.selectSection('institution'));
    await change(() => result.current.navigation.selectSymbol(symbols[0]));
    expect(result.current.location.key).toBe(key);
    expect(result.current.navigation).toMatchObject({ query: '公告', page: 2, materialFilter: 'announcements' });
    await change(() => result.current.navigate(-1));
    expect(result.current.location.pathname).toBe('/before');
  });

  it('lets explicit overview entries replace a remembered scope instead of inheriting its filters', async () => {
    const { result } = setup(`${start}&section=messages&message=enterprise&query=风险&page=2`);
    await change(() => result.current.navigation.selectPrimary('overview'));
    await change(() => result.current.navigation.openSection('messages'));
    expect(result.current.navigation).toMatchObject({ section: 'messages', messageFilter: 'news', materialFilter: 'all', query: '', page: 1 });
    await change(() => result.current.navigation.openSection('institution', 'announcements'));
    expect(result.current.navigation).toMatchObject({ section: 'institution', materialFilter: 'announcements', query: '', page: 1 });
    await change(() => result.current.navigation.setQuery('半年报'));
    await change(() => result.current.navigation.setPage(2));
    await change(() => result.current.navigation.openSection('institution', 'research'));
    expect(result.current.navigation).toMatchObject({ materialFilter: 'research', query: '', page: 1 });
  });

  it('resets only the page on type changes, and clearFilters keeps the selected message type', async () => {
    const { result } = setup(`${start}&section=messages&material=research&query=记录&page=2`);
    await change(() => result.current.navigation.selectMessage('enterprise'));
    expect(result.current.navigation).toMatchObject({ query: '记录', page: 1, messageFilter: 'enterprise' });
    await change(() => result.current.navigation.setPage(2));
    await change(() => result.current.navigation.selectMaterial('announcements'));
    expect(result.current.navigation).toMatchObject({ query: '记录', page: 1, materialFilter: 'announcements' });
    await change(() => result.current.navigation.clearFilters());
    expect(result.current.navigation).toMatchObject({ query: '', page: 1, materialFilter: 'all', messageFilter: 'enterprise' });
    const params = new URLSearchParams(result.current.location.search);
    expect(params.has('query')).toBe(false);
    expect(params.has('page')).toBe(false);
    expect(params.has('material')).toBe(false);
    expect(params.get('message')).toBe('enterprise');
  });

  it('clears query and page on a real stock switch, preserves types and does not restore another stock memory', async () => {
    const { result } = setup(`${start}&section=messages&material=research&message=enterprise&query=华懋&page=2`);
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    await change(() => result.current.navigation.selectPrimary('evidence'));
    await change(() => result.current.navigation.selectSymbol(symbols[1]));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[1], section: 'messages', materialFilter: 'research', messageFilter: 'enterprise', query: '', page: 1 });
    await change(() => result.current.navigation.setQuery('中际'));
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    await change(() => result.current.navigation.selectPrimary('evidence'));
    expect(result.current.navigation.query).toBe('中际');
    await change(() => result.current.navigation.selectSymbol(symbols[0]));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[0], query: '', page: 1 });
    await change(() => result.current.navigation.selectPrimary('fundamental'));
    await change(() => result.current.navigation.selectPrimary('evidence'));
    expect(result.current.navigation.query).toBe('');
  });

  it('uses the historical stock and its URL position when navigating back across a stock switch', async () => {
    const { result } = setup(`${start}&section=messages&message=enterprise&query=华懋&page=2`);
    await change(() => result.current.navigation.selectSymbol(symbols[1]));
    await change(() => result.current.navigate(-1));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[0], query: '华懋', page: 2, messageFilter: 'enterprise' });
    await change(() => result.current.navigate(1));
    expect(result.current.navigation).toMatchObject({ activeSymbol: symbols[1], query: '', page: 1, messageFilter: 'enterprise' });
  });

  it.each(['/chat', '/tasks'])('restores URL state after genuine route unmount and return from %s, without persistent storage or network', async target => {
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    const fetch = vi.spyOn(globalThis, 'fetch');
    let navigation!: ReturnType<typeof useWatchlistNavigation>;
    let navigate!: ReturnType<typeof useNavigate>;
    function Controls() {
      const current = useNavigate();
      useEffect(() => { navigate = current; }, [current]);
      return null;
    }
    function Workspace() {
      const current = useWatchlistNavigation(symbols);
      useEffect(() => { navigation = current; }, [current]);
      return <output data-testid="reading-position">{JSON.stringify({ section: current.section, material: current.materialFilter, query: current.query, page: current.page })}</output>;
    }
    render(<MemoryRouter initialEntries={[`${start}&section=institution&material=announcements`]}><Controls /><Routes>
      <Route path="/super-watchlist" element={<Workspace />} />
      <Route path="/chat" element={<div>chat placeholder</div>} />
      <Route path="/tasks" element={<div>tasks placeholder</div>} />
    </Routes></MemoryRouter>);
    await change(() => navigation.setQuery('新闻'));
    await change(() => navigation.setPage(2));
    await change(() => navigate(target));
    expect(screen.queryByTestId('reading-position')).not.toBeInTheDocument();
    await change(() => navigate(-1));
    expect(JSON.parse(screen.getByTestId('reading-position').textContent!)).toEqual({ section: 'institution', material: 'announcements', query: '新闻', page: 2 });
    expect(storage).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
