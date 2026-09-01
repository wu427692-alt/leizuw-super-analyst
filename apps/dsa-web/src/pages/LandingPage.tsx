import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, BarChart3, BookOpen, Building2, DatabaseZap, Download,
  FileText, FlaskConical, Landmark, Layers3, LineChart,
  ListChecks, LockKeyhole, MessageCircleMore, MessagesSquare, Mic2,
  Search, ShieldCheck, Sparkles, Star, UserRoundPlus, Waves,
} from 'lucide-react';
import './LandingPage.css';

const connectedSources = [
  { label: 'Tushare', detail: '行情、财务、资金、研报、新闻与龙虎榜', icon: LineChart },
  { label: '巨潮资讯', detail: '上市公司公告、历史回补与 PDF 原文', icon: FileText },
  { label: '知识星球 MCP', detail: '机构段子、图片、文件、录音与增量同步', icon: Waves },
  { label: '天眼查', detail: '企业登记、风险、股权与知识产权事实', icon: Building2 },
  { label: '公开股评', detail: '东方财富股吧公开讨论与个股关联', icon: MessageCircleMore },
  { label: '本地 SQLite', detail: '跨页面共享的行情、语料与证据索引', icon: DatabaseZap },
];

const marketRibbon = [
  { code: 'TODAY', label: '今日决策', detail: '变化、新闻与待办' },
  { code: 'OPPORTUNITY', label: '机会发现', detail: '题材、事件与候选' },
  { code: 'STOCK', label: '个股决策', detail: '证据、情景与条件' },
  { code: 'RESEARCH', label: '深度研究', detail: '公司、行业与原文' },
  { code: 'TASKS', label: '任务与验证', detail: '调研、转写、回测与结果' },
];

const evidenceChain = [
  {
    number: '01', title: '今日变化', summary: '先看今天发生了什么，以及哪些变化值得处理。',
    data: '指数、个股、成交量、市场广度、行业行情与重要新闻', output: '市场环境、重要变化、影响范围与今日待办',
    screenshot: '/landing/screens/market-overview.jpg', alt: '今日市场环境最新真实界面', icon: BarChart3, href: '/app',
  },
  {
    number: '02', title: '机会候选', summary: '把题材、事件和市场共识转成可以继续研究的候选。',
    data: '题材层级、成分股、行情联动、机构语料与新闻事实', output: '候选股票、题材权重、Beta／Alpha 归因与证据入口',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '机会发现最新真实界面', icon: Layers3, href: '/concept-themes',
  },
  {
    number: '03', title: '个股判断', summary: '围绕一个标的组织支持、反对和仍未知的证据。',
    data: '实时行情、财务、资金、公告、研报、机构语料与股评', output: '事实时间线、关键变量、情景判断与行动条件',
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '个股决策最新真实界面', icon: Star, href: '/super-watchlist',
  },
  {
    number: '04', title: '深度研究', summary: '信息不足时，启动公司或行业调研补齐关键问题。',
    data: '研报、公告财报、互联网资料、机构语料与录音转写', output: '标准研究报告、图表、证据清单、Word 与 PDF',
    screenshot: '/landing/screens/industry-research.jpg', alt: '深度研究最新真实界面', icon: Landmark, href: '/industry-research',
  },
  {
    number: '05', title: '验证复盘', summary: '把假设放回历史样本，判断它是否可重复和可交易。',
    data: '事件时间、历史行情、交易约束、机构事件与研究样本', output: '收益、回撤、置信区间、稳健性与交易解释',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '验证复盘最新真实界面', icon: FlaskConical, href: '/essay-quant',
  },
];

