import { useEffect, useRef, useState } from 'react';
import { ExternalLink, FileText, RefreshCw } from 'lucide-react';
import { investmentMonitorApi } from '../../api/investmentMonitor';
import type { MonitorEvent } from '../../types/investmentMonitor';
import { isResearchEventType } from '../../utils/stockResearchRelation';
import { Drawer } from '../common';
import { evidenceTime, sourceRecordUrl } from './investmentMonitorMeta';
import { sourceRecordGroups, type SourceRecordGroup } from './sourceRecordFields';
import './SourceRecordDrawer.css';
import { newsPublisher } from '../../utils/newsPublisher';

function textValue(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) return value.filter(item => typeof item === 'string').join('、') || null;
  return null;
}

const FINANCIAL_SECTIONS = { income: '利润表', cashflow: '现金流量表', indicator: '财务指标' } as const;
export type FinancialRecordSection = keyof typeof FINANCIAL_SECTIONS;
type Props = { event?: MonitorEvent; eventId?: number; financialSection?: FinancialRecordSection; homeNews?: boolean; onClose: () => void };
// These existing builders synthesize prose from source fields. Older saved
// rows may predate _evidence.contentNature, including their compact list form.
const STRUCTURED_EVENT_TYPES = new Set([
  'market_snapshot', 'fundamental_snapshot', 'capital_chip_snapshot', 'ownership_snapshot', 'technical_factor', 'earnings_express',
]);

function SourceRecordTable({ title, note, table }: { title: string; note?: string; table: NonNullable<SourceRecordGroup['table']> }) {
  const [requestedPage, setRequestedPage] = useState(0);
  const region = useRef<HTMLDivElement>(null);
  const pageSize = table.pageSize;
  const pages = Math.max(1, Math.ceil(table.rows.length / pageSize));
  const page = Math.min(requestedPage, pages - 1);
  const rows = table.rows.slice(page * pageSize, (page + 1) * pageSize);
  const changePage = (next: number) => {
    setRequestedPage(next);
    // The clicked button may become disabled on the first/last page. Keep
    // keyboard focus in the table rather than letting the browser lose it.
    region.current?.focus({ preventScroll: true });
  };
  return <details className="source-record-forecasts">
    <summary>{title} · 已保存 {table.total} 条</summary>
    {note ? <p className="source-record-boundary">{note}</p> : null}
    <div ref={region} className="source-record-table" tabIndex={0} role="region" aria-label={`${title}明细，可横向滚动`}>
      <table><thead><tr>{table.columns.map(column => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={page * pageSize + index}>{table.columns.map(column => <td key={column.key}>{row[column.key]}</td>)}</tr>)}</tbody>
      </table>
    </div>
    {pages > 1 ? <nav className="source-record-pagination" aria-label={`${title}分页`}>
      <span aria-live="polite">第 {page + 1} / {pages} 页</span>
      <button type="button" aria-label={`${title}上一页`} disabled={page === 0} onClick={() => changePage(page - 1)}>上一页</button>
      <button type="button" aria-label={`${title}下一页`} disabled={page + 1 >= pages} onClick={() => changePage(page + 1)}>下一页</button>
    </nav> : null}
  </details>;
}

