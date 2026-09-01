import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, BarChart3, BookOpen, Building2, CheckCircle2, DatabaseZap,
  Download, FileSearch, FileText, FlaskConical, Landmark, Layers3, LineChart,
  LockKeyhole, MessageCircleMore, MessagesSquare, Mic2, Radar, Search,
  ShieldCheck, SlidersHorizontal, Sparkles, Star, UserRoundPlus, Waves,
} from 'lucide-react';
import './LandingPage.css';

const connectedSources = [
  { label: 'Tushare', detail: '行情、财务、资金、研报与资讯', icon: LineChart },
  { label: '巨潮资讯', detail: '上市公司公告与 PDF 原文', icon: FileText },
  { label: '知识星球 MCP', detail: '机构段子、图片、文件与录音', icon: Waves },
  { label: '天眼查', detail: '企业登记、风险与知识产权事实', icon: Building2 },
  { label: '公开股评', detail: '东方财富股吧公开讨论', icon: MessageCircleMore },
  { label: '本地 SQLite', detail: '跨页面共享的行情与证据库', icon: DatabaseZap },
];

const marketRibbon = [
  { code: 'INDEX', label: '核心指数', detail: '分时与多周期' },
  { code: 'BREADTH', label: '市场广度', detail: '涨跌家数与行业分布' },
  { code: 'DISCLOSURE', label: '公司公告', detail: '交易所原文与附件' },
  { code: 'RESEARCH', label: '机构研报', detail: '标题、正文与 PDF' },
  { code: 'CORPUS', label: '机构语料', detail: '段子、文件与录音' },
  { code: 'VALIDATION', label: '量化验证', detail: '任务、结果与快照' },
];

const researchLedger = [
  { number: '01', label: '行情口径', detail: '交易日 · 昨收 · 来源' },
  { number: '02', label: '证据链', detail: '公告 · 研报 · 机构语料' },
  { number: '03', label: '研究输出', detail: '结论 · 风险 · 跟踪点' },
];

const researchFlow = [
  { number: '01', title: '定义问题', detail: '明确对象、日期和需要回答的研究问题。', icon: Search },
  { number: '02', title: '召回证据', detail: '从统一数据库检索行情、公告、研报与机构语料。', icon: FileSearch },
  { number: '03', title: '交叉验证', detail: '把观点、价格和公司事实放回同一时间轴。', icon: ShieldCheck },
  { number: '04', title: '持续跟踪', detail: '将假设交给自选股、情报监控或量化任务。', icon: Radar },
];

