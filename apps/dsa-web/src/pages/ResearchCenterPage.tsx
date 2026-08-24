import { useCallback, useMemo, useState } from 'react';
import { Activity, ArrowUpRight, CheckCircle2, CircleAlert, Database, RadioTower, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage } from '../components/common';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type { ResearchCenterOverview, ResearchDecisionPacket } from '../types/investmentMonitor';
import './ResearchCenterPage.css';
import './ResearchCenterExtensions.css';

const compact = (value: number) => new Intl.NumberFormat('zh-CN', {
  notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1,
}).format(value);

const stamp = (value?: string | null) => {
  if (!value) return '等待数据';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

function LoadingFrame() {
  return <div className="rc-loading" role="status" aria-label="正在组装研究决策台">
    <div className="rc-loading-line"><span /></div>
    <p>正在从共享行情库与统一证据库组装研究工作区…</p>
  </div>;
}

function EvidenceSpine({ packet }: { packet: ResearchDecisionPacket }) {
  const price = packet.market.price;
  const change = packet.market.changePct;
  const steps = [
    { no: '01', label: '事实变化', value: `${packet.changes.length} 条近期证据`, note: packet.changes[0]?.title || '暂无新增事实' },
    { no: '02', label: '行情确认', value: price == null ? '行情待补齐' : `${price}${change == null ? '' : ` / ${change > 0 ? '+' : ''}${change.toFixed(2)}%`}`, note: `时间 ${stamp(packet.market.updatedAt)}` },
    { no: '03', label: '预期交叉', value: `${packet.expectations.brokerReports} 份研报 / ${packet.expectations.essayEstimates} 条语料预期`, note: packet.expectations.asOf ? `截至 ${stamp(packet.expectations.asOf)}` : '只展示已经提取的预期' },
    { no: '04', label: '矛盾与失效', value: packet.agreement.conflict ? '存在多空事实冲突' : '当前未检测到双向事实', note: `${packet.agreement.bullishFacts} 多 / ${packet.agreement.bearishFacts} 空 · ${packet.invalidationEvidence.length} 条风险证据` },
    { no: '05', label: '研究门', value: packet.state, note: `${packet.verificationTasks.length} 个下一步核验任务` },
  ];
  return <ol className="rc-spine">
    {steps.map(step => <li key={step.no}>
      <span className="rc-spine-no">{step.no}</span>
      <div><p>{step.label}</p><strong>{step.value}</strong><small>{step.note}</small></div>
    </li>)}
  </ol>;
}

function PacketView({ packet }: { packet: ResearchDecisionPacket }) {
  return <section className="rc-packet" aria-label={`${packet.name}研究证据链`}>
    <header className="rc-packet-head">
      <div><p className="rc-kicker">ACTIVE RESEARCH OBJECT</p><h2>{packet.name}<span>{packet.symbol}</span></h2></div>
      <div className="rc-gate"><span>研究就绪度</span><strong>{packet.readinessScore}</strong><em>{packet.state}</em></div>
    </header>
    <div className="rc-packet-grid">
      <EvidenceSpine packet={packet} />
      <div className="rc-score-panel">
        <p className="rc-panel-title">评分不是涨跌预测</p>
        {packet.scoreComponents.map(item => <div className="rc-score" key={item.name}>
          <div><span>{item.name}</span><small>权重 {item.weight}%</small><strong>{item.score}</strong></div>
          <i><b style={{ width: `${item.score}%` }} /></i>
        </div>)}
        <p className="rc-disclaimer">{packet.disclaimer}</p>
      </div>
    </div>
    <div className="rc-research-grid">
      <article><p className="rc-panel-title">最新事实 · 可回看原文</p>{packet.changes.slice(0, 4).map(event => <Link key={event.id} to={`/investment-monitor/feed?event=${event.id}`}><time>{stamp(event.eventAt)}</time><span>{event.title}</span><ArrowUpRight /></Link>)}{packet.changes.length === 0 ? <p className="rc-muted">暂无可展示的新增事实</p> : null}</article>
      <article><p className="rc-panel-title">下一步核验 · 不替用户拍脑袋</p>{packet.verificationTasks.map((task, index) => <div className="rc-task" key={`${task.task}-${index}`}><span>{task.priority}</span><div><strong>{task.task}</strong><p>{task.reason}</p></div></div>)}</article>
    </div>
  </section>;
}

export default function ResearchCenterPage() {
  const [data, setData] = useState<ResearchCenterOverview | null>(null);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [initialLoading, setInitialLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await investmentMonitorApi.researchCenter();
      setData(next); setSelected(current => current || next.decisionPackets[0]?.symbol || ''); setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究决策数据暂时不可用');
    } finally { setInitialLoading(false); }
  }, []);

  usePageActivationRefresh(load, { intervalMs: 30_000, minIntervalMs: 2_000 });

  const packet = useMemo(() => data?.decisionPackets.find(item => item.symbol === selected) || data?.decisionPackets[0], [data, selected]);
  const attention = useMemo(() => (data?.dataSources ?? []).filter(source => source.monitoringStatus !== 'live' || source.freshnessStatus === 'stale').slice(0, 8), [data]);

  return <AppPage className="max-w-[1760px]">
    <main className="rc-shell">
      <header className="rc-header">
        <div><p className="rc-kicker">EVIDENCE OPERATING SYSTEM · V3</p><h1>研究决策台</h1><p>把“我有什么数据”推进到“这只股票现在能不能研究、缺什么证据、下一步核验什么”。</p></div>
        <div className="rc-live"><i /><span>后台自动更新</span><strong>{stamp(data?.generatedAt)}</strong></div>
      </header>

      {initialLoading && !data ? <LoadingFrame /> : null}
      {error && !data ? <div className="rc-error" role="alert"><CircleAlert />共享数据库暂时不可读；页面会在后台自动重试，不会弹窗打断操作。</div> : null}
      {data ? <>
        <section className="rc-iterations" aria-label="三个迭代版本">
          {data.iterations.map(item => <article key={item.version}><span>{item.version}</span><div><strong>{item.name}</strong><p>{item.result}</p></div></article>)}
        </section>

        <section className="rc-metrics">
          {[
            { icon: RadioTower, label: '已接入数据源', value: data.system.sourceCount, unit: '个' },
            { icon: Database, label: '本地事实事件', value: compact(data.system.storedEventCount), unit: '条' },
            { icon: Activity, label: '实时巡检源', value: data.system.liveMonitorCount, unit: '个' },
            { icon: ShieldCheck, label: '新鲜数据源', value: data.system.freshSourceCount, unit: '个' },
            { icon: CircleAlert, label: '需要关注', value: data.system.attentionSourceCount, unit: '项' },
            { icon: CheckCircle2, label: '研究对象', value: data.system.watchlistCount, unit: '只' },
          ].map(({ icon: Icon, label, value, unit }) => <div key={label}><Icon /><span>{label}</span><strong>{value}<small>{unit}</small></strong></div>)}
        </section>

        <nav className="rc-symbols" aria-label="选择研究对象">
          {data.decisionPackets.map(item => <button type="button" key={item.symbol} className={item.symbol === packet?.symbol ? 'is-active' : ''} onClick={() => setSelected(item.symbol)}><span>{item.name}</span><small>{item.symbol}</small><strong>{item.readinessScore}</strong></button>)}
        </nav>

        {packet ? <PacketView packet={packet} /> : <div className="rc-empty">当前账号尚未添加自选股。先在<Link to="/super-watchlist">自选股超级看板</Link>添加研究对象。</div>}

        <section className="rc-section-head"><div><p className="rc-kicker">V1 · CAPABILITY MAP</p><h2>整体功能清单</h2></div><p>每个入口都明确目的、实际使用的数据和交付结果。</p></section>
        <section className="rc-functions">
          {data.functions.map((item, index) => <Link to={item.route} key={item.route}><span>{String(index + 1).padStart(2, '0')}</span><div><h3>{item.name}</h3><p>{item.purpose}</p><small>{item.data.join(' · ')}</small><strong>{item.output}</strong></div><ArrowUpRight /></Link>)}
        </section>

        <section className="rc-two-column">
          <div><div className="rc-section-head rc-compact"><div><p className="rc-kicker">SYSTEM LOGIC</p><h2>构建逻辑</h2></div></div><ol className="rc-architecture">{data.architecture.map(item => <li key={item.layer}><strong>{item.layer}</strong><p>{item.logic}</p></li>)}</ol></div>
          <div><div className="rc-section-head rc-compact"><div><p className="rc-kicker">TRUST LEDGER</p><h2>需要关注的数据源</h2></div><Link to="/investment-monitor/bi">查看全部</Link></div><div className="rc-sources">{attention.map(source => <div key={source.sourceKey}><i className={source.lastStatus === 'failed' ? 'is-bad' : ''}/><span><strong>{source.name}</strong><small>{source.sourceKey}</small></span><em>{source.monitoringStatus || 'pending'} / {source.freshnessStatus || 'empty'}</em></div>)}{attention.length === 0 ? <p className="rc-muted">全部数据源处于当前阈值内。</p> : null}</div></div>
        </section>

        <section className="rc-section-head"><div><p className="rc-kicker">DECISION UTILITY</p><h2>怎样真实帮助投资决策</h2></div><p>不给神奇分数，缩短发现、核验、证伪和复盘的路径。</p></section>
        <section className="rc-uses">{data.decisionUses.map((item, index) => <article key={item.name}><span>{String(index + 1).padStart(2, '0')}</span><h3>{item.name}</h3><p>{item.value}</p></article>)}</section>

        <section className="rc-section-head"><div><p className="rc-kicker">SELF AUDIT BACKLOG</p><h2>自我反思与升级清单</h2></div><p>明确当前仍不够好的地方，避免把“功能多”误当作“决策可靠”。</p></section>
        <section className="rc-reflection">{data.reflection.map((item, index) => <article key={item.gap}><span>R{index + 1}</span><div><h3>{item.gap}</h3><p>{item.impact}</p><strong>下一版 · {item.upgrade}</strong></div></article>)}</section>

        <section className="rc-principles"><p className="rc-panel-title">投资决策保护栏</p>{data.principles.map((item, index) => <div key={item}><span>{index + 1}</span><p>{item}</p></div>)}</section>
      </> : null}
    </main>
  </AppPage>;
}
