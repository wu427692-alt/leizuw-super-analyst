import { useCallback, useEffect, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, BarChart3, Building2, DatabaseZap, FileText, FlaskConical,
  Landmark, LockKeyhole, MessageCircleMore, Network, Radar, ShieldCheck,
} from 'lucide-react';
import './LandingPage.css';

const sources = [
  { label: '公告', note: '交易所 · 巨潮', icon: FileText },
  { label: '研报', note: '券商 · 机构', icon: Landmark },
  { label: '知识星球', note: '纪要 · 观点', icon: Radar },
  { label: '企业事实', note: '工商 · 股权', icon: Building2 },
  { label: '公开讨论', note: '论坛 · 媒体', icon: MessageCircleMore },
];

const capabilities = [
  { label: '实时行情', note: '分时 · 日线 · 市场广度', icon: BarChart3, status: '实时更新' },
  { label: '全渠道情报', note: '公告 · 研报 · 新闻 · 企业', icon: Network, status: '持续汇聚' },
  { label: '小作文洞察', note: '实体识别 · 热点追踪 · 证据', icon: Radar, status: '智能识别' },
  { label: '量化研究', note: '因子 · 样本外 · 稳健性', icon: FlaskConical, status: '体系化研究' },
  { label: '证据决策', note: '时间轴 · 证据链 · 任务', icon: ShieldCheck, status: '可信可追溯' },
];

const evidence = [
  { time: '09:31', source: '公告', text: '关键合同与经营事项进入证据链' },
  { time: '09:42', source: '研报', text: '盈利预测与假设变化完成结构化' },
  { time: '10:15', source: '知识星球', text: '产业纪要与市场观点完成关联' },
  { time: '10:28', source: '企业事实', text: '股权与知识产权变化完成核验' },
];

const EvidenceMap = () => (
  <div className="landing-evidence-map" id="data-map" aria-label="全域数据汇聚为可验证证据的动态示意">
    <svg className="landing-evidence-lines" viewBox="0 0 760 560" aria-hidden="true">
      <defs>
        <linearGradient id="evidence-line" x1="0" x2="1">
          <stop offset="0" stopColor="#1c4ed8" stopOpacity="0.16" />
          <stop offset="0.54" stopColor="#38bdf8" stopOpacity="0.95" />
          <stop offset="1" stopColor="#60a5fa" stopOpacity="0.2" />
        </linearGradient>
        <filter id="evidence-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      {[132, 202, 272, 342, 412].map((y, index) => (
        <path key={y} className="landing-flow-line" d={`M 120 ${y} C 255 ${y}, 260 ${278 + (y - 272) * 0.16}, 380 278 C 495 ${278 + (y - 272) * 0.12}, 510 ${128 + index * 74}, 646 ${128 + index * 74}`} pathLength="1" />
      ))}
      <circle className="landing-flow-pulse" cx="380" cy="278" r="72" />
      <circle className="landing-flow-pulse is-delayed" cx="380" cy="278" r="72" />
    </svg>

    <div className="landing-market-trace" aria-hidden="true">
      <span>A股 · 日线</span>
      <svg viewBox="0 0 280 86"><path d="M4 70 L22 59 L39 63 L55 35 L73 46 L92 25 L110 38 L130 31 L147 49 L165 39 L183 57 L202 31 L219 27 L237 36 L254 14 L276 8" /></svg>
    </div>

    <div className="landing-source-list">
      {sources.map(({ label, note, icon: Icon }) => (
        <div className="landing-source" key={label}>
          <span><Icon aria-hidden="true" /></span>
          <div><strong>{label}</strong><small>{note}</small></div>
        </div>
      ))}
    </div>

    <div className="landing-company-core">
      <Building2 aria-hidden="true" /><strong>上市公司</strong><small>事实中心</small><i />
    </div>

    <div className="landing-decision-chain" aria-hidden="true">
      <span>数据汇聚</span><ArrowRight /><span>证据沉淀</span><ArrowRight /><span>支持决策</span>
    </div>

    <aside className="landing-evidence-timeline" id="evidence">
      <header><strong>证据时间轴</strong><span>实时</span></header>
      <div>
        {evidence.map((item) => (
          <article key={`${item.time}-${item.source}`}>
            <time>{item.time}</time><span /><p><b>{item.source}</b>{item.text}</p>
          </article>
        ))}
      </div>
    </aside>
  </div>
);

const LandingPage = () => {
  const pageRef = useRef<HTMLElement>(null);

  useEffect(() => { document.title = '乐子乌超级价值 · 全域财经研究平台'; }, []);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    pageRef.current?.style.setProperty('--pointer-x', `${(x - 0.5) * 14}px`);
    pageRef.current?.style.setProperty('--pointer-y', `${(y - 0.5) * 10}px`);
    pageRef.current?.style.setProperty('--pointer-glow-x', `${x * 100}%`);
    pageRef.current?.style.setProperty('--pointer-glow-y', `${y * 100}%`);
  }, []);

  const resetPointer = useCallback(() => {
    pageRef.current?.style.setProperty('--pointer-x', '0px');
    pageRef.current?.style.setProperty('--pointer-y', '0px');
    pageRef.current?.style.setProperty('--pointer-glow-x', '68%');
    pageRef.current?.style.setProperty('--pointer-glow-y', '34%');
  }, []);

  return (
    <main ref={pageRef} className="landing-page" onPointerMove={handlePointerMove} onPointerLeave={resetPointer}>
      <div className="landing-mesh" aria-hidden="true" /><div className="landing-noise" aria-hidden="true" />
      <header className="landing-header">
        <Link className="landing-brand" to="/" aria-label="乐子乌超级价值首页"><span><DatabaseZap aria-hidden="true" /></span>乐子乌超级价值</Link>
        <nav aria-label="首页栏目"><a href="#data-map">全域数据</a><a href="#capabilities">研究工作台</a><a href="#evidence">决策证据</a></nav>
        <div className="landing-header-actions">
          <Link className="landing-admin-enter" to="/admin"><LockKeyhole aria-hidden="true" />管理员</Link>
          <Link className="landing-header-enter" to="/app">进入研究终端<ArrowRight aria-hidden="true" /></Link>
        </div>
      </header>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-copy">
          <h1 id="landing-title">把复杂市场，<span>变成可验证的投资线索。</span></h1>
          <p>连接行情、公告、研报、知识星球、企业事实与公开讨论，在同一时间轴中追踪公司、机构与市场。</p>
          <div className="landing-actions">
            <Link className="landing-enter" to="/app">进入研究终端<ArrowRight aria-hidden="true" /></Link>
            <a className="landing-data-link" href="#data-map">查看数据版图<ArrowRight aria-hidden="true" /></a>
          </div>
        </div>
        <EvidenceMap />
      </section>

      <section className="landing-capabilities" id="capabilities" aria-label="平台核心能力">
        {capabilities.map(({ label, note, icon: Icon, status }, index) => (
          <article className="landing-capability" key={label}>
            <span className="landing-capability-number">0{index + 1}</span><span className="landing-capability-icon"><Icon aria-hidden="true" /></span>
            <div><strong>{label}</strong><small>{note}</small><em><i />{status}</em></div>
          </article>
        ))}
      </section>
    </main>
  );
};

export default LandingPage;