const productTours = [
  {
    number: '01', category: '今天先处理什么', title: '今日决策', href: '/app', action: '查看今日变化', icon: BarChart3,
    description: '从真实交易日出发查看核心指数、市场广度、行业分布、自选股行情和重要新闻。先建立今天的市场环境，再决定哪些变化需要跟踪。',
    data: 'Tushare · 腾讯行情 · 新浪行情 · SQLite · 新闻', output: '市场环境、重要变化、关注标的与今日待办',
    screenshot: '/landing/screens/market-overview.jpg', alt: '今日决策最新真实页面',
  },
  {
    number: '02', category: '哪里值得继续研究', title: '机会发现', href: '/concept-themes', action: '发现题材与候选', icon: Layers3,
    description: '从分层概念题材、市场共识和成分股联动中形成候选，以相关权重和证据区分板块 Beta、个股 Alpha 与独特驱动。',
    data: '题材成分 · 行情联动 · 机构语料 · 公告与新闻事实', output: '题材树、候选池、Beta／Alpha 归因与研究入口',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '机会发现最新真实页面',
  },
  {
    number: '03', category: '这只股票能不能做', title: '个股决策', href: '/super-watchlist', action: '进入个股决策', icon: Star,
    description: '输入名称或代码建立个人标的档案。行情、财务、公告、研报、机构段子、企业风险、股评和一致预期共享同一股票与时间口径。',
    data: '行情 · 财务 · 资金 · 公告 · 研报 · 机构语料 · 股评', output: '事实时间线、支持／反对证据、情景与行动条件',
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '个股决策最新真实页面',
  },
  {
    number: '04', category: '关键问题还缺什么', title: '深度研究', href: '/industry-research', action: '启动公司或行业调研', icon: Landmark,
    description: '行业与公司双模式后台任务自动组织产业链、公司公告与财报、研报、互联网资料、机构段子和录音转写，形成可补强、可导出的标准报告。',
    data: '研报 · 公告财报 · 录音转写 · 机构语料 · 互联网资料', output: '深度报告、图表、证据清单、Word 与 PDF',
    screenshot: '/landing/screens/industry-research.jpg', alt: '深度研究最新真实页面',
  },
  {
    number: '05', category: '离开页面也继续执行', title: '任务与验证', href: '/tasks', action: '查看任务与研究结果', icon: ListChecks,
    description: '调研、录音转写、数据打包和量化验证都在后台执行；量化任务按用户独立保存参数、进度和结果，完成后可以复现与比较。',
    data: '后台任务状态 · 行情 · 交易日历 · 机构事件 · 研究样本', output: '任务进度、研究结果、收益回撤、稳健性与可复现快照',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心最新真实页面',
  },
];

const utilityEntries = [
  { title: '机构段子与录音', detail: '检索原文、文件和录音；可批量下载、转写并生成纪要。', href: '/essay-radar', icon: Mic2 },
  { title: '数据一站式获取', detail: '精细筛选研报、公告与机构资料，勾选后后台打包。', href: '/data-acquisition', icon: Download },
  { title: '问股', detail: '优先调用本地事实，缺失时再请求外部数据。', href: '/chat', icon: MessagesSquare },
  { title: 'AlphaSift 选股', detail: '按条件生成候选池，再进入个股证据与量化验证。', href: '/screening', icon: Search },
  { title: '完整使用手册', detail: '按任务检索全部入口、数据、步骤和核验方法。', href: '/guide', icon: BookOpen },
];

