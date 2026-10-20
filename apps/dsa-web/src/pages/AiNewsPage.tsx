import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, ChevronLeft, ChevronRight, Clock3, Layers3, Newspaper, Radio, RefreshCw, Search, X } from 'lucide-react';
import './AiNewsPage.css';

type NewsView = 'hot' | 'latest' | 'sources';
type NewsWindow = '24h' | '7d' | '30d';
type NewsSource = {
  id: string; name: string; url: string; kind: string;
  last_success: string | null; last_attempt: string | null;
  last_checked_at?: string | null; latest_published_at?: string | null;
  next_check_at?: string | null; interval_seconds?: number;
  status: string; error: string | null; item_count: number;
};
type NewsItem = {
  id: string; title: string; summary?: string; url: string;
  source_id: string; source_name: string; publisher: string;
  published_at: string | null; discovered_at: string; topic: string; topic_label: string;
  related: Array<{ title: string; url: string; source_name: string; published_at: string | null }>;
  source_count: number; score: number;
};
export type AiNewsFeed = {
  items: NewsItem[]; total: number; page: number; page_size: number; pages: number;
  updated_at: string | null; content_updated_at?: string | null;
  latest_published_at?: string | null; last_checked_at?: string | null;
  refreshing: boolean; stale: boolean;
  sources: NewsSource[]; topics: Array<{ id: string; label: string; count: number }>;
  stats: { items: number; sources: number; healthy_sources: number; multi_source_events: number; items_24h?: number; items_12h?: number };
  methodology: string;
};
type Filters = { view: NewsView; q: string; topic: string; source: string; window: NewsWindow; page: number };

function initialFilters(): Filters {
  const params = new URLSearchParams(window.location.search);
  const view = params.get('view'); const period = params.get('window');
  return {
    view: view === 'hot' || view === 'sources' ? view : 'latest',
    q: (params.get('q') ?? '').slice(0, 200), topic: params.get('topic') ?? '', source: params.get('source') ?? '',
    window: period === '24h' || period === '30d' ? period : '7d',
    page: Math.max(1, Math.min(10000, Number.parseInt(params.get('page') ?? '1', 10) || 1)),
  };
}
function formatDate(value: string | null | undefined, timeOnly = false): string {
  if (!value) return '暂无时间'; const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', ...(!timeOnly ? { month: '2-digit' as const, day: '2-digit' as const } : {}), hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
function dateKey(item: NewsItem): string {
  const value = item.published_at || item.discovered_at;
  if (Number.isNaN(Date.parse(value))) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric' }).format(new Date(value));
}
function externalUrl(value: string): string | undefined {
  try { const parsed = new URL(value); return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : undefined; }
  catch { return undefined; }
}
function articleContent(items: NewsItem[]): string {
  return JSON.stringify(items.map(({ id, title, summary, url, source_id, source_name, publisher, published_at, discovered_at, topic, topic_label, related, source_count }) => ({ id, title, summary, url, source_id, source_name, publisher, published_at, discovered_at, topic, topic_label, related, source_count })).sort((a, b) => a.id.localeCompare(b.id)));
}
function useNewsFeed(query: string) {
  const [result, setResult] = useState<{ feed: AiNewsFeed; query: string } | null>(null);
  const resultRef = useRef(result);
  const [nextFeed, setNextFeed] = useState<AiNewsFeed | null>(null);
  const [checkedAt, setCheckedAt] = useState(() => Date.now());
  const [pending, setPending] = useState(true); const [error, setError] = useState('');
  const activeRequest = useRef<AbortController | null>(null);
  const accept = useCallback((feed: AiNewsFeed) => {
    const next = { feed, query }; resultRef.current = next; setResult(next); setNextFeed(null);
  }, [query]);
  const refresh = useCallback(async (explicit = false) => {
    if (activeRequest.current) return;
    const controller = new AbortController(); activeRequest.current = controller;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    setPending(true); setError('');
    try {
      const response = await fetch(`/api/v1/ai-news/feed?${query}`, { credentials: 'same-origin', signal: controller.signal, headers: { Accept: 'application/json' } });
      if (response.status === 401) { window.location.assign(`/access?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`); return; }
      if (!response.ok) throw new Error(`暂时无法读取新闻（HTTP ${response.status}）`);
      const feed: AiNewsFeed = await response.json();
      if (!Array.isArray(feed.items) || !Array.isArray(feed.sources) || !Array.isArray(feed.topics)) throw new Error('新闻数据格式异常，请稍后重试');
      if (activeRequest.current !== controller || controller.signal.aborted) return;
      setCheckedAt(Date.now()); const previous = resultRef.current;
      // New rows wait for explicit acceptance instead of interrupting reading.
      if (!explicit && previous?.query === query && previous.feed.items.length && articleContent(previous.feed.items) !== articleContent(feed.items)) {
        setNextFeed(feed);
        const retained = { query, feed: { ...previous.feed, sources: feed.sources, stats: feed.stats, last_checked_at: feed.last_checked_at, latest_published_at: feed.latest_published_at, stale: feed.stale, refreshing: feed.refreshing } };
        resultRef.current = retained; setResult(retained);
      }
      else accept(!explicit && previous?.query === query && previous.feed.items.length ? { ...feed, items: previous.feed.items } : feed);
    } catch (cause) {
      if (activeRequest.current !== controller || (controller.signal.aborted && !timedOut)) return;
      setCheckedAt(Date.now()); setError(timedOut ? '请求超时，请检查网络后重试' : cause instanceof Error ? cause.message : '网络连接中断，请稍后重试');
    } finally {
      window.clearTimeout(timeout);
      if (activeRequest.current === controller) { activeRequest.current = null; setPending(false); }
    }
  }, [query, accept]);
  useEffect(() => {
    void refresh(true);
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh(); };
    const timer = window.setInterval(onVisible, 60000);
    document.addEventListener('visibilitychange', onVisible); window.addEventListener('focus', onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); window.removeEventListener('focus', onVisible); activeRequest.current?.abort(); activeRequest.current = null; };
  }, [refresh]);
  return { data: result?.feed, nextFeed, accept, checkedAt, previousQuery: !!result && result.query !== query, pending, error, refresh };
}
function SourceStatus({ source, checkedAt }: { source: NewsSource; checkedAt: number }) {
  const success = source.last_success ? Date.parse(source.last_success) : 0;
  const stale = source.status === 'ok' && (!Number.isFinite(success) || checkedAt - success >= (source.interval_seconds ?? 3600) * 3000);
  const healthy = source.status === 'ok' && !stale;
  return <span className={`ai-news-source-status ${healthy ? 'is-ok' : source.status === 'error' || stale ? 'is-error' : ''}`}><i aria-hidden="true" />{healthy ? '正常' : stale ? '陈旧' : source.status === 'error' ? '重试中' : '等待更新'}</span>;
}

