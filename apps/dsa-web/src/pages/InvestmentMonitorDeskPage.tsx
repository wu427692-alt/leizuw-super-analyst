import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, ExternalLink, FileCheck2, Landmark, ShieldAlert, Sparkles, TrendingUp } from 'lucide-react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import { CHANNEL_LABELS, eventTime } from '../components/investmentMonitor/investmentMonitorMeta';
import type { IntelligenceDashboard, MonitorEvent, MonitorSymbolDetail } from '../types/investmentMonitor';

const configurations = {
  market: { eyebrow: 'Market structure', title: '市场结构', description: '把行情、技术因子、资金席位、题材热度与新闻放在同一时间轴上。', channels: 'market,technical,capital,news', icon: TrendingUp },
  company: { eyebrow: 'Company & institution', title: '公司与机构', description: '公司公告、治理、财务、企业风险、机构调研与研报统一对照。', channels: 'company,governance,fundamental,ownership,enterprise,institution,research', icon: Landmark },
} as const;

function EventCard({ event }: { event: MonitorEvent }) {
  const evidence = (event.metrics._evidence ?? {}) as { evidenceLevel?: string; channel?: string };
  const className = "grid gap-3 border-t border-[#D8DADF] py-2.5 text-left transition-colors first:border-t-0 hover:bg-[#F7F7F8] md:grid-cols-[96px_minmax(0,1fr)_76px]";
  const content = <>
    <div><p className="font-mono text-xs text-[#17181A]">{eventTime(event.eventAt)}</p><p className="mt-1 text-[10px] text-[#7B7F87]">入库 {eventTime(event.ingestedAt)}</p></div>
    <div className="min-w-0"><div className="flex flex-wrap gap-1"><span className="bg-[#EEF4FF] px-1.5 py-0.5 text-[9px] font-semibold text-[#155EEF]">{CHANNEL_LABELS[evidence.channel ?? 'other'] ?? evidence.channel}</span><span className={`px-1.5 py-0.5 text-[9px] font-semibold ${evidence.evidenceLevel === 'unverified' ? 'bg-[#FFF4E5] text-[#B54708]' : 'bg-[#ECFDF3] text-[#027A48]'}`}>{evidence.evidenceLevel === 'unverified' ? '待核验观点' : '事实记录'}</span></div><h3 className="mt-1 text-[12px] font-bold leading-5 text-[#17181A]">{event.title}</h3>{event.summary ? <p className="mt-0.5 line-clamp-1 text-[10px] leading-4 text-[#62666D]">{event.summary}</p> : null}<p className="mt-1 text-[9px] text-[#7B7F87]">{event.sourceName}</p></div>
    <div className="flex items-start justify-between md:block md:text-right"><div><p className="font-mono text-xl font-bold">{event.importanceScore}</p><p className="text-[10px] text-[#7B7F87]">重要度</p></div><span className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold text-[#155EEF]">{event.url ? '打开原文' : '查看接口原文'} <ExternalLink className="h-3 w-3"/></span></div>
  </>;
  return event.url
    ? <a href={event.url} target="_blank" rel="noreferrer" className={className}>{content}</a>
    : <Link to={`/investment-monitor/feed?event=${event.id}`} className={className}>{content}</Link>;
}

function WatchlistView() {
  const [search] = useSearchParams();
  const [items, setItems] = useState<MonitorSymbolDetail[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    void Promise.allSettled(['603306.SH', '300476.SZ'].map(code => investmentMonitorApi.symbol(code, 30)))
      .then(results => {
        if (!active) return;
        setItems(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const preferred = search.get('symbol');
  const ordered = preferred ? [...items].sort(item => item.symbol === preferred ? -1 : 1) : items;
  return <><header className="border-b border-[#17181A] bg-white px-5 py-5"><p className="font-mono text-[10px] uppercase tracking-[0.24em] text-[#155EEF]">Super watchlist / 30 days</p><h1 className="mt-2 text-3xl font-bold tracking-[-0.04em]">超级关注股</h1><p className="mt-2 text-sm text-[#62666D]">不按分钟重复抓取；只在来源产生新事实时刷新标的画像。</p></header><InvestmentMonitorNav/><div className="grid xl:grid-cols-2">{ordered.map((item, index) => <section key={item.symbol} className={`bg-white p-5 ${index ? 'border-l border-[#C9CCD2]' : ''}`}><div className="flex items-start justify-between border-b border-[#17181A] pb-4"><div><h2 className="text-2xl font-bold">{item.name}</h2><p className="font-mono text-xs text-[#62666D]">{item.symbol}</p></div><span className="bg-[#155EEF] px-3 py-1 font-mono text-sm text-white">{item.total} 条证据</span></div><div className="grid grid-cols-4 border-b border-[#D8DADF] py-4">{[['机会', item.scorecard.opportunityScore], ['风险', item.scorecard.riskScore], ['高优先', item.scorecard.highPriorityCount], ['机构记录', item.scorecard.institutionRatingCount]].map(([label,value]) => <div key={String(label)}><p className="text-[10px] text-[#7B7F87]">{label}</p><p className="mt-1 font-mono text-xl font-bold">{value}</p></div>)}</div><div className="grid grid-cols-3 border-b border-[#D8DADF] py-4 text-center">{(['investor','company','institution'] as const).map(key => <div key={key}><p className="font-mono text-xl font-bold">{item.scorecard.perspectives[key] ?? 0}</p><p className="text-[10px] text-[#7B7F87]">{{investor:'投资者',company:'上市公司',institution:'机构'}[key]}视角</p></div>)}</div><div className="mt-2">{item.events.slice(0, 6).map(event => <EventCard event={event} key={event.id}/>)}</div></section>)}{!loading && !items.length ? <EmptyState title="暂无自选股数据" description="请先同步投资情报数据源。"/> : null}</div></>;
}

function DomainView({ mode }: { mode: 'market' | 'company' }) {
  const config = configurations[mode]; const Icon = config.icon;
  const [events, setEvents] = useState<MonitorEvent[]>([]); const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    void investmentMonitorApi.events({ days: 14, channel: config.channels, pageSize: 40 })
      .then(result => { if (active) setEvents(result.items); })
      .catch(() => undefined)
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [config.channels]);
  const groups = useMemo(() => Object.entries(events.reduce<Record<string, MonitorEvent[]>>((acc,event) => { const e = (event.metrics._evidence ?? {}) as { channel?: string }; const key=e.channel ?? 'other'; (acc[key] ??= []).push(event); return acc; },{})).sort((a,b)=>b[1].length-a[1].length), [events]);
  return <><header className="border-b border-[#17181A] bg-white px-4 py-3"><div className="flex items-center gap-2"><Icon className="h-5 w-5 text-[#155EEF]"/><div><p className="font-mono text-[9px] uppercase tracking-[0.2em] text-[#155EEF]">{config.eyebrow}</p><h1 className="mt-0.5 text-2xl font-bold tracking-[-0.04em]">{config.title}</h1></div></div><p className="mt-1 text-xs text-[#62666D]">{config.description}</p></header><InvestmentMonitorNav/><section className="grid border-b border-[#C9CCD2] bg-white sm:grid-cols-2 lg:grid-cols-4">{groups.slice(0,4).map(([key,values], index)=><div key={key} className={`p-3 ${index?'border-l border-[#D8DADF]':''}`}><p className="text-[10px] font-semibold text-[#62666D]">{CHANNEL_LABELS[key]??key}</p><p className="mt-1 font-mono text-2xl font-bold">{values.length}</p><p className="text-[9px] text-[#7B7F87]">高优先 {values.filter(e=>e.importanceScore>=75).length}</p></div>)}</section><section className="grid bg-white xl:grid-cols-[210px_minmax(0,1fr)]"><aside className="border-r border-[#C9CCD2] bg-[#EEF2F7] p-4"><p className="text-[10px] font-bold">渠道分布</p><div className="mt-3 space-y-2">{groups.map(([key,values])=><div key={key} className="flex items-center justify-between text-[10px]"><span>{CHANNEL_LABELS[key]??key}</span><span className="font-mono font-bold">{values.length}</span></div>)}</div></aside><div className="p-4"><div className="flex items-end justify-between"><h2 className="text-[13px] font-bold">最近 14 天高价值事实</h2><span className="font-mono text-[9px] text-[#7B7F87]">默认展示 10 / {events.length}</span></div><div className="mt-2">{events.slice(0,10).map(event=><EventCard event={event} key={event.id}/>)}{!loading&&!events.length?<EmptyState title="暂无记录" description="同步对应数据源后将在这里展示。"/>:null}</div><Link to="/investment-monitor/feed" className="mt-3 inline-flex text-[10px] font-semibold text-[#155EEF]">查看完整事实流水</Link></div></section></>;
}

function AnalysisView() {
  const navigate = useNavigate(); const [data,setData]=useState<IntelligenceDashboard|null>(null);
  useEffect(()=>{
    let active = true;
    void investmentMonitorApi.intelligenceDashboard(14)
      .then(value => { if (active) setData(value); })
      .catch(() => undefined);
    return () => { active = false; };
  },[]);
  const thesis = useMemo(()=>{
    if(!data) return [];
    const top=data.channels.slice(0,3).map(row=>`${CHANNEL_LABELS[row.name]??row.name} ${row.count} 条（环比 ${row.changePct>=0?'+':''}${row.changePct}%）`);
    return [`情报总量较上一周期 ${data.summary.eventChangePct>=0?'上升':'下降'} ${Math.abs(data.summary.eventChangePct)}%，当前事实记录 ${data.summary.factualCount} 条。`, `信息最密集渠道：${top.join('；')}。`, `自选股相关命中 ${data.summary.watchlistHits} 条，高优先级事件 ${data.summary.highPriorityCount} 条，证据冲突 ${data.contradictions.length} 组。`];
  },[data]);
  const openModel = () => {
    const evidence = (data?.signalEvents ?? []).slice(0, 12).map(event =>
      `[事件${event.id}] ${event.eventAt}｜${event.sourceName}｜${event.symbols.join('/')}｜重要度${event.importanceScore}｜${event.title}${event.url ? `｜${event.url}` : ''}`,
    ).join('\n');
    const prompt = `请基于以下投资情报台真实证据，对华懋科技（603306.SH）和胜宏科技（300476.SZ）做深度研判。\n要求：1. 严格区分事实、推断、待核验观点；2. 分别给出多头证据、空头证据、催化剂、风险、机构视角和需要继续验证的问题；3. 每个关键结论引用事件ID；4. 不得补造数据；5. 最后给出未来1日/1周应监控的指标，不给确定性收益承诺。\n\n结构化底稿：\n${thesis.join('\n')}\n\n证据包：\n${evidence}`;
    navigate(`/chat?prompt=${encodeURIComponent(prompt)}`);
  };
  return <><header className="border-b border-[#17181A] bg-white px-5 py-5"><p className="font-mono text-[10px] uppercase tracking-[0.24em] text-[#155EEF]">Grounded synthesis</p><h1 className="mt-2 text-3xl font-bold tracking-[-0.04em]">综合研判</h1><p className="mt-2 text-sm text-[#62666D]">先由结构化数据形成底稿，再把底稿交给大模型深读；模型结论必须能回指事件证据。</p></header><InvestmentMonitorNav/><section className="grid xl:grid-cols-[1.1fr_1fr]"><div className="border-r border-[#C9CCD2] bg-white p-5"><div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-[#155EEF]"/><p className="text-xs font-bold uppercase tracking-[0.12em] text-[#155EEF]">机器研判底稿</p></div><div className="mt-5 space-y-4">{thesis.map((text,index)=><div className="grid grid-cols-[32px_1fr] gap-3" key={text}><span className="font-mono text-xs text-[#155EEF]">0{index+1}</span><p className="text-sm leading-6">{text}</p></div>)}</div><button onClick={openModel} disabled={!data} className="mt-8 inline-flex items-center gap-2 bg-[#155EEF] px-4 py-3 text-xs font-semibold text-white disabled:opacity-40">携带证据包交给大模型 <ArrowRight className="h-3.5 w-3.5"/></button><p className="mt-2 text-[10px] text-[#7B7F87]">自动预填事件 ID、来源、时间、重要度和原文链接；由你确认后再发送。</p></div><div className="bg-[#F7F7F8] p-5"><div className="flex items-center gap-2"><FileCheck2 className="h-4 w-4 text-[#155EEF]"/><p className="text-xs font-bold uppercase tracking-[0.12em]">模型上下文证据包</p></div><div className="mt-4">{(data?.signalEvents??[]).slice(0,7).map(event=><EventCard event={event} key={event.id}/>)}</div></div></section><section className="border-t border-[#C9CCD2] bg-[#FFF4E5] p-5"><div className="flex gap-3"><ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[#B54708]"/><p className="text-xs leading-5 text-[#7A2E0E]">模型分析不是事实源。页面只把真实事件、来源、时间和链接送入分析；任何无法回指证据的判断，都应视为假设而不是结论。</p></div></section></>;
}

export default function InvestmentMonitorDeskPage() {
  const path=useLocation().pathname; const mode=path.split('/').pop();
  return <AppPage className="aurora-workbench max-w-[1680px]"><div className="aurora-workbench-shell overflow-hidden border border-[#C9CCD2] bg-[#F7F7F8] text-[#17181A] shadow-[0_18px_50px_rgba(17,24,39,0.08)]">{mode==='watchlist'?<WatchlistView/>:mode==='market'?<DomainView mode="market"/>:mode==='company'?<DomainView mode="company"/>:<AnalysisView/>}</div></AppPage>;
}
