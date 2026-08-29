import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, CheckSquare2, Database, Download, ExternalLink, Filter, LoaderCircle, Search } from 'lucide-react';
import { dataAcquisitionApi } from '../../api/dataAcquisition';
import type {
  ResearchReportFacets,
  ResearchReportItem,
  ResearchReportLibraryStatus,
  ResearchReportSearchFilters,
  ResearchReportSearchResult,
} from '../../types/dataAcquisition';

const isoDate = (daysAgo = 0) => {
  const value = new Date();
  value.setDate(value.getDate() - daysAgo);
  return value.toISOString().slice(0, 10);
};

const EMPTY_FILTERS: ResearchReportSearchFilters = {
  titleQuery: '', contentQuery: '', broker: '', company: '', tsCode: '', reportType: '',
  industry: '', author: '', tag: '', startDate: isoDate(730), endDate: isoDate(), hasPdf: true, sort: 'latest',
};

const compact = (value: number) => value >= 10_000 ? `${(value / 10_000).toFixed(1)}万` : value.toLocaleString();

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const SelectField = ({ label, value, values, onChange }: {
  label: string; value: string; values: Array<{ value: string; count: number }>; onChange: (value: string) => void;
}) => <label className="space-y-1.5 text-[11px] text-secondary-text">
  <span>{label}</span>
  <select value={value} onChange={(event) => onChange(event.target.value)} className="h-10 w-full rounded-xl border border-border bg-background/75 px-3 text-xs text-foreground outline-none focus:border-primary/60">
    <option value="">全部{label}</option>
    {values.map((item) => <option key={item.value} value={item.value}>{item.value}（{item.count}）</option>)}
  </select>
</label>;

const TextField = ({ label, value, placeholder, onChange, list }: {
  label: string; value: string; placeholder: string; onChange: (value: string) => void; list?: string;
}) => <label className="space-y-1.5 text-[11px] text-secondary-text">
  <span>{label}</span>
  <input value={value} list={list} onChange={(event) => onChange(event.target.value)} placeholder={placeholder}
    className="h-10 w-full rounded-xl border border-border bg-background/75 px-3 text-xs text-foreground outline-none placeholder:text-muted-text focus:border-primary/60" />
</label>;

const ReportRow = ({ item, checked, onToggle }: { item: ResearchReportItem; checked: boolean; onToggle: () => void }) => (
  <article className={`grid gap-3 border-t border-border/60 px-3 py-4 first:border-t-0 lg:grid-cols-[28px_110px_minmax(0,1fr)_170px_110px] ${checked ? 'bg-primary/5' : ''}`}>
    <label className="pt-1"><input type="checkbox" checked={checked} onChange={onToggle} aria-label={`选择研报 ${item.title}`} className="h-4 w-4 accent-primary" /></label>
    <div className="text-xs"><strong className="font-mono text-foreground">{item.tradeDate}</strong><p className="mt-1 text-muted-text">{item.reportType || '未分类'}</p></div>
    <div className="min-w-0">
      <h4 className="text-sm font-semibold leading-5 text-foreground">{item.title}</h4>
      <p className="mt-1 line-clamp-2 text-xs leading-5 text-secondary-text">{item.abstract || '暂无摘要'}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">{item.tags.slice(0, 6).map((tag) => <span key={tag} className="rounded-md border border-primary/15 bg-primary/5 px-1.5 py-0.5 text-[10px] text-primary">{tag}</span>)}</div>
    </div>
    <div className="text-xs leading-5 text-secondary-text"><p className="font-medium text-foreground">{item.broker || '未知券商'}</p><p>{item.author || '作者未标注'}</p><p>{item.companyName || item.industry || '策略/行业研究'} {item.tsCode ? `· ${item.tsCode}` : ''}</p></div>
    <div className="flex items-center justify-end">{item.pdfUrl ? <a href={item.pdfUrl} target="_blank" rel="noreferrer" className="btn-secondary inline-flex items-center gap-1.5 text-xs"><ExternalLink className="h-3.5 w-3.5" />打开 PDF</a> : <span className="text-xs text-muted-text">无 PDF 链接</span>}</div>
  </article>
);

