import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, BarChart3, BookOpen, Building2, DatabaseZap, Download,
  FileSearch, FileText, FlaskConical, Landmark, Layers3, LineChart,
  LockKeyhole, MessageCircleMore, MessagesSquare, Mic2, Radar,
  ShieldCheck, SlidersHorizontal, Sparkles, Star, UserRoundPlus, Waves,
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
  { code: 'MARKET', label: '市场行情', detail: '指数、个股与多周期' },
  { code: 'NEWS', label: '新闻速递', detail: '重要度与原文' },
  { code: 'DISCLOSURE', label: '公司公告', detail: '交易所原文与附件' },
  { code: 'RESEARCH', label: '机构研报', detail: '本地索引与 PDF' },
  { code: 'CORPUS', label: '机构语料', detail: '段子、文件与录音' },
  { code: 'VALIDATION', label: '量化验证', detail: '任务、结果与快照' },
];

const evidenceChain = [
  {
    number: '01', title: '市场异动', summary: '先确定发生了什么，而不是先猜原因。',
    data: '指数、个股、成交量、市场广度与行业行情', output: '异动时间、方向、强度和影响范围',
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览最新真实界面', icon: BarChart3,
  },
  {
    number: '02', title: '原文证据', summary: '回到公告、研报、新闻和机构语料核验。',
    data: '公告、研报、新闻、企业事实、机构段子与股评', output: '原文、来源、发布时间和关联标的',
    screenshot: '/landing/screens/investment-monitor.jpg', alt: '全渠道情报最新真实界面', icon: FileSearch,
  },
  {
    number: '03', title: 'AI研判', summary: '让模型围绕证据提取观点、分歧和跟踪点。',
    data: '结构化事实、原文语料、录音转写与上下文', output: '结论、证据引用、风险和待验证假设',
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业与公司调研最新真实界面', icon: Sparkles,
  },
  {
    number: '04', title: '历史验证', summary: '把判断放回历史样本，检查它是否可重复。',
    data: '事件时间、历史行情、交易约束与研究样本', output: '收益、回撤、置信区间和稳健性结果',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心最新真实界面', icon: FlaskConical,
  },
];

const productTours = [
  {
    number: '01', category: '每日市场判断', title: '市场总览', href: '/app', action: '打开市场总览', icon: BarChart3,
    description: '核心指数、分时与 K 线、新闻速递、全市场涨跌家数、行业分布、海外市场和个人自选股使用明确的交易日与来源口径。',
    data: 'Tushare · 腾讯行情 · 新浪行情 · SQLite', output: '行情现场、新闻、市场广度与行业分布',
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览最新真实页面',
  },
  {
    number: '02', category: '公司研究', title: '自选股超级看板', href: '/super-watchlist', action: '进入自选股看板', icon: Star,
    description: '输入股票名称或代码建立个人关注档案，报价、财务、公告、研报、机构段子、企业风险、股评和一致预期共享同一标的。',
    data: '行情 · 财务 · 资金 · 公告 · 研报 · 机构语料', output: '个股行情、事实时间线、一致预期与证据',
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '自选股超级看板最新真实页面',
  },
  {
    number: '03', category: '深度研究', title: '行业与公司调研', href: '/industry-research', action: '开始深度研究', icon: Landmark,
    description: '行业与公司双模式后台任务自动组织产业链、公司公告与财报、研报、互联网资料、机构段子和录音转写，形成可继续补强的标准报告。',
    data: '研报 · 公告财报 · 录音转写 · 机构语料 · 互联网资料', output: '深度报告、图表、证据清单、Word 与 PDF',
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业与公司调研最新真实页面',
  },
  {
    number: '04', category: '主线归因', title: '概念题材查看', href: '/concept-themes', action: '查看概念题材', icon: Layers3,
    description: '按分层题材、成分股和市场共识组织页面，以相关权重、板块联动和证据区分题材 Beta、个股 Alpha 与独特驱动。',
    data: '题材成分 · 行情联动 · 机构语料 · 公告与新闻事实', output: '题材树、成分矩阵、Beta／Alpha 归因与研究入口',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '概念题材查看最新真实页面',
  },
  {
    number: '05', category: '非结构化研究', title: '机构段子与录音', href: '/essay-radar', action: '检索机构语料', icon: Mic2,
    description: '新增内容自动增量入库；正文支持标题或全文检索，录音可批量下载、仅转写、查看转写原文或进一步生成 AI 纪要。',
    data: '知识星球正文 · 图片 · 文件 · 录音 · AI 标签', output: '全库检索、洞察图谱、趋势、日报、转写与纪要',
    screenshot: '/landing/screens/essay-radar.jpg', alt: '机构段子与录音最新真实页面',
  },
  {
    number: '06', category: '全渠道监控', title: '投资情报台', href: '/investment-monitor', action: '进入投资情报台', icon: Radar,
    description: '公告、研报、新闻、龙虎榜、公司事实、企业风险、机构段子和公开股评按渠道分流，统一展示同步状态、新鲜度与原文。',
    data: '巨潮 · Tushare · 天眼查 · 知识星球 · 公开股评', output: '全渠道信息流、数据源状态、BI 与龙虎榜',
    screenshot: '/landing/screens/investment-monitor.jpg', alt: '投资情报台最新真实页面',
  },
  {
    number: '07', category: '验证研究假设', title: '量化回测与数据利用', href: '/essay-quant', action: '创建量化任务', icon: FlaskConical,
    description: '从模板或自然语言建立事件研究、多因子、机构胜率和信息趋势共振任务；任务按用户独立在后台运行，完成后保存可复现结果。',
    data: '行情 · 交易日历 · 机构事件 · 财务与技术因子', output: '收益、回撤、置信区间、稳健性与交易解释',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心最新真实页面',
  },
  {
    number: '08', category: '资料交付', title: '数据一站式获取', href: '/data-acquisition', action: '检索与下载数据', icon: Download,
    description: '从本地资料库按标题、内容、券商、公司、类型、行业、作者和日期筛选研报、公告、机构段子、文件与录音，再后台打包勾选结果。',
    data: '研报 PDF · 公告原文 · 机构段子 · 文件与录音', output: '精确筛选结果、原文文件包与真实任务进度',
    screenshot: '/landing/screens/data-acquisition.jpg', alt: '数据一站式获取最新真实页面',
  },
];