const LandingPage = () => {
  const pageRef = useRef<HTMLElement>(null);
  const [activeEvidence, setActiveEvidence] = useState(0);
  const [activeProduct, setActiveProduct] = useState(0);
  const evidence = evidenceChain[activeEvidence];
  const product = productTours[activeProduct];

  useEffect(() => {
    document.title = '乐子乌超级价值 · 证据驱动的投资研究工作台';
    let frame = 0;
    const updateProgress = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
        pageRef.current?.style.setProperty('--landing-scroll', `${Math.min(window.scrollY / max, 1)}`);
      });
    };
    updateProgress();
    window.addEventListener('scroll', updateProgress, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', updateProgress);
    };
  }, []);

  return <main ref={pageRef} className="landing-page">
    <div className="landing-scroll-progress" aria-hidden="true"><i /></div>
    <header className="landing-header">
      <Link className="landing-brand" to="/" aria-label="乐子乌超级价值首页"><span><DatabaseZap aria-hidden="true" /></span><b>乐子乌超级价值</b></Link>
      <nav aria-label="首页栏目"><a href="#decision-loop">决策闭环</a><a href="#real-product">五个工作区</a><a href="#data-sources">数据来源</a><Link to="/guide">使用手册</Link></nav>
      <div className="landing-header-actions">
        <Link className="landing-register-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />注册</Link>
        <Link className="landing-admin-enter" to="/admin"><LockKeyhole aria-hidden="true" />管理员</Link>
        <Link className="landing-header-enter" to="/app">进入平台<ArrowRight aria-hidden="true" /></Link>
      </div>
    </header>

    <section className="landing-market-ribbon" aria-label="平台研究数据范围">
      <div className="landing-market-ribbon__track">
        {[...marketRibbon, ...marketRibbon].map((item, index) => <article key={`${item.code}-${index}`} aria-hidden={index >= marketRibbon.length}>
          <span>{item.code}</span><strong>{item.label}</strong><small>{item.detail}</small>
        </article>)}
      </div>
    </section>

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero-copy">
        <h1 id="landing-title">从变化，<br />到判断，<br />再到<em>行动。</em></h1>
        <p>首页先告诉你今天发生了什么；机会页形成候选，个股页组织判断，研究与后台任务补齐证据。每一步都能回到时间、来源和原文。</p>
        <div className="landing-actions"><Link className="landing-enter" to="/app">进入今日决策<ArrowRight aria-hidden="true" /></Link><Link className="landing-discover" to="/guide"><BookOpen aria-hidden="true" />查看完整手册</Link></div>
        <dl className="landing-hero-facts" aria-label="新版决策工作流">
          <div><dt>今日</dt><dd>变化 · 新闻 · 待办</dd></div>
          <div><dt>个股</dt><dd>证据 · 情景 · 条件</dd></div>
          <div><dt>任务</dt><dd>调研 · 转写 · 验证</dd></div>
        </dl>
      </div>
      <div className="landing-hero-visual">
        <figure className="landing-hero-screen">
          <div className="landing-window-bar"><span>01</span><b>今日市场环境</b><em>2026-09-01 · 线上实机</em></div>
          <img src="/landing/screens/market-overview.jpg" alt="乐子乌超级价值今日市场环境最新真实页面" fetchPriority="high" />
          <figcaption><span>截图来自当前生产系统</span><b>交易日、来源与更新时间均在页面展示</b></figcaption>
        </figure>
      </div>
    </section>

    <section className="landing-proof" aria-label="平台研究原则">
      <article><strong>01</strong><BarChart3 aria-hidden="true" /><div><h2>先看变化</h2><p>用真实交易日与来源确定今天需要处理什么。</p></div></article>
      <article><strong>02</strong><ShieldCheck aria-hidden="true" /><div><h2>再做判断</h2><p>支持、反对和未知证据分开，不用单一分数代替思考。</p></div></article>
      <article><strong>03</strong><ListChecks aria-hidden="true" /><div><h2>最后行动</h2><p>把结论写成入场、退出、等待或继续核验的条件。</p></div></article>
    </section>

    <section className="landing-evidence" id="decision-loop" aria-labelledby="landing-evidence-title">
      <div className="landing-section-heading"><span>01 / 决策闭环</span><h2 id="landing-evidence-title">五个步骤，<br />围绕一次真实决策。</h2><p>从今日变化形成机会候选，进入个股判断；证据不足时启动深度研究，最后用历史样本验证并复盘。</p></div>
      <div className="landing-evidence-steps" role="tablist" aria-label="研究证据链">
        {evidenceChain.map(({ number, title, summary, icon: Icon }, index) => <button type="button" role="tab" aria-selected={activeEvidence === index} className={activeEvidence === index ? 'is-active' : ''} onClick={() => setActiveEvidence(index)} key={title}>
          <span>{number}</span><Icon aria-hidden="true" /><strong>{title}</strong><small>{summary}</small>
        </button>)}
      </div>
      <div className="landing-evidence-view" role="tabpanel" key={evidence.title}>
        <figure>
          <div className="landing-window-bar"><span>{evidence.number}</span><b>{evidence.title}</b><em>当前步骤</em></div>
          <img src={evidence.screenshot} alt={evidence.alt} loading="lazy" />
        </figure>
        <div className="landing-evidence-notes">
          <span>{evidence.number} / 05</span>
          <h3>{evidence.title}</h3>
          <p>{evidence.summary}</p>
          <dl><div><dt>使用数据</dt><dd>{evidence.data}</dd></div><div><dt>形成输出</dt><dd>{evidence.output}</dd></div></dl>
          <Link to={evidence.href}>进入对应工作台<ArrowRight aria-hidden="true" /></Link>
        </div>
      </div>
    </section>

    <section className="landing-platform" id="real-product" aria-labelledby="landing-platform-title">
      <div className="landing-section-heading"><span>02 / 真实产品</span><h2 id="landing-platform-title">五个工作区，<br />围绕一次决策。</h2><p>下面均为 2026-09-01 当前程序的实际页面。前台不再堆叠数据看台，而是按用户从发现到行动的顺序组织。</p></div>
      <div className="landing-product-workbench">
        <div className="landing-product-index" role="tablist" aria-label="研究任务">
          {productTours.map(({ number, title, category, icon: Icon }, index) => <button type="button" role="tab" aria-selected={activeProduct === index} className={activeProduct === index ? 'is-active' : ''} onClick={() => setActiveProduct(index)} key={title}>
            <span>{number}</span><Icon aria-hidden="true" /><span><small>{category}</small><strong>{title}</strong></span><ArrowRight aria-hidden="true" />
          </button>)}
        </div>
        <article className="landing-product-stage" role="tabpanel" key={product.title}>
          <figure>
            <div className="landing-window-bar"><span>{product.number}</span><b>{product.title}</b><em>生产环境最新截图</em></div>
            <img src={product.screenshot} alt={product.alt} loading="lazy" />
          </figure>
          <div className="landing-product-copy">
            <span>{product.category}</span><h3>{product.title}</h3><p>{product.description}</p>
            <dl><div><dt>使用数据</dt><dd>{product.data}</dd></div><div><dt>形成输出</dt><dd>{product.output}</dd></div></dl>
            <Link to={product.href}>{product.action}<ArrowRight aria-hidden="true" /></Link>
          </div>
        </article>
      </div>
      <div className="landing-utility-grid" aria-label="其他研究入口">
        {utilityEntries.map(({ title, detail, href, icon: Icon }, index) => <Link to={href} key={title}><span>{String(index + 6).padStart(2, '0')}</span><Icon aria-hidden="true" /><div><h3>{title}</h3><p>{detail}</p></div><ArrowRight aria-hidden="true" /></Link>)}
      </div>
    </section>

    <section className="landing-data-section" id="data-sources" aria-labelledby="landing-data-title">
      <div className="landing-section-heading"><span>03 / 数据基础</span><h2 id="landing-data-title">来源分开，<br />证据打通。</h2><p>每类数据保留更新时间、来源字段和原文能力，再围绕股票、行业、题材与事件建立关联。</p></div>
      <div className="landing-source-grid">
        {connectedSources.map(({ label, detail, icon: Icon }, index) => <article key={label}><strong>{String(index + 1).padStart(2, '0')}</strong><Icon aria-hidden="true" /><div><h3>{label}</h3><p>{detail}</p></div></article>)}
      </div>
      <p className="landing-data-note"><Sparkles aria-hidden="true" />AI 用于提取、归纳、转写后研判和生成研究任务，不替代原始数据，也不以不可解释的单一分数代替投资判断。</p>
    </section>

    <section className="landing-final" id="landing-start" aria-labelledby="landing-final-title">
      <span>04 / 开始使用</span><h2 id="landing-final-title">先处理今天，<br />再研究机会。</h2>
      <p>注册只需要姓名和密码；申请通过后，个人自选股、问股记录、调研任务、量化任务和下载任务相互独立。</p>
      <div><Link className="landing-final-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />申请使用</Link><Link className="landing-final-secondary" to="/app">已有账号，进入平台<ArrowRight aria-hidden="true" /></Link></div>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" to="/"><span><DatabaseZap aria-hidden="true" /></span><b>乐子乌超级价值</b></Link><p>证据驱动的投资研究工作台 · 页面与数据持续更新</p><div><Link to="/guide">使用手册</Link><Link to="/app">今日决策</Link><Link to="/admin">管理员</Link></div></footer>
  </main>;
};

export default LandingPage;
