import { useCallback, useEffect, useRef } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowDown, ArrowRight, BarChart3, Building2, DatabaseZap, Download,
  FileText, FlaskConical, Landmark, LineChart, LockKeyhole, MessageCircleMore,
  Radio, Radar, ShieldCheck, Sparkles, Star, UserRoundPlus, Waves,
} from 'lucide-react';
import './LandingPage.css';

const heroSources = [
  { label: '实时行情', icon: LineChart },
  { label: '公告', icon: FileText },
  { label: '研报', icon: Landmark },
  { label: '机构段子与录音', icon: Waves },
  { label: '企业事实', icon: Building2 },
  { label: '公开股评', icon: MessageCircleMore },
  { label: '量化研究', icon: FlaskConical },
];

const researchFlow = [
  { number: '01', title: '事实汇聚', note: '实时行情 · 市场广度', detail: '行情、公告、研报、新闻、企业事实与机构语料持续增量进入统一事实库。', icon: Radio },
  { number: '02', title: '证据整理', note: '原文 · 时间 · 来源', detail: '保留原始链接、附件、发布时间与股票关联，观点和事实不再混在一起。', icon: ShieldCheck },
  { number: '03', title: '研究验证', note: 'AI 提取 · 多周期洞察', detail: '从非结构化语料提取主题、预期与分歧，并与真实行情和公司数据交叉验证。', icon: Sparkles },
  { number: '04', title: '持续跟踪', note: '事件研究 · 自选股监控', detail: '把研究假设放入后台任务和自选股监控，在新证据出现时持续更新。', icon: Radar },
];

const platformFeatures = [
  { number: '01', title: '市场总览', description: '核心指数、分时与 K 线、市场广度、行业涨跌和海外市场，在同一交易日口径下查看。', href: '/app', action: '查看市场', icon: BarChart3, visual: 'market' },
  { number: '02', title: '自选股超级看板', description: '新增股票后自动串联行情、公告、研报、机构段子、企业事实、股评和一致预期。', href: '/super-watchlist', action: '管理自选', icon: Star, visual: 'watchlist' },
  { number: '03', title: '机构段子与录音', description: '知识星球增量入库，支持标题或全文检索、录音与文件下载、日报及短中长期洞察。', href: '/essay-radar', action: '进入研判台', icon: Waves, visual: 'essay' },
  { number: '04', title: '投资情报台', description: '按公告、研报、新闻、龙虎榜、企业风险和公开讨论分渠道展示，保留原文证据。', href: '/investment-monitor', action: '查看情报', icon: Radar, visual: 'intelligence' },
  { number: '05', title: '量化回测与数据利用', description: '事件研究、机构胜率、信息与趋势共振、多因子研究和受约束的自然语言回测。', href: '/essay-quant', action: '创建研究任务', icon: FlaskConical, visual: 'quant' },
  { number: '06', title: '数据一站式获取', description: '从本地资料库按标题、内容、券商、公司与日期筛选，后台打包原文、附件或 PDF 链接。', href: '/data-acquisition', action: '检索与下载', icon: Download, visual: 'data' },
];