function SourceRecordGroupView({ group, current = false }: { group: SourceRecordGroup; current?: boolean }) {
  return group.table ? <SourceRecordTable title={group.title} note={group.note} table={group.table} /> : <section className="source-record-fields">
    <h3>{current ? `当前核对：${group.title}` : group.title}</h3>{group.note ? <p className="source-record-boundary">{group.note}</p> : null}
    <dl>{group.fields.map(field => <div key={field.label} className={field.value.length > 80 ? 'source-record-wide' : undefined}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
  </section>;
}

export function SourceRecordDrawer({ event, eventId = event?.id, financialSection, homeNews, onClose }: Props) {
  if (!eventId || !Number.isInteger(eventId) || eventId <= 0) return null;
  const section = financialSection === 'income' || financialSection === 'cashflow' || financialSection === 'indicator' ? financialSection : undefined;
  // Switching entries resets state immediately; a late response cannot display
  // a previous company's record under the new heading. A different table in
  // this same record changes presentation only, without another detail GET.
  return <SourceRecordReader key={eventId} event={event?.id === eventId ? event : undefined} eventId={eventId} financialSection={section} homeNews={homeNews} onClose={onClose} />;
}

function SourceRecordReader({ event, eventId, financialSection, homeNews, onClose }: Props & { eventId: number }) {
  const [record, setRecord] = useState(event);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [denied, setDenied] = useState(false);
  const request = useRef<{ attempt: number; promise: Promise<MonitorEvent> } | null>(null);
  const financialRegion = useRef<HTMLElement>(null);
  const previousFinancialSection = useRef(financialSection);
  useEffect(() => {
    if (previousFinancialSection.current !== financialSection) {
      previousFinancialSection.current = financialSection;
      financialRegion.current?.focus({ preventScroll: true });
      financialRegion.current?.scrollIntoView?.({ block: 'start' });
    }
  }, [financialSection]);
  useEffect(() => {
    let cancelled = false;
    if (!request.current || request.current.attempt !== attempt) {
      request.current = { attempt, promise: investmentMonitorApi.event(eventId) };
    }
    void request.current.promise.then(value => {
      if (!value || value.id !== eventId) throw new Error('Record identity mismatch');
      if (!cancelled) { setRecord(value); setLoaded(true); setFailed(false); }
    }).catch(error => {
      if (cancelled) return;
      const status = error?.response?.status;
      if (status === 401 || status === 403) { setRecord(undefined); setLoaded(false); setDenied(true); }
      setFailed(true);
    })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [eventId, attempt]);
  const retry = () => { setLoading(true); setFailed(false); setDenied(false); setAttempt(value => value + 1); };
  const announcement = record?.eventType === 'company_announcement';
  const research = record && isResearchEventType(record.eventType);
  const url = sourceRecordUrl(record?.url);
  const groups = record ? sourceRecordGroups(record) : [];
  const financialTitle = financialSection ? FINANCIAL_SECTIONS[financialSection] : undefined;
  const currentGroup = record?.eventType === 'fundamental_snapshot' ? groups.find(group => group.title === financialTitle) : undefined;
  const otherGroups = groups.filter(group => group !== currentGroup);
  const evidence = record?.metrics._evidence;
  const derived = evidence && typeof evidence === 'object' && 'contentNature' in evidence && evidence.contentNature === 'derived_summary';
  // Historical generated summaries can contain stale labels. Where source
  // fields exist, read those directly instead of presenting generated prose as
  // an original article. Do not rewrite or delete saved records on a read.
  const abstract = research ? textValue(record.metrics.abstr) : null;
  const excerpt = abstract || record?.summary;
  const fieldsOnly = !announcement && !research && !excerpt?.trim() && groups.length > 0;
  const structured = Boolean(derived || fieldsOnly || (record && STRUCTURED_EVENT_TYPES.has(record.eventType)));
  return <Drawer isOpen onClose={onClose} title={record?.title || '资料详情'} width="max-w-3xl">
    <article className="source-record-reader">
      {homeNews ? <button type="button" className="btn-secondary mb-4" onClick={onClose}>返回首页</button> : null}
      {record ? <header className="source-record-meta"><span>{homeNews ? newsPublisher(record) : record.sourceName || '来源未注明'}</span><time>{evidenceTime(record)}</time></header> : null}
      {loading ? <p role="status" className="source-record-status">正在读取已入库资料{record ? structured ? '，正在核对来源字段。' : '，先显示列表中已有的内容。' : '…'}</p> : null}
      {failed ? <div role="status" className="source-record-retry"><p>{denied ? '当前账号无法读取该资料，请重新登录或确认访问权限。' : `本次详情读取未完成${record ? structured ? '，已保留来源信息。' : '，已保留列表摘录。' : '，请重试。'}`}</p><button type="button" onClick={retry} disabled={loading}><RefreshCw size={14} />重试读取资料</button></div> : null}
      <section className="source-record-content">
        <h3><FileText size={17} />{structured ? '来源字段记录' : announcement ? '公告记录' : research ? '研报摘要与记录' : loaded ? '已保存摘录' : '列表摘录'}</h3>
        <p className="source-record-boundary">{structured ? '以下为数据源记录，按各自日期和单位展示，不是报告正文。' : announcement || research
          ? '这里展示已入库的摘要与资料字段，不是完整 PDF 正文。'
          : '这里展示本地已保存的内容，可能是摘要或摘录；完整内容请以来源原文为准。'}</p>
        {structured ? !groups.length && <p className="source-record-empty">{loading ? '正在读取来源字段…' : failed ? '来源字段尚未取得，可重试读取。' : '此条未保存可展示的来源字段。'}</p> : excerpt ? <div className="source-record-excerpt">{excerpt}</div>
          : <p className="source-record-empty">{groups.length ? '此条只保存了资料字段，没有正文摘录。' : loading ? '尚未取得内容。' : '这条记录没有保存正文摘录。'}</p>}
      </section>
      {url ? <a className="source-record-original" href={url} target="_blank" rel="noopener noreferrer">{/\.pdf(?:$|[?#])/i.test(url) ? '打开原始 PDF' : '打开来源原文'}<ExternalLink size={16} /></a>
        : !loading && !structured ? <p className="source-record-empty">此条未提供可用的原文链接，不将摘要当作全文。</p> : null}
      {financialTitle ? <div key={financialSection} className="source-record-financial-context">
        <section ref={financialRegion} tabIndex={-1} className="source-record-current" aria-label={`当前核对${financialTitle}`}>
          {currentGroup ? <SourceRecordGroupView group={currentGroup} current /> : <><h3>当前核对：{financialTitle}</h3><p className="source-record-empty">{loading ? `正在读取${financialTitle}字段…` : failed ? `${financialTitle}字段尚未取得，可重试读取。` : `此条记录未保存可展示的${financialTitle}字段，不使用其他表替代。`}</p></>}
        </section>
        {otherGroups.length ? <details className="source-record-other-fields">
          <summary>其他已保存字段（{otherGroups.length} 组）</summary>
          <p className="source-record-boundary">其他表有各自的报告期，不一定是看板当前选用的版本。</p>
          {otherGroups.map(group => <SourceRecordGroupView key={group.title} group={group} />)}
        </details> : null}
      </div> : groups.map(group => <SourceRecordGroupView key={group.title} group={group} />)}
    </article>
  </Drawer>;
}