export default function ResearchReportLibrary() {
  const [status, setStatus] = useState<ResearchReportLibraryStatus | null>(null);
  const [facets, setFacets] = useState<ResearchReportFacets>({ brokers: [], reportTypes: [], industries: [], companies: [], tags: [] });
  const [draft, setDraft] = useState<ResearchReportSearchFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<ResearchReportSearchFilters>(EMPTY_FILTERS);
  const [result, setResult] = useState<ResearchReportSearchResult | null>(null);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try { setStatus(await dataAcquisitionApi.researchReportStatus()); }
    catch { /* Keep the last truthful state during a transient poll failure. */ }
  }, []);

  const loadFacets = useCallback(async () => {
    try { setFacets(await dataAcquisitionApi.researchReportFacets()); }
    catch { /* Search remains usable with free-text conditions. */ }
  }, []);

  const runSearch = useCallback(async (filters: ResearchReportSearchFilters, nextPage = 1) => {
    setLoading(true); setError(null);
    try {
      const data = await dataAcquisitionApi.searchResearchReports(filters, nextPage, 30);
      setResult(data); setApplied(filters); setPage(nextPage); setSelected(new Set());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '本地研报库检索失败');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    void Promise.all([loadStatus(), loadFacets(), runSearch(EMPTY_FILTERS, 1)]);
  }, [loadFacets, loadStatus, runSearch]);

  useEffect(() => {
    if (!status || !['queued', 'running', 'interrupted'].includes(status.status)) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadStatus();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadStatus, status]);

  useEffect(() => {
    if (status?.status !== 'completed') return;
    void loadFacets();
  }, [loadFacets, status?.completedAt, status?.status]);

  const pages = Math.max(1, Math.ceil((result?.total ?? 0) / 30));
  const pageIds = useMemo(() => (result?.items ?? []).map((item) => item.id), [result]);
  const setFilter = <K extends keyof ResearchReportSearchFilters,>(key: K, value: ResearchReportSearchFilters[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const toggle = (id: number) => setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const togglePage = () => setSelected((current) => {
    const next = new Set(current); const all = pageIds.every((id) => next.has(id));
    pageIds.forEach((id) => all ? next.delete(id) : next.add(id)); return next;
  });
  const exportSelected = async () => {
    if (!selected.size) return;
    setExporting(true); setError(null);
    try { saveBlob(await dataAcquisitionApi.exportSelectedResearchReports([...selected]), `人工筛选研报_${isoDate()}.xlsx`); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '导出已选研报失败'); }
    finally { setExporting(false); }
  };

  return <section className="overflow-hidden rounded-3xl border border-primary/20 bg-card shadow-card">
    <header className="border-b border-border/70 bg-gradient-to-r from-primary/10 via-card to-info/5 px-5 py-5 lg:px-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div><div className="flex items-center gap-2 text-xs font-semibold tracking-[.18em] text-primary"><BookOpenCheck className="h-4 w-4" />LOCAL RESEARCH TERMINAL</div><h2 className="mt-2 text-xl font-bold text-foreground">两年研报链接库 · 人工筛选台</h2><p className="mt-1 text-sm text-secondary-text">后台先完整同步 Tushare 研报元数据与 PDF URL；每次点击搜索只查本地 SQLite，不调用 AI、不临时扫接口。</p></div>
        <div className="grid min-w-[320px] grid-cols-3 gap-2">
          <div className="rounded-xl border border-border/70 bg-background/55 p-3"><span className="text-[10px] text-muted-text">已入库</span><strong className="mt-1 block font-mono text-lg text-foreground">{compact(status?.total ?? 0)}</strong></div>
          <div className="rounded-xl border border-border/70 bg-background/55 p-3"><span className="text-[10px] text-muted-text">PDF链接</span><strong className="mt-1 block font-mono text-lg text-success">{compact(status?.pdfCount ?? 0)}</strong></div>
          <div className="rounded-xl border border-border/70 bg-background/55 p-3"><span className="text-[10px] text-muted-text">同步进度</span><strong className="mt-1 block font-mono text-lg text-primary">{status?.progress ?? 0}%</strong></div>
        </div>
      </div>
      <div className="mt-4 flex flex-col gap-2 rounded-xl border border-border/60 bg-background/45 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2 text-xs text-secondary-text">{status && ['queued', 'running'].includes(status.status) ? <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-primary" /> : <Database className="h-4 w-4 shrink-0 text-success" />}<span className="truncate">{status?.message || '正在读取研报库状态'}</span></div>
        <span className="shrink-0 font-mono text-[11px] text-muted-text">{status?.earliestTradeDate || '—'} → {status?.latestTradeDate || '—'} · 本地库</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border/60"><div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${status?.progress ?? 0}%` }} /></div>
    </header>

    <div className="p-4 lg:p-6">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <TextField label="标题关键词" value={draft.titleQuery} placeholder="例：低空经济 无人机" onChange={(value) => setFilter('titleQuery', value)} />
        <TextField label="摘要关键词（接口内容）" value={draft.contentQuery} placeholder="Tushare 摘要字段，不冒充PDF全文" onChange={(value) => setFilter('contentQuery', value)} />
        <SelectField label="券商" value={draft.broker} values={facets.brokers} onChange={(value) => setFilter('broker', value)} />
        <TextField label="公司名称" value={draft.company} list="report-company-options" placeholder="例：华懋科技" onChange={(value) => setFilter('company', value)} />
        <datalist id="report-company-options">{facets.companies.map((item) => <option key={item.value} value={item.value} />)}</datalist>
        <SelectField label="研报类型" value={draft.reportType} values={facets.reportTypes} onChange={(value) => setFilter('reportType', value)} />
        <SelectField label="行业" value={draft.industry} values={facets.industries} onChange={(value) => setFilter('industry', value)} />
        <SelectField label="人工标签" value={draft.tag} values={facets.tags} onChange={(value) => setFilter('tag', value)} />
        <TextField label="作者/研究员" value={draft.author} placeholder="输入作者姓名" onChange={(value) => setFilter('author', value)} />
        <TextField label="股票代码" value={draft.tsCode} placeholder="例：603306.SH" onChange={(value) => setFilter('tsCode', value.toUpperCase())} />
        <label className="space-y-1.5 text-[11px] text-secondary-text"><span>开始日期</span><input type="date" value={draft.startDate} onChange={(event) => setFilter('startDate', event.target.value)} className="h-10 w-full rounded-xl border border-border bg-background/75 px-3 text-xs text-foreground" /></label>
        <label className="space-y-1.5 text-[11px] text-secondary-text"><span>结束日期</span><input type="date" value={draft.endDate} onChange={(event) => setFilter('endDate', event.target.value)} className="h-10 w-full rounded-xl border border-border bg-background/75 px-3 text-xs text-foreground" /></label>
        <label className="space-y-1.5 text-[11px] text-secondary-text"><span>排序</span><select value={draft.sort} onChange={(event) => setFilter('sort', event.target.value as 'latest' | 'oldest')} className="h-10 w-full rounded-xl border border-border bg-background/75 px-3 text-xs text-foreground"><option value="latest">日期从新到旧</option><option value="oldest">日期从旧到新</option></select></label>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
        <div className="flex flex-wrap gap-2 text-xs"><button className="btn-secondary" onClick={() => setDraft((current) => ({ ...current, startDate: isoDate(30), endDate: isoDate() }))}>近1月</button><button className="btn-secondary" onClick={() => setDraft((current) => ({ ...current, startDate: isoDate(90), endDate: isoDate() }))}>近3月</button><button className="btn-secondary" onClick={() => setDraft((current) => ({ ...current, startDate: isoDate(365), endDate: isoDate() }))}>近1年</button><button className="btn-secondary" onClick={() => setDraft((current) => ({ ...current, startDate: isoDate(730), endDate: isoDate() }))}>近2年</button><label className="flex items-center gap-2 rounded-xl border border-border px-3"><input type="checkbox" checked={draft.hasPdf} onChange={(event) => setFilter('hasPdf', event.target.checked)} className="accent-primary" />只看有 PDF</label></div>
        <div className="flex gap-2"><button className="btn-secondary inline-flex items-center gap-2" onClick={() => setDraft(EMPTY_FILTERS)}><Filter className="h-4 w-4" />重置条件</button><button className="btn-primary inline-flex items-center gap-2" disabled={loading} onClick={() => void runSearch(draft, 1)}>{loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}{loading ? '检索本地库…' : '点击搜索'}</button></div>
      </div>
      {error ? <p className="mt-3 rounded-xl border border-danger/25 bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p> : null}
    </div>

    <div className="border-t border-border/70 bg-background/25">
      <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between lg:px-6"><p className="text-xs text-secondary-text">本次命中 <strong className="text-foreground">{(result?.total ?? 0).toLocaleString()}</strong> 篇 · 第 {page}/{pages} 页 · 条件在点击搜索后才生效</p><div className="flex flex-wrap gap-2"><button className="btn-secondary inline-flex items-center gap-1.5 text-xs" disabled={!pageIds.length} onClick={togglePage}><CheckSquare2 className="h-3.5 w-3.5" />全选/取消本页</button><button className="btn-primary inline-flex items-center gap-1.5 text-xs" disabled={!selected.size || exporting} onClick={() => void exportSelected()}><Download className="h-3.5 w-3.5" />{exporting ? '正在生成…' : `导出已选 ${selected.size} 篇及PDF链接`}</button></div></div>
      <div className="mx-3 mb-3 overflow-hidden rounded-2xl border border-border/70 bg-card lg:mx-5">{(result?.items ?? []).map((item) => <ReportRow key={item.id} item={item} checked={selected.has(item.id)} onToggle={() => toggle(item.id)} />)}{!loading && !result?.items.length ? <div className="px-4 py-12 text-center text-sm text-muted-text">当前人工条件没有命中研报；可放宽标题、内容、券商、公司或日期条件。</div> : null}</div>
      <div className="flex items-center justify-center gap-3 px-4 pb-5"><button className="btn-secondary" disabled={loading || page <= 1} onClick={() => void runSearch(applied, page - 1)}>上一页</button><span className="font-mono text-xs text-secondary-text">{page} / {pages}</span><button className="btn-secondary" disabled={loading || page >= pages} onClick={() => void runSearch(applied, page + 1)}>下一页</button></div>
    </div>
  </section>;
}