const FeatureVisual = ({ type }: { type: string }) => {
  if (type === 'market') {
    return <div className="landing-feature-visual is-market" aria-hidden="true">
      <svg viewBox="0 0 520 180" preserveAspectRatio="none"><defs><linearGradient id="landing-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#22d3ee" stopOpacity=".32" /><stop offset="1" stopColor="#2563eb" stopOpacity="0" /></linearGradient></defs><path className="landing-chart-area" d="M0 150 L42 136 L75 141 L112 92 L150 113 L196 64 L231 86 L274 51 L318 72 L365 36 L406 60 L451 22 L520 42 L520 180 L0 180 Z" /><path className="landing-chart-line" d="M0 150 L42 136 L75 141 L112 92 L150 113 L196 64 L231 86 L274 51 L318 72 L365 36 L406 60 L451 22 L520 42" /></svg>
      <span>分时</span><span>日 K</span><span>市场广度</span><span>行业分布</span>
    </div>;
  }
  if (type === 'essay') {
    return <div className="landing-feature-visual is-wave" aria-hidden="true">
      <div className="landing-waveform">{Array.from({ length: 52 }, (_, index) => <i key={index} style={{ '--wave': `${22 + ((index * 17) % 68)}%` } as CSSProperties} />)}</div>
      <span>标题 / 全文</span><span>录音</span><span>文件</span><span>每日研判</span>
    </div>;
  }
  if (type === 'quant') {
    return <div className="landing-feature-visual is-quant" aria-hidden="true">
      <svg viewBox="0 0 520 180" preserveAspectRatio="none"><path className="is-baseline" d="M0 146 C74 116 111 130 172 95 S290 101 351 65 S448 66 520 25" /><path className="is-alpha" d="M0 154 C62 149 120 103 174 113 S278 74 338 80 S438 30 520 38" /></svg>
      <div><b>样本外</b><b>置信区间</b><b>最大回撤</b></div>
    </div>;
  }
  const rows = type === 'watchlist' ? ['行情与估值', '公告与研报', '一致预期'] : type === 'intelligence' ? ['公告治理', '机构研报', '龙虎榜与资金'] : ['研报资料库', '小作文原文', '附件与 PDF'];
  return <div className={`landing-feature-visual is-rows is-${type}`} aria-hidden="true">
    {rows.map((row, index) => <div key={row}><span>{row}</span><i style={{ width: `${76 - index * 13}%` }} /></div>)}
  </div>;
};