const productTours = [
  {
    number: '01', eyebrow: '每日市场判断', title: '市场总览', href: '/app', action: '打开市场总览', icon: BarChart3,
    description: '核心指数、分时与 K 线、全市场涨跌家数、行业分布、海外市场和个人自选股保持统一交易日口径。',
    data: 'Tushare 指数与全市场日线 · 腾讯分钟快照 · SQLite 行情库',
    output: '市场广度、行业分布与自选股行情',
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览真实页面，展示指数、交易日、分时行情和成交量',
  },
  {
    number: '02', eyebrow: '公司研究', title: '自选股超级看板', href: '/super-watchlist', action: '进入自选股看板', icon: Star,
    description: '新增股票后自动形成共享档案，把报价、财务、公告、研报、机构段子、企业风险、股评和一致预期放在一处。',
    data: '行情 · 财务 · 资金 · 公告 · 研报 · 机构语料',
    output: '个股行情、事实时间线与一致预期',
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '自选股超级看板真实页面，展示个股行情、估值和分时图',
  },
  {
    number: '03', eyebrow: '深度研究', title: '行业与公司调研', href: '/industry-research', action: '开始深度研究', icon: Landmark,
    description: '后台任务围绕产业链、趋势、龙头、痛点、应用场景和关键验证指标组织多源证据，并生成可继续补强的报告。',
    data: '研报 · 公告 · 财报 · 录音转写 · 互联网资料',
    output: '标准调研报告、图表、Word 与 PDF',
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业与公司调研真实页面，展示后台任务、证据来源和深度报告',
  },
  {
    number: '04', eyebrow: '主线归因', title: '概念题材查看', href: '/concept-themes', action: '查看概念题材', icon: Layers3,
    description: '从分层题材、成分股和多源共识出发，区分板块 Beta 与个股 Alpha，并保留归因证据和更新时间。',
    data: '题材成分 · 行情联动 · 机构语料 · 公告事实',
    output: '题材树、成分矩阵、Beta 与 Alpha 线索',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '概念题材真实页面，展示题材分层、成分股和归因分析',
  },
  {
    number: '05', eyebrow: '非结构化研究', title: '机构段子与录音', href: '/essay-radar', action: '检索机构语料', icon: Mic2,
    description: '知识星球新增内容自动增量入库；正文支持标题或全文检索，录音支持批量下载、转写和纪要任务。',
    data: '知识星球正文 · 图片 · 文件 · 录音 · AI 标签',
    output: '全库检索、趋势洞察、转写与纪要',
    screenshot: '/landing/screens/essay-radar.jpg', alt: '机构段子与录音真实页面，展示语料统计、主题与证据覆盖',
  },
  {
    number: '06', eyebrow: '全渠道监控', title: '投资情报台', href: '/investment-monitor', action: '进入投资情报台', icon: Radar,
    description: '公告、研报、资讯、龙虎榜、公司事实、企业风险、机构段子和公开股评按渠道分流，并显示同步状态。',
    data: '巨潮 · Tushare · 天眼查 · 知识星球 · 公开股评',
    output: '实时流水、来源新鲜度、BI 与龙虎榜',
    screenshot: '/landing/screens/investment-monitor.jpg', alt: '投资情报台真实页面，展示全渠道数据源及其同步状态',
  },
  {
    number: '07', eyebrow: '验证研究假设', title: '量化回测与数据利用', href: '/essay-quant', action: '创建量化任务', icon: FlaskConical,
    description: '从模板或自然语言建立事件研究、多因子、机构胜率和信息趋势共振任务，后台运行并保存结果快照。',
    data: '行情 · 交易日历 · 机构事件 · 财务与技术因子',
    output: '收益、回撤、置信区间与稳健性检验',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心真实页面，展示研究流程与后台任务',
  },
  {
    number: '08', eyebrow: '资料交付', title: '数据一站式获取', href: '/data-acquisition', action: '检索与下载数据', icon: Download,
    description: '从本地资料库按标题、摘要、券商、公司、类型、行业、作者和日期筛选，再后台打包勾选的原文或链接。',
    data: '研报 PDF 链接 · 公告 · 知识星球文件与录音',
    output: '筛选结果、原文文件包与真实进度',
    screenshot: '/landing/screens/data-acquisition.jpg', alt: '数据一站式获取真实页面，展示研报筛选字段与后台任务状态',
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

  useEffect(() => {
    document.title = '乐子乌超级价值 · 全域财经研究平台';
    const updateProgress = () => {
      const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
      pageRef.current?.style.setProperty('--landing-scroll', `${Math.min(window.scrollY / max, 1)}`);
    };
    updateProgress();
    window.addEventListener('scroll', updateProgress, { passive: true });
    return () => window.removeEventListener('scroll', updateProgress);
  }, []);

  return <main ref={pageRef} className="landing-page">
    <div className="landing-scroll-progress" aria-hidden="true"><i /></div>
    <header className="landing-header">
      <Link className="landing-brand" to="/" aria-label="乐子乌超级价值首页"><span><DatabaseZap aria-hidden="true" /></span><b>乐子乌超级价值</b></Link>
      <nav aria-label="首页栏目"><a href="#research-flow">研究方法</a><a href="#real-product">真实界面</a><a href="#data-sources">数据来源</a><Link to="/guide">使用手册</Link></nav>
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
        <div className="landing-kicker"><span>中国股票研究工作台</span><b>2026 / 真实生产系统</b></div>
        <h1 id="landing-title">研究，<br />不该从<br /><em>整理资料</em>开始。</h1>
        <p>把行情、公告、研报、机构段子与录音、企业事实和量化验证接入同一条证据链。时间、来源、原文和任务状态都可以核验。</p>
        <div className="landing-actions"><Link className="landing-enter" to="/app">进入研究平台<ArrowRight aria-hidden="true" /></Link><Link className="landing-discover" to="/guide"><BookOpen aria-hidden="true" />查看完整手册</Link></div>
      </div>
      <div className="landing-hero-visual">
        <div className="landing-hero-coordinate" aria-hidden="true"><span>研究对象</span><b>市场 / 行业 / 公司</b></div>
        <figure className="landing-hero-screen">
          <div className="landing-window-bar"><span>01</span><b>市场总览</b><em>真实运行界面</em></div>
          <img src="/landing/screens/market-overview.jpg" alt="乐子乌超级价值市场总览真实页面" fetchPriority="high" />
          <figcaption><span>截图采集于 2026-08-31</span><b>页面数据按实际交易日自动更新</b></figcaption>
        </figure>
        <div className="landing-hero-ledger" aria-label="研究工作台三层结构">
          {researchLedger.map(item => <article key={item.number}><span>{item.number}</span><div><strong>{item.label}</strong><small>{item.detail}</small></div></article>)}
        </div>
      </div>
      <dl className="landing-hero-metrics" aria-label="平台覆盖范围">
        <div><dt>12</dt><dd>研究工作台</dd></div>
        <div><dt>08</dt><dd>真实界面展示</dd></div>
        <div><dt>06</dt><dd>核心数据源</dd></div>
        <div><dt>原文</dt><dd>证据可追溯</dd></div>
      </dl>
    </section>

    <section className="landing-proof" aria-label="平台研究原则">
      <article><strong>01</strong><CheckCircle2 aria-hidden="true" /><div><h2>数据有口径</h2><p>交易日、昨收零轴、单位和来源明确展示。</p></div></article>
      <article><strong>02</strong><FileText aria-hidden="true" /><div><h2>观点有原文</h2><p>公告、研报、段子、录音和股评保留入口。</p></div></article>
      <article><strong>03</strong><Radar aria-hidden="true" /><div><h2>任务可回看</h2><p>调研、量化、分析和打包都保留状态与结果。</p></div></article>
    </section>

    <section className="landing-flow-section" id="research-flow" aria-labelledby="landing-flow-title">
      <div className="landing-section-heading"><span>01 / 研究方法</span><h2 id="landing-flow-title">从问题到证据，<br />再到可跟踪的判断。</h2><p>同一标的和同一时间范围的数据跨页面共享，不需要在每个功能里重新抓取。</p></div>
      <div className="landing-flow-stage">
        {researchFlow.map(({ number, title, detail, icon: Icon }) => <article key={title}><strong>{number}</strong><Icon aria-hidden="true" /><h3>{title}</h3><p>{detail}</p></article>)}
      </div>
    </section>

    <section className="landing-platform" id="real-product" aria-labelledby="landing-platform-title">
      <div className="landing-section-heading"><span>02 / 真实产品</span><h2 id="landing-platform-title">八个界面，<br />对应八类研究任务。</h2><p>下面均为当前程序的实际页面，不使用概念图或虚构收益。数值会随交易日、同步状态和用户权限变化。</p></div>
      <div className="landing-feature-index">
        {productTours.map(({ number, eyebrow, title, href, action, icon: Icon, description, data, output, screenshot, alt }) => <article className="landing-feature-card" key={title}>
          <figure className="landing-product-shot">
            <div className="landing-window-bar"><span>{number}</span><b>{title}</b><em>实机截图</em></div>
            <img src={screenshot} alt={alt} loading="lazy" />
          </figure>
          <div className="landing-feature-copy">
            <div className="landing-feature-title"><Icon aria-hidden="true" /><span>{eyebrow}</span><h3>{title}</h3></div>
            <p className="landing-feature-description">{description}</p>
            <dl className="landing-feature-details"><div><dt>使用数据</dt><dd>{data}</dd></div><div><dt>形成输出</dt><dd>{output}</dd></div></dl>
            <Link to={href}>{action}<ArrowRight aria-hidden="true" /></Link>
          </div>
        </article>)}
      </div>
      <div className="landing-utility-grid" aria-label="其他研究入口">
        {utilityEntries.map(({ title, detail, href, icon: Icon }, index) => <Link to={href} key={title}><span>{String(index + 9).padStart(2, '0')}</span><Icon aria-hidden="true" /><div><h3>{title}</h3><p>{detail}</p></div><ArrowRight aria-hidden="true" /></Link>)}
      </div>
    </section>

    <section className="landing-data-section" id="data-sources" aria-labelledby="landing-data-title">
      <div className="landing-section-heading"><span>03 / 数据基础</span><h2 id="landing-data-title">来源分开，<br />证据打通。</h2><p>每一类数据保留自己的更新时间、来源字段和原文能力，再围绕股票、行业与事件建立关联。</p></div>
      <div className="landing-source-grid">
        {connectedSources.map(({ label, detail, icon: Icon }, index) => <article key={label}><strong>{String(index + 1).padStart(2, '0')}</strong><Icon aria-hidden="true" /><div><h3>{label}</h3><p>{detail}</p></div></article>)}
      </div>
      <p className="landing-data-note"><Sparkles aria-hidden="true" />AI 用于提取、归纳和生成研究任务，不替代原始数据；没有证据的内容应标记待核验。</p>
    </section>

    <section className="landing-final" id="landing-start" aria-labelledby="landing-final-title">
      <span>04 / 开始使用</span><h2 id="landing-final-title">先看证据，<br />再做判断。</h2>
      <p>注册只需要姓名和密码；申请通过后，个人自选股、问股记录与后台任务相互独立。</p>
      <div><Link className="landing-final-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />申请使用</Link><Link className="landing-final-secondary" to="/app">已有账号，进入平台<ArrowRight aria-hidden="true" /></Link></div>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" to="/"><span><DatabaseZap aria-hidden="true" /></span><b>乐子乌超级价值</b></Link><p>全域财经研究平台 · 页面与数据持续更新</p><div><Link to="/guide">使用手册</Link><Link to="/app">研究平台</Link><Link to="/admin">管理员</Link></div></footer>
  </main>;
};

export default LandingPage;