const utilityEntries = [
  { title: '研究决策台', detail: '把市场、行业、公司和资料任务组织成研究路径。', href: '/research-center', icon: SlidersHorizontal },
  { title: '问股', detail: '优先调用本地事实，缺失时再请求外部数据。', href: '/chat', icon: MessagesSquare },
  { title: 'AlphaSift 选股', detail: '从条件筛选进入候选池，再回到证据与验证。', href: '/screening', icon: Sparkles },
  { title: '完整使用手册', detail: '按任务检索全部入口、数据、步骤和核验方法。', href: '/guide', icon: BookOpen },
];

const LandingPage = () => {
  const pageRef = useRef<HTMLElement>(null);
  const [activeEvidence, setActiveEvidence] = useState(1);
  const [activeProduct, setActiveProduct] = useState(0);
  const evidence = evidenceChain[activeEvidence];
  const product = productTours[activeProduct];

  useEffect(() => {
    document.title = '乐子乌超级价值 · 全域财经研究平台';
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
      <nav aria-label="首页栏目"><a href="#evidence-chain">证据链</a><a href="#real-product">真实界面</a><a href="#data-sources">数据来源</a><Link to="/guide">使用手册</Link></nav>
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
        <h1 id="landing-title">研究，<br />不该从<br /><em>整理资料</em>开始。</h1>
        <p>行情、公告、研报、机构段子与录音、企业事实和量化验证进入同一条证据链。时间、来源、原文和任务状态都可以核验。</p>
        <div className="landing-actions"><Link className="landing-enter" to="/app">进入研究平台<ArrowRight aria-hidden="true" /></Link><Link className="landing-discover" to="/guide"><BookOpen aria-hidden="true" />查看完整手册</Link></div>
        <dl className="landing-hero-facts" aria-label="研究工作台三层结构">
          <div><dt>行情口径</dt><dd>交易日 · 昨收 · 来源</dd></div>
          <div><dt>原文证据</dt><dd>公告 · 研报 · 机构语料</dd></div>
          <div><dt>研究输出</dt><dd>结论 · 风险 · 跟踪点</dd></div>
        </dl>
      </div>
      <div className="landing-hero-visual">
        <div className="landing-hero-thread" aria-hidden="true"><span /><i /></div>
        <figure className="landing-hero-screen">
          <div className="landing-window-bar"><span>01</span><b>市场总览</b><em>2026-09-01 · 线上实机</em></div>
          <img src="/landing/screens/market-overview.jpg" alt="乐子乌超级价值市场总览最新真实页面" fetchPriority="high" />
          <figcaption><span>截图来自当前生产系统</span><b>交易日、来源与更新时间均在页面展示</b></figcaption>
        </figure>
      </div>
    </section>

    <section className="landing-proof" aria-label="平台研究原则">
      <article><strong>01</strong><ShieldCheck aria-hidden="true" /><div><h2>数据有口径</h2><p>交易日、昨收零轴、单位和来源明确展示。</p></div></article>
      <article><strong>02</strong><FileText aria-hidden="true" /><div><h2>观点有原文</h2><p>公告、研报、段子、录音和股评保留入口。</p></div></article>
      <article><strong>03</strong><Radar aria-hidden="true" /><div><h2>任务可回看</h2><p>调研、量化、转写和打包保留状态与结果。</p></div></article>
    </section>

    <section className="landing-evidence" id="evidence-chain" aria-labelledby="landing-evidence-title">
      <div className="landing-section-heading"><span>01 / 证据链</span><h2 id="landing-evidence-title">一条证据链，<br />回答一个真实问题。</h2><p>从发现行情变化到核验原文、形成研判，再用历史样本验证，每一步都可以回到数据和方法。</p></div>
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
          <span>{evidence.number} / 04</span>
          <h3>{evidence.title}</h3>
          <p>{evidence.summary}</p>
          <dl><div><dt>使用数据</dt><dd>{evidence.data}</dd></div><div><dt>形成输出</dt><dd>{evidence.output}</dd></div></dl>
          <Link to={activeEvidence === 0 ? '/app' : activeEvidence === 1 ? '/investment-monitor' : activeEvidence === 2 ? '/industry-research' : '/essay-quant'}>进入对应工作台<ArrowRight aria-hidden="true" /></Link>
        </div>
      </div>
    </section>

    <section className="landing-platform" id="real-product" aria-labelledby="landing-platform-title">
      <div className="landing-section-heading"><span>02 / 真实产品</span><h2 id="landing-platform-title">八类任务，<br />一套共享数据底座。</h2><p>下面均为 2026-09-01 当前程序的实际页面。选择任务时只加载对应截图，减少首页流量和等待。</p></div>
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
        {utilityEntries.map(({ title, detail, href, icon: Icon }, index) => <Link to={href} key={title}><span>{String(index + 9).padStart(2, '0')}</span><Icon aria-hidden="true" /><div><h3>{title}</h3><p>{detail}</p></div><ArrowRight aria-hidden="true" /></Link>)}
      </div>
    </section>

    <section className="landing-data-section" id="data-sources" aria-labelledby="landing-data-title">
      <div className="landing-section-heading"><span>03 / 数据基础</span><h2 id="landing-data-title">来源分开，<br />证据打通。</h2><p>每类数据保留更新时间、来源字段和原文能力，再围绕股票、行业、题材与事件建立关联。</p></div>
      <div className="landing-source-grid">
        {connectedSources.map(({ label, detail, icon: Icon }, index) => <article key={label}><strong>{String(index + 1).padStart(2, '0')}</strong><Icon aria-hidden="true" /><div><h3>{label}</h3><p>{detail}</p></div></article>)}
      </div>
      <p className="landing-data-note"><Sparkles aria-hidden="true" />AI 用于提取、归纳、转写后研判和生成研究任务，不替代原始数据；没有证据的内容应标记待核验。</p>
    </section>

    <section className="landing-final" id="landing-start" aria-labelledby="landing-final-title">
      <span>04 / 开始使用</span><h2 id="landing-final-title">先看证据，<br />再做判断。</h2>
      <p>注册只需要姓名和密码；申请通过后，个人自选股、问股记录、调研任务、量化任务和下载任务相互独立。</p>
      <div><Link className="landing-final-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />申请使用</Link><Link className="landing-final-secondary" to="/app">已有账号，进入平台<ArrowRight aria-hidden="true" /></Link></div>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" to="/"><span><DatabaseZap aria-hidden="true" /></span><b>乐子乌超级价值</b></Link><p>全域财经研究平台 · 页面与数据持续更新</p><div><Link to="/guide">使用手册</Link><Link to="/app">研究平台</Link><Link to="/admin">管理员</Link></div></footer>
  </main>;
};

export default LandingPage;