const LandingPage = () => {
  const pageRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = '乐子乌超级价值 · 全域财经研究平台';
    const page = pageRef.current;
    if (!page) return undefined;
    const revealNodes = Array.from(page.querySelectorAll<HTMLElement>('[data-landing-reveal]'));
    if (typeof IntersectionObserver === 'undefined') {
      revealNodes.forEach((node) => node.classList.add('is-visible'));
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          (entry.target as HTMLElement).classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.12 });
    revealNodes.forEach((node) => observer.observe(node));

    let frame = 0;
    const updateScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
        page.style.setProperty('--landing-scroll', `${Math.min(window.scrollY / max, 1)}`);
        page.style.setProperty('--landing-hero-shift', `${Math.min(window.scrollY * 0.12, 110)}px`);
        frame = 0;
      });
    };
    updateScroll();
    window.addEventListener('scroll', updateScroll, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', updateScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    pageRef.current?.style.setProperty('--pointer-x', `${(x - 0.5) * 18}px`);
    pageRef.current?.style.setProperty('--pointer-y', `${(y - 0.5) * 12}px`);
    pageRef.current?.style.setProperty('--pointer-glow-x', `${x * 100}%`);
    pageRef.current?.style.setProperty('--pointer-glow-y', `${y * 100}%`);
  }, []);

  const resetPointer = useCallback(() => {
    pageRef.current?.style.setProperty('--pointer-x', '0px');
    pageRef.current?.style.setProperty('--pointer-y', '0px');
    pageRef.current?.style.setProperty('--pointer-glow-x', '68%');
    pageRef.current?.style.setProperty('--pointer-glow-y', '34%');
  }, []);

  return <main ref={pageRef} className="landing-page" onPointerMove={handlePointerMove} onPointerLeave={resetPointer}>
    <div className="landing-scroll-progress" aria-hidden="true"><i /></div>
    <header className="landing-header">
      <Link className="landing-brand" to="/" aria-label="乐子乌超级价值首页"><span><DatabaseZap aria-hidden="true" /></span>乐子乌超级价值</Link>
      <nav aria-label="首页栏目"><a href="#research-flow">研究流程</a><a href="#platform-features">平台能力</a><a href="#landing-start">开始使用</a></nav>
      <div className="landing-header-actions">
        <Link className="landing-register-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />注册</Link>
        <Link className="landing-admin-enter" to="/admin"><LockKeyhole aria-hidden="true" />管理员</Link>
        <Link className="landing-header-enter" to="/app">进入研究终端<ArrowRight aria-hidden="true" /></Link>
      </div>
    </header>

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero-backdrop" aria-hidden="true" />
      <div className="landing-hero-copy" data-landing-reveal>
        <h1 id="landing-title">让市场信息，<span>成为可验证的研究优势。</span></h1>
        <p>连接实时行情、公告、研报、机构段子与录音、企业事实、公开股评和量化研究。</p>
        <div className="landing-actions"><Link className="landing-enter" to="/app">进入研究终端<ArrowRight aria-hidden="true" /></Link><a className="landing-discover" href="#research-flow">向下了解平台<ArrowDown aria-hidden="true" /></a></div>
      </div>
      <div className="landing-hero-sources" aria-label="平台已接入的数据与研究能力">
        {heroSources.map(({ label, icon: Icon }, index) => <span key={label} style={{ '--source-index': index } as CSSProperties}><Icon aria-hidden="true" />{label}</span>)}
      </div>
      <a className="landing-scroll-cue" href="#research-flow" aria-label="向下查看平台研究流程"><i /><ArrowDown aria-hidden="true" /></a>
    </section>

    <section className="landing-flow-section" id="research-flow" aria-labelledby="landing-flow-title">
      <div className="landing-section-heading" data-landing-reveal><h2 id="landing-flow-title">从信息到证据，<span>再到可执行的研究。</span></h2><p>每一条结论都能回到来源，每一个研究假设都能继续验证。</p></div>
      <div className="landing-flow-stage">
        <div className="landing-flow-beam" aria-hidden="true"><i /></div>
        {researchFlow.map(({ number, title, note, detail, icon: Icon }, index) => <article key={title} className="landing-flow-step" data-landing-reveal style={{ '--step-index': index } as CSSProperties}><span className="landing-flow-number">{number}</span><Icon aria-hidden="true" /><h3>{title}</h3><strong>{note}</strong><p>{detail}</p></article>)}
      </div>
    </section>

    <section className="landing-platform" id="platform-features" aria-labelledby="landing-platform-title">
      <div className="landing-section-heading is-left" data-landing-reveal><h2 id="landing-platform-title">不是更多页面，<span>而是一套完整研究工作流。</span></h2><p>从市场观察、个股跟踪、非结构化语料，到量化验证和资料交付，数据在各页面之间共享。</p></div>
      <div className="landing-feature-index">
        {platformFeatures.map(({ number, title, description, href, action, icon: Icon, visual }, index) => <article className={`landing-feature-row${index % 2 ? ' is-reverse' : ''}`} key={title} data-landing-reveal><div className="landing-feature-copy"><span className="landing-feature-number">{number}</span><Icon aria-hidden="true" /><div><h3>{title}</h3><p>{description}</p><Link to={href}>{action}<ArrowRight aria-hidden="true" /></Link></div></div><FeatureVisual type={visual} /></article>)}
      </div>
    </section>

    <section className="landing-final" id="landing-start" aria-labelledby="landing-final-title" data-landing-reveal>
      <div className="landing-final-orbit" aria-hidden="true"><i /><i /><i /></div>
      <h2 id="landing-final-title">研究，不该止于看到更多信息。</h2><p>把事实、观点、行情与验证放在同一个工作流里。</p><Link className="landing-final-enter" to="/app">开始使用<ArrowRight aria-hidden="true" /></Link>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" to="/"><span><DatabaseZap aria-hidden="true" /></span>乐子乌超级价值</Link><p>财经情报、研究与数据利用平台</p><div><Link to="/app">研究终端</Link><Link to="/admin">管理员</Link></div></footer>
  </main>;
};

export default LandingPage;
