import { useCallback, useEffect, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowDown, ArrowRight, BarChart3, Building2, CheckCircle2, DatabaseZap,
  Download, FileSearch, FileText, FlaskConical, Landmark, LineChart,
  LockKeyhole, MessageCircleMore, Mic2, Radar, Search, ShieldCheck,
  Sparkles, Star, UserRoundPlus, Waves,
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

const researchFlow = [
  { number: '01', title: '确定问题', detail: '先明确市场、行业或公司问题，以及日期、标的和资料范围。', icon: Search },
  { number: '02', title: '召回证据', detail: '从统一数据库检索行情、公告、研报、机构语料、企业事实和公开讨论。', icon: FileSearch },
  { number: '03', title: '交叉验证', detail: '把观点与价格、财务和公司事实放在同一时间轴，保留来源和原文入口。', icon: ShieldCheck },
  { number: '04', title: '形成任务', detail: '将需要持续观察的假设交给自选股、情报监控、行业研究或量化任务。', icon: Radar },
];

const productTours = [
  {
    number: '01', eyebrow: '市场与行情', title: '市场总览', href: '/app', action: '打开市场总览', icon: BarChart3,
    description: '把核心指数、分时与 K 线、全市场涨跌家数、行业分布、海外市场和个人自选股放在同一页，并明确显示实际交易日与数据来源。',
    data: ['Tushare 指数与全市场日线', '腾讯行情分钟快照', '本地 SQLite 行情库'],
    outputs: ['指数与个股周期切换', '市场广度及行业涨跌分布', '自选股行情与最新情报'],
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览真实页面，展示指数、交易日、分时行情和成交量',
  },
  {
    number: '02', eyebrow: '个股研究', title: '自选股超级看板', href: '/super-watchlist', action: '进入自选股看板', icon: Star,
    description: '新增股票后自动建立行情和全渠道信息档案。报价、K 线、财务、资金、公告、研报、机构段子、企业风险、股评和一致预期围绕同一股票共享。',
    data: ['实时/最近事实行情', '公告、研报与财务指标', '机构段子、企业事实与公开股评'],
    outputs: ['统一口径的个股行情', '可回原文的事实时间线', '财务估值、资金筹码与一致预期'],
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '自选股超级看板真实页面，展示中际旭创行情、估值和分时图',
  },
  {
    number: '03', eyebrow: '非结构化研究', title: '机构段子与录音', href: '/essay-radar', action: '查看机构语料', icon: Mic2,
    description: '知识星球新增内容自动增量入库。正文可以按标题或全文检索，图片和文件保留查看入口，录音可以筛选、批量下载或提交后台转写与纪要任务。',
    data: ['知识星球 MCP 增量内容', '正文、图片、文件与录音元数据', 'AI 结构化标签与日报结果'],
    outputs: ['今日研判与多周期洞察', '全库检索、Excel 导出与批量下载', '主题趋势、个股提及和录音纪要'],
    screenshot: '/landing/screens/essay-radar.jpg', alt: '机构段子与录音真实洞察页面，展示语料统计、主题与证据覆盖',
  },
  {
    number: '04', eyebrow: '全渠道监控', title: '投资情报台', href: '/investment-monitor', action: '进入投资情报台', icon: Radar,
    description: '不同来源不再混成一条流水，而是按公告、研报、资讯、龙虎榜、公司事实、企业风险、机构段子和公开股评分渠道查看。',
    data: ['巨潮公告与 Tushare 数据', '天眼查企业事实', '知识星球与东方财富公开股评'],
    outputs: ['来源级新鲜度与同步状态', '全渠道信息流与原文入口', 'BI 汇总、龙虎榜和自选股筛选'],
    screenshot: '/landing/screens/investment-monitor.jpg', alt: '投资情报台真实页面，展示全渠道数据源及其同步状态',
  },
  {
    number: '05', eyebrow: '验证研究假设', title: '量化回测与数据利用', href: '/essay-quant', action: '创建量化任务', icon: FlaskConical,
    description: '研究以后台任务运行，离开页面不会中断。用户可以从模板或自然语言建立事件研究、多因子、机构胜率、信息与趋势共振等可复现任务。',
    data: ['行情与交易日历', '机构段子事件和来源标签', '财务、资金与技术因子'],
    outputs: ['样本、交易约束和数据截止时间', '收益、回撤、置信区间与分组稳定性', '可复现任务、结果快照和方法说明'],
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心真实页面，展示五步研究流程与后台任务',
  },
  {
    number: '06', eyebrow: '资料交付', title: '数据一站式获取', href: '/data-acquisition', action: '检索与下载数据', icon: Download,
    description: '先从本地资料库按标题、摘要、券商、公司、研报类型、行业、作者和日期筛选，再由后台打包用户勾选的原文、附件或 PDF 链接。',
    data: ['本地研报元数据与 PDF 链接库', '知识星球正文、附件和录音', '公告、行情、财务与企业事实接口'],
    outputs: ['可审阅的筛选结果', '勾选后的文件或链接包', '真实获取与打包进度'],
    screenshot: '/landing/screens/data-acquisition.jpg', alt: '数据一站式获取真实页面，展示研报筛选字段与后台任务状态',
  },
  {
    number: '07', eyebrow: '快速建立认知', title: '行业调研', href: '/industry-research', action: '开始行业调研', icon: Landmark,
    description: '输入行业主题后，后台任务围绕产业链、发展趋势、龙头公司、痛点、应用场景和关键验证指标组织证据，并保存可继续补强的研究报告。',
    data: ['研报、公告和公司资料', '机构段子、录音纪要与资讯', '行情、财务和企业事实'],
    outputs: ['产业链与关键环节', '龙头、趋势、痛点和应用场景', '证据引用、待验证问题和深度报告'],
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业调研真实页面，展示光模块研究任务入口与四步方法',
  },
];

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
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.08 });
    revealNodes.forEach((node) => observer.observe(node));

    let frame = 0;
    const updateScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
        page.style.setProperty('--landing-scroll', `${Math.min(window.scrollY / max, 1)}`);
        page.style.setProperty('--landing-parallax', `${Math.min(window.scrollY * 0.07, 90)}px`);
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
    pageRef.current?.style.setProperty('--pointer-x', `${x * 100}%`);
    pageRef.current?.style.setProperty('--pointer-y', `${y * 100}%`);
  }, []);

  return <main ref={pageRef} className="landing-page" onPointerMove={handlePointerMove}>
    <div className="landing-scroll-progress" aria-hidden="true"><i /></div>
    <header className="landing-header">
      <Link className="landing-brand" to="/" aria-label="乐子乌超级价值首页"><span><DatabaseZap aria-hidden="true" /></span>乐子乌超级价值</Link>
      <nav aria-label="首页栏目"><a href="#real-product">真实界面</a><a href="#research-flow">研究流程</a><a href="#data-sources">数据来源</a></nav>
      <div className="landing-header-actions">
        <Link className="landing-register-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />注册</Link>
        <Link className="landing-admin-enter" to="/admin"><LockKeyhole aria-hidden="true" />管理员</Link>
        <Link className="landing-header-enter" to="/app">进入平台<ArrowRight aria-hidden="true" /></Link>
      </div>
    </header>

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero-mesh" aria-hidden="true" />
      <div className="landing-hero-copy" data-landing-reveal>
        <div className="landing-proof-label"><CheckCircle2 aria-hidden="true" />以下功能均为已上线真实页面</div>
        <h1 id="landing-title">让市场信息，<span>成为可验证的研究优势。</span></h1>
        <p>一套面向中国股票研究的事实与证据工作台。连接行情、公告、研报、机构段子与录音、企业事实、公开股评和量化研究；每个页面明确显示时间、来源与原文入口。</p>
        <div className="landing-actions"><Link className="landing-enter" to="/app">进入研究平台<ArrowRight aria-hidden="true" /></Link><a className="landing-discover" href="#real-product">查看真实界面<ArrowDown aria-hidden="true" /></a></div>
      </div>
      <figure className="landing-hero-screen" data-landing-reveal>
        <div className="landing-window-bar"><i /><i /><i /><span>市场总览 · 实机截图</span></div>
        <img src="/landing/screens/market-overview.jpg" alt="乐子乌超级价值市场总览真实页面" fetchPriority="high" />
        <figcaption><span>截图采集于 2026-08-31</span><b>页面中的日期和来源随数据更新</b></figcaption>
      </figure>
      <div className="landing-hero-sources" aria-label="平台真实接入的数据源">
        {connectedSources.map(({ label, icon: Icon }) => <span key={label}><Icon aria-hidden="true" />{label}</span>)}
      </div>
    </section>

    <section className="landing-truth" id="real-product" aria-labelledby="landing-truth-title">
      <div className="landing-section-heading" data-landing-reveal><span>真实产品导览</span><h2 id="landing-truth-title">不是概念图。<br />下面每张都是实际运行页面。</h2><p>截图来自当前程序和本地真实数据库。数值会随交易日、数据同步状态和用户权限变化，介绍页不固定展示虚构收益或虚构案例。</p></div>
      <div className="landing-truth-grid">
        <article data-landing-reveal><strong>01</strong><h3>数据有口径</h3><p>行情点位、涨跌幅、市场广度和个股价格显示实际交易日与来源，避免把旧数据写成“实时”。</p></article>
        <article data-landing-reveal><strong>02</strong><h3>观点有原文</h3><p>公告、研报、机构段子、录音纪要和股评保留来源、发布时间及原文或文件入口。</p></article>
        <article data-landing-reveal><strong>03</strong><h3>任务可回看</h3><p>量化、行业研究、AI 分析和文件打包在后台执行，完成后保留参数、状态和结果。</p></article>
      </div>
    </section>

    <section className="landing-flow-section" id="research-flow" aria-labelledby="landing-flow-title">
      <div className="landing-section-heading" data-landing-reveal><span>研究流程</span><h2 id="landing-flow-title">从问题出发，<br />沿证据形成结论。</h2><p>数据跨页面共享；同一标的和同一时间范围不需要在每个功能里重新抓取一遍。</p></div>
      <div className="landing-flow-stage">
        {researchFlow.map(({ number, title, detail, icon: Icon }) => <article key={title} data-landing-reveal><strong>{number}</strong><Icon aria-hidden="true" /><h3>{title}</h3><p>{detail}</p></article>)}
      </div>
    </section>

    <section className="landing-platform" aria-labelledby="landing-platform-title">
      <div className="landing-section-heading" data-landing-reveal><span>七个真实工作台</span><h2 id="landing-platform-title">每个入口解决什么，<br />使用什么数据，输出什么。</h2></div>
      <div className="landing-feature-index">
        {productTours.map(({ number, eyebrow, title, href, action, icon: Icon, description, data, outputs, screenshot, alt }, index) => <article className={`landing-feature-row${index % 2 ? ' is-reverse' : ''}`} key={title} data-landing-reveal>
          <div className="landing-feature-copy">
            <strong className="landing-feature-number">{number}</strong>
            <div className="landing-feature-title"><Icon aria-hidden="true" /><span>{eyebrow}</span><h3>{title}</h3></div>
            <p className="landing-feature-description">{description}</p>
            <div className="landing-feature-details">
              <div><h4>使用的数据</h4><ul>{data.map(item => <li key={item}>{item}</li>)}</ul></div>
              <div><h4>得到的输出</h4><ul>{outputs.map(item => <li key={item}>{item}</li>)}</ul></div>
            </div>
            <Link to={href}>{action}<ArrowRight aria-hidden="true" /></Link>
          </div>
          <figure className="landing-product-shot">
            <div className="landing-window-bar"><i /><i /><i /><span>{title} · 当前版本实机截图</span></div>
            <img src={screenshot} alt={alt} loading="lazy" />
            <figcaption>截图采集于 2026-08-31 · 页面数据会自动更新</figcaption>
          </figure>
        </article>)}
      </div>
    </section>

    <section className="landing-data-section" id="data-sources" aria-labelledby="landing-data-title">
      <div className="landing-section-heading" data-landing-reveal><span>数据如何进入平台</span><h2 id="landing-data-title">来源分开，证据打通。</h2><p>平台不把不同渠道混成无法追溯的摘要。每一类数据保留自己的更新时间、来源字段和原文能力，再围绕股票、行业与事件建立关联。</p></div>
      <div className="landing-source-grid">
        {connectedSources.map(({ label, detail, icon: Icon }) => <article key={label} data-landing-reveal><Icon aria-hidden="true" /><div><h3>{label}</h3><p>{detail}</p></div></article>)}
      </div>
      <p className="landing-data-note"><Sparkles aria-hidden="true" />AI 用于提取、归纳和生成研究任务，不替代原始数据；没有证据的内容应标记待核验。</p>
    </section>

    <section className="landing-final" id="landing-start" aria-labelledby="landing-final-title" data-landing-reveal>
      <h2 id="landing-final-title">先看真实数据，<br />再做研究判断。</h2>
      <p>注册只需要姓名和密码；申请通过后，个人自选股、问股记录与后台任务相互独立。</p>
      <div><Link className="landing-final-enter" to="/access?mode=register&redirect=%2Fapp"><UserRoundPlus aria-hidden="true" />申请使用</Link><Link className="landing-final-secondary" to="/app">已有账号，进入平台<ArrowRight aria-hidden="true" /></Link></div>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" to="/"><span><DatabaseZap aria-hidden="true" /></span>乐子乌超级价值</Link><p>截图和功能说明基于 2026-08-31 当前版本</p><div><Link to="/app">研究平台</Link><Link to="/admin">管理员</Link></div></footer>
  </main>;
};

export default LandingPage;