export default function AiNewsPage() {
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [searchText, setSearchText] = useState(filters.q);
  const query = new URLSearchParams({ view: filters.view === 'latest' ? 'latest' : 'hot', q: filters.q, topic: filters.topic, source: filters.source, window: filters.window, page: String(filters.page), page_size: '24' }).toString();
  const { data, nextFeed, accept, checkedAt, previousQuery, pending, error, refresh } = useNewsFeed(query);
  const listRef = useRef<HTMLElement>(null); const isSources = filters.view === 'sources';
  const failedSources = data?.sources.filter(source => source.status === 'error') ?? [];
  const selectedFilters = !!(filters.q || filters.topic || filters.source);
  const windowLabel = filters.window === '24h' ? '近 24 小时' : filters.window === '30d' ? '近 30 天' : '近 7 天';
  const newCount = nextFeed?.items.filter(item => !data?.items.some(old => old.id === item.id)).length ?? 0;
  useEffect(() => { document.title = 'AI 新闻热点 · 乐子乌超级价值'; }, []);
  useEffect(() => {
    const params = new URLSearchParams(query); params.delete('page_size'); params.set('view', filters.view);
    for (const key of ['q', 'source', 'topic']) if (!params.get(key)) params.delete(key);
    window.history.replaceState(window.history.state, '', `${window.location.pathname}?${params}`);
  }, [query, filters.view]);
  const updateFilters = (update: Partial<Filters>) => setFilters(current => ({ ...current, ...update, page: update.page ?? 1 }));
  const submitSearch = (event: FormEvent) => { event.preventDefault(); updateFilters({ q: searchText.trim() }); };
  const clearFilters = () => { setSearchText(''); updateFilters({ q: '', source: '', topic: '' }); };
  const turnPage = (page: number) => { updateFilters({ page }); listRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' }); };
  return <div className="ai-news">
    <a className="ai-news-skip" href="#ai-news-content">跳转到新闻列表</a>
    <header className="ai-news-heading">
      <div className="ai-news-heading-title"><span className="ai-news-heading-icon"><Newspaper aria-hidden="true" /></span><div><h1>AI 新闻热点</h1><p>模型、产品与产业动态，持续追踪原始来源。</p></div></div>
      <div className="ai-news-edition"><span><i aria-hidden="true" />{pending ? '正在检查更新' : '自动更新已开启'}</span><strong>{data?.latest_published_at ? `最新新闻 ${formatDate(data.latest_published_at)}` : data?.content_updated_at || data?.updated_at ? `内容更新 ${formatDate(data.content_updated_at || data.updated_at)}` : '等待首轮更新'}</strong></div>
    </header>
    <div className="ai-news-toolbar"><nav className="ai-news-tabs" aria-label="新闻视图">{([{ id: 'latest', label: '最新', Icon: Clock3 }, { id: 'hot', label: '热点', Icon: Layers3 }, { id: 'sources', label: '来源', Icon: Radio }] as const).map(tab => <button type="button" key={tab.id} aria-pressed={filters.view === tab.id} onClick={() => updateFilters({ view: tab.id })}><tab.Icon size={16} aria-hidden="true" />{tab.label}</button>)}</nav>
      {!isSources && <form className="ai-news-search" onSubmit={submitSearch} role="search"><Search size={17} aria-hidden="true" /><input type="search" aria-label="搜索新闻标题" placeholder="搜索标题、公司或产品" maxLength={200} value={searchText} onChange={event => setSearchText(event.target.value)} /><button type="submit">搜索</button></form>}
    </div>
    {!isSources && <section className="ai-news-filter-panel" aria-label="新闻筛选"><div className="ai-news-topic-pills" aria-label="快捷主题"><button type="button" aria-pressed={!filters.topic} onClick={() => updateFilters({ topic: '' })}>全部动态</button>{data?.topics.map(topic => <button key={topic.id} type="button" aria-pressed={filters.topic === topic.id} onClick={() => updateFilters({ topic: topic.id })}>{topic.label}<span>{topic.count}</span></button>)}</div>
      <div className="ai-news-filters"><label className="ai-news-topic-select"><span>主题</span><select value={filters.topic} onChange={event => updateFilters({ topic: event.target.value })}><option value="">全部主题</option>{data?.topics.map(topic => <option key={topic.id} value={topic.id}>{topic.label}</option>)}</select></label><label><span>来源</span><select value={filters.source} onChange={event => updateFilters({ source: event.target.value })}><option value="">全部来源</option>{data?.sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label><label><span>时间</span><select value={filters.window} onChange={event => updateFilters({ window: event.target.value as NewsWindow })}><option value="24h">近 24 小时</option><option value="7d">近 7 天</option><option value="30d">近 30 天</option></select></label>{selectedFilters && <button type="button" className="ai-news-clear" onClick={clearFilters}>清除筛选<X size={13} aria-hidden="true" /></button>}<span className="ai-news-filter-count">{data ? `${previousQuery ? '上次结果 · ' : ''}${data.total} 条动态` : '正在读取新闻'}</span><button className="ai-news-refresh" type="button" disabled={pending} onClick={() => void refresh(true)} aria-label="刷新"><RefreshCw size={14} className={pending ? 'is-spinning' : ''} aria-hidden="true" /><span>{pending ? '同步中' : '检查更新'}</span></button></div>
    </section>}
    <div className="ai-news-status" aria-live="polite">{error && <p className="ai-news-warning" role="alert">{error}。{data ? '保留上次成功读取的结果。' : ''}<button type="button" onClick={() => void refresh(true)} disabled={pending}>重试</button></p>}{previousQuery && <p>{pending ? '正在应用筛选，暂显示上次结果。' : '筛选尚未更新，以下仍为上次结果。'}</p>}{data?.stale && <p className="ai-news-warning">缓存可能已过期，新闻时效请以来源页面为准。</p>}{data?.refreshing && !data.items.length && <p>订阅源正在后台更新，首轮结果生成后会自动显示。</p>}</div>
    <section id="ai-news-content" className="ai-news-content" ref={listRef} tabIndex={-1}>
      {isSources ? <><div className="ai-news-section-heading"><div><h2>来源目录</h2><p>{data?.stats.healthy_sources ?? 0} / {data?.stats.sources ?? 0} 个来源可用 · 统计范围：{windowLabel}。</p></div><button className="ai-news-refresh" type="button" disabled={pending} onClick={() => void refresh(true)}><RefreshCw size={14} aria-hidden="true" />刷新状态</button></div>
        {data ? <div className="ai-news-source-grid">{data.sources.map(source => <article key={source.id} className="ai-news-source-card"><div className="ai-news-source-card-top"><span>{({ official: '官方动态', research: '研究机构', media: '科技媒体' } as Record<string, string>)[source.kind] || 'RSS 订阅'}</span><SourceStatus source={source} checkedAt={checkedAt} /></div><h3><a href={externalUrl(source.url)} target="_blank" rel="noopener noreferrer">{source.name}<ArrowUpRight size={17} aria-hidden="true" /></a></h3><dl><div><dt>当前窗口条目</dt><dd>{source.item_count}</dd></div><div><dt>最近成功</dt><dd>{source.last_success ? formatDate(source.last_success) : '尚无成功记录'}</dd></div><div><dt>最近检查</dt><dd>{source.last_checked_at || source.last_attempt ? formatDate(source.last_checked_at || source.last_attempt) : '尚未尝试'}</dd></div>{source.interval_seconds && <div><dt>检查周期</dt><dd>{Math.round(source.interval_seconds / 60)} 分钟</dd></div>}{source.latest_published_at && <div><dt>最新发布时间</dt><dd>{formatDate(source.latest_published_at)}</dd></div>}</dl>{source.error && <details className="ai-news-source-error"><summary>查看更新异常</summary><p>{source.error}</p>{source.next_check_at && <p>下次检查 {formatDate(source.next_check_at)}</p>}</details>}<button className="ai-news-source-read" type="button" onClick={() => updateFilters({ view: 'latest', source: source.id })}>查看该来源新闻<ArrowRight size={14} aria-hidden="true" /></button></article>)}</div> : <div className="ai-news-empty"><h3>{pending ? '正在读取来源目录' : '来源目录暂不可用'}</h3></div>}
      </> : <div className="ai-news-columns"><div className="ai-news-results"><div className="ai-news-list-heading"><h2>{filters.view === 'hot' ? '热点动态' : '新闻时间线'}</h2><span>{filters.view === 'hot' ? (data?.stats.multi_source_events ? '按独立来源与时效排序' : '当前以新近动态为主') : '按发布时间倒序 · 北京时间'}</span></div>
        {nextFeed && !previousQuery && <button className="ai-news-new-items" type="button" onClick={() => { accept(nextFeed); listRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' }); }}><ArrowDown size={15} aria-hidden="true" />{newCount ? `${newCount} 条新动态，点击查看` : '新闻内容已更新，点击查看'}</button>}
        {data?.items.length ? <ol className="ai-news-list">{data.items.map((item, index) => <Fragment key={item.id}>{filters.view === 'latest' && (index === 0 || dateKey(item) !== dateKey(data.items[index - 1])) && <li className="ai-news-day" role="presentation"><span>{dateKey(item)}</span><i /></li>}<li className="ai-news-story"><article className="ai-news-article"><div className="ai-news-story-time"><time dateTime={item.published_at || item.discovered_at}>{formatDate(item.published_at || item.discovered_at, true)}</time><i aria-hidden="true" /></div><div className="ai-news-article-main"><div className="ai-news-article-meta"><span className="ai-news-publisher-mark" aria-hidden="true">{item.source_name.slice(0, 1).toUpperCase()}</span><span>{item.source_name}</span><span className="ai-news-topic">{item.topic_label || 'AI 动态'}</span><time dateTime={item.published_at || item.discovered_at}>{!item.published_at ? '收录于 ' : ''}{formatDate(item.published_at || item.discovered_at)}</time></div><h3><a href={externalUrl(item.url)} target="_blank" rel="noopener noreferrer">{item.title}<ArrowUpRight className="ai-news-story-arrow" size={17} aria-hidden="true" /></a></h3>{item.summary?.trim() && <p className="ai-news-summary">{item.summary}</p>}<div className="ai-news-article-bottom"><span>{item.summary?.trim() ? '来源摘要' : item.publisher && item.publisher !== item.source_name ? item.publisher : '来源原始标题'}</span><a href={externalUrl(item.url)} target="_blank" rel="noopener noreferrer">阅读原文<ArrowUpRight size={13} aria-hidden="true" /></a></div>{item.related?.length > 0 && <details className="ai-news-related"><summary>{item.source_count} 个独立发布主体 · 查看关联报道</summary><ul>{item.related.map((related, i) => <li key={`${related.url}-${i}`}><a href={externalUrl(related.url)} target="_blank" rel="noopener noreferrer">{related.title}<ArrowUpRight size={13} aria-hidden="true" /></a><span>{related.source_name}{related.published_at ? ` · ${formatDate(related.published_at)}` : ''}</span></li>)}</ul></details>}</div></article></li></Fragment>)}</ol> : <div className="ai-news-empty"><Newspaper size={28} aria-hidden="true" /><h3>{pending && !data ? '正在读取 AI 动态' : data?.refreshing ? '正在准备首批新闻' : error && !data ? '新闻暂时无法读取' : '没有符合条件的新闻'}</h3><p>{data?.refreshing ? '订阅源在后台同步，本页会自动读取更新后的缓存。' : selectedFilters ? '试试其他关键词，或减少筛选条件。' : '可稍后查看订阅源是否已有更新。'}</p>{selectedFilters && <button type="button" onClick={clearFilters}>清除筛选</button>}</div>}
        {data && data.pages > 1 && <nav className="ai-news-pagination" aria-label="新闻分页"><button type="button" disabled={pending || previousQuery || data.page <= 1} onClick={() => turnPage(data.page - 1)}><ChevronLeft size={16} aria-hidden="true" />上一页</button><span>第 <strong>{data.page}</strong> / {data.pages} 页</span><button type="button" disabled={pending || previousQuery || data.page >= data.pages} onClick={() => turnPage(data.page + 1)}>下一页<ChevronRight size={16} aria-hidden="true" /></button></nav>}
      </div><aside className="ai-news-aside" aria-label="动态概览与来源"><section className="ai-news-overview"><div className="ai-news-aside-heading"><h2>动态概览</h2><span>{windowLabel}</span></div><div className="ai-news-overview-numbers"><div><strong>{data?.stats.items_24h ?? data?.stats.items ?? '—'}</strong><span>{data?.stats.items_24h != null ? '24 小时动态' : '收录动态'}</span></div><div><strong>{data?.stats.healthy_sources ?? '—'}<small> / {data?.stats.sources ?? '—'}</small></strong><span>更新正常的来源</span></div></div><div className="ai-news-topic-breakdown">{data?.topics.slice(0, 5).map(topic => <button type="button" key={topic.id} onClick={() => updateFilters({ topic: topic.id })}><span>{topic.label}</span><span>{topic.count}<ArrowRight size={12} aria-hidden="true" /></span></button>)}</div></section><section className="ai-news-source-overview"><div className="ai-news-aside-heading"><h2>订阅来源</h2><button type="button" onClick={() => updateFilters({ view: 'sources' })}>全部<ArrowUpRight size={13} aria-hidden="true" /></button></div>{data ? <ul>{[...data.sources].sort((a, b) => b.item_count - a.item_count).slice(0, 7).map(source => <li key={source.id}><button type="button" onClick={() => updateFilters({ source: source.id })}>{source.name}</button><span>{source.item_count}</span></li>)}</ul> : <p>正在读取来源状态</p>}{failedSources.length > 0 && <button className="ai-news-source-warning" type="button" aria-label="查看来源状态" onClick={() => updateFilters({ view: 'sources' })}>{failedSources.length} 个来源暂时更新失败<ArrowRight size={13} aria-hidden="true" /></button>}</section><details className="ai-news-methodology"><summary>更新与聚合说明</summary><p>{data?.methodology || '从订阅源收集标题、摘要、发布时间与原文链接。热点排序不代表真实性背书。'}</p><p>页面可见时每分钟检查缓存；新动态不会打断当前阅读。未提供发布时间的条目标注收录时间。英文保留原文，不使用大模型翻译。</p><p>采集最近检查：{formatDate(data?.last_checked_at || data?.updated_at)}<br />内容最近变化：{formatDate(data?.content_updated_at || data?.updated_at)}<br />本页最近检查：{formatDate(new Date(checkedAt).toISOString(), true)}</p></details></aside></div>}
    </section><footer className="ai-news-footer"><span>内容版权归原作者及来源网站所有</span><a href="https://github.com/KKKKhazix/AIHOT" target="_blank" rel="noopener noreferrer">参考 AIHOT 开源思路<ArrowUpRight size={12} aria-hidden="true" /></a></footer>
  </div>;
}
