import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowLeft, ArrowRight, BarChart3, BookOpen, Building2, CheckCircle2,
  DatabaseZap, Download, Expand, FileSearch, FlaskConical, HelpCircle,
  Landmark, LineChart, LockKeyhole, Mic2, Network, Radar, Search, ShieldCheck,
  Star, X,
} from 'lucide-react';
import './UserGuidePage.css';

type GuideChapter = {
  id: string;
  eyebrow: string;
  title: string;
  summary: string;
  screenshot: string;
  alt: string;
  icon: typeof BarChart3;
  steps: string[];
  result: string;
  href: string;
  action: string;
};

const chapters: GuideChapter[] = [
  {
    id: 'market', eyebrow: '每天先看', title: '市场总览', icon: BarChart3,
    summary: '判断当前使用的是盘中快照还是最近交易日收盘数据，再看指数、市场广度、行业分布和自选股。',
    screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览实机页面截图', href: '/app', action: '打开市场总览',
    steps: ['先核对页面顶部的交易日、时点和数据来源。', '点击八个核心指数切换主行情，选择分时、日K、周K、月K或年K。', '继续查看涨跌家数、行业涨跌分布和自选股最新报价。'],
    result: '得到当日或最近交易日的市场状态，不把旧行情误认为实时行情。',
  },
  {
    id: 'watchlist', eyebrow: '研究一家公司', title: '自选股超级看板', icon: Star,
    summary: '输入股票名称或代码建立个人自选股，行情与公告、研报、机构段子、企业事实和公开股评共享同一标的。',
    screenshot: '/landing/screens/super-watchlist.jpg', alt: '自选股超级看板实机页面截图', href: '/super-watchlist', action: '打开自选股看板',
    steps: ['在左侧输入股票名称或代码，从候选结果中确认加入。', '切换分时和K线周期，核对最新价、昨收、成交量与行情时间。', '使用财务估值、资金筹码、公告研报、小作文、一致预期等标签查看证据。'],
    result: '形成围绕单只股票的行情、事实、观点与原文时间线；新增股票会自动触发历史资料补充。',
  },
  {
    id: 'concepts', eyebrow: '识别市场主线', title: '概念题材查看', icon: Network,
    summary: '把六套来源的原始题材、行业层级和成分归属组织成共识地图，解释个股题材权重、Beta 与独特 Alpha。',
    screenshot: '/landing/screens/concept-themes.jpg', alt: '概念题材六源共识与Beta Alpha研究工作台实机截图', href: '/concept-themes', action: '打开概念题材查看',
    steps: ['先按家族、产业链、来源或2源+/3源+/4源+共识门槛缩小题材范围。', '打开题材查看六源成分矩阵、Beta/Alpha四象限和研究优先队列。', '点击股票下钻到主要题材、非共识线索与公司独特证据；需要时对照两个题材或导出CSV。'],
    result: '区分市场共同认可的主线、单源待核验标签、题材共振和公司独立表现，不把题材名直接当作投资结论。',
  },
  {
    id: 'essays', eyebrow: '检索非结构化资料', title: '机构段子与录音', icon: Mic2,
    summary: '检索知识星球增量入库的正文、附件和录音；正文可查看分析标签与原文，录音可以下载或生成AI纪要。',
    screenshot: '/landing/screens/essay-radar.jpg', alt: '机构段子与录音洞察图谱实机页面截图', href: '/essay-radar/feed', action: '打开检索与获取',
    steps: ['在“检索与获取”选择小作文或录音，并设置标题检索或全文检索。', '通过关键词、日期和分析状态缩小范围；点击正文在页面内查看原文。', '勾选结果导出 Excel、批量打包，或将录音提交后台转写与AI纪要。'],
    result: '得到可追溯的原文、结构化标签、附件或录音研究成果；后台任务离开页面后继续执行。',
  },
  {
    id: 'monitor', eyebrow: '持续监控', title: '投资情报台', icon: Radar,
    summary: '按渠道查看公告、研报、新闻、龙虎榜、企业事实、机构段子和公开股评，避免不同性质的信息混在一条流水里。',
    screenshot: '/landing/screens/investment-monitor.jpg', alt: '投资情报台全渠道数据源实机页面截图', href: '/investment-monitor', action: '打开投资情报台',
    steps: ['先在总览检查各渠道最近成功时间、覆盖范围和新鲜度。', '进入全渠道信息流，按来源、股票、日期或关键词筛选。', '需要观察市场结构时进入BI页；需要席位数据时进入龙虎榜页。'],
    result: '知道平台当前有什么数据、数据新不新，并能回到公告、研报或消息原文。',
  },
  {
    id: 'quant', eyebrow: '验证研究假设', title: '量化回测与数据利用', icon: FlaskConical,
    summary: '把机构观点、行情、财务、资金和技术条件组合成后台研究任务，保存参数、数据截止时间和结果快照。',
    screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心实机页面截图', href: '/essay-quant', action: '打开量化工作台',
    steps: ['从事件研究、多因子、机构胜率、信息趋势共振等方法选择研究模板。', '明确样本范围、入场和退出条件、交易成本及数据截止时间。', '启动后台任务；完成后在任务中心查看收益、回撤、置信区间和稳健性。'],
    result: '得到可复现的研究结果和停止采用条件，而不是只有一个无法解释的收益数字。',
  },
  {
    id: 'download', eyebrow: '筛选并交付资料', title: '数据一站式获取', icon: Download,
    summary: '从本地资料库精确筛选研报、机构段子、录音或其他数据，只打包用户确认的结果。',
    screenshot: '/landing/screens/data-acquisition.jpg', alt: '数据一站式获取筛选工作台实机页面截图', href: '/data-acquisition', action: '打开数据一站式获取',
    steps: ['先选择资料类型，再设置标题、内容、券商、公司、行业、作者和日期等条件。', '点击搜索从本地库召回结果，逐条检查标题、日期、来源和PDF状态。', '勾选真正需要的资料后提交后台打包，等待真实进度完成再下载。'],
    result: '得到经过人工确认的原文、PDF、附件或链接包，不会把无关的全量资料直接交付。',
  },
  {
    id: 'industry', eyebrow: '快速建立行业认知', title: '行业调研', icon: Landmark,
    summary: '围绕产业链、趋势、龙头企业、核心痛点、应用场景和验证指标组织跨渠道证据。',
    screenshot: '/landing/screens/industry-research.jpg', alt: '行业调研任务工作台实机页面截图', href: '/industry-research', action: '打开行业调研',
    steps: ['输入明确行业主题，并补充地区、时间范围或关注问题。', '启动研究后在任务框查看证据召回、章节生成和报告状态。', '打开已完成报告，沿证据编号核对研报、公告、机构语料与公司事实。'],
    result: '得到可继续补强的产业链地图、公司候选、关键问题和带引用的行业报告。',
  },
];

const sourceRows = [
  ['Tushare', '指数、个股行情、财务、资金、研报与资讯', '查看交易日、指标口径和接口更新时间'],
  ['巨潮资讯', '上市公司公告及PDF原文', '以公告发布时间、证券代码和原文为准'],
  ['知识星球 MCP', '机构段子、图片、文件和录音', '区分原文、AI提取结果与待核验观点'],
  ['天眼查', '企业登记、风险和知识产权事实', '核对企业主体、事件日期和接口返回字段'],
  ['公开股评', '东方财富股吧公开讨论', '属于投资者观点，不等同于公司事实'],
  ['本地 SQLite', '跨页面共享的行情与证据索引', '页面应显示数据时间、来源和最近同步状态'],
  ['六源题材库', '同花顺、东方财富板块/题材、开盘啦、通达信与申万', '先看独立来源数、扫描覆盖率，再核验成分和入选理由'],
];

const quickTasks = [
  { title: '判断今天市场怎么样', detail: '交易日与数据源 → 指数 → 市场广度 → 行业分布', href: '#market', icon: LineChart },
  { title: '搞明白一家公司', detail: '加入自选 → 行情 → 基本面 → 公告研报 → 观点证据', href: '#watchlist', icon: Building2 },
  { title: '找一批研报或录音', detail: '选择资料 → 细化筛选 → 人工确认 → 后台打包', href: '#download', icon: FileSearch },
  { title: '研究一个行业', detail: '提出问题 → 固定证据 → 后台生成 → 沿引用核验', href: '#industry', icon: Landmark },
  { title: '判断股票属于什么主线', detail: '共识门槛 → 六源矩阵 → Beta/Alpha → 公司证据', href: '#concepts', icon: Network },
];

const UserGuidePage = () => {
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);

  useEffect(() => {
    document.title = '使用手册 - 乐子乌超级价值';
  }, []);

  useEffect(() => {
    if (!lightbox) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLightbox(null);
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [lightbox]);

  return <main className="guide-page">
    <header className="guide-header">
      <Link className="guide-brand" to="/"><span><DatabaseZap /></span>乐子乌超级价值</Link>
      <div><Link to="/"><ArrowLeft />返回介绍首页</Link><Link className="is-primary" to="/app">进入研究平台<ArrowRight /></Link></div>
    </header>

    <section className="guide-hero">
      <div className="guide-hero-copy">
        <span className="guide-kicker"><BookOpen />网站使用手册 · 2026-08-31</span>
        <h1>从问题出发，<br /><em>找到数据，完成研究。</em></h1>
        <p>本手册使用当前网站的真实页面截图，说明每个入口解决什么问题、怎样操作、如何判断数据是否可用，以及最终可以得到什么。</p>
        <div className="guide-hero-actions"><a href="#start">从第一次使用开始<ArrowRight /></a><a href="#workspaces">直接查看工作台</a></div>
      </div>
      <figure className="guide-hero-shot">
        <img src="/landing/screens/market-overview.jpg" alt="乐子乌超级价值市场总览实际页面" />
        <figcaption><CheckCircle2 />真实运行页面；行情日期和数值会自动更新</figcaption>
      </figure>
    </section>

    <section className="guide-task-picker" aria-labelledby="guide-task-title">
      <div><span>按目标查手册</span><h2 id="guide-task-title">你现在想完成什么？</h2></div>
      <div className="guide-task-grid">{quickTasks.map(({ title, detail, href, icon: Icon }) => <a href={href} key={title}><Icon /><strong>{title}</strong><span>{detail}</span><ArrowRight /></a>)}</div>
    </section>

    <div className="guide-layout">
      <aside className="guide-toc" aria-label="使用手册目录">
        <span>目录</span>
        <a href="#start">01 · 注册与第一次登录</a>
        <a href="#read-data">02 · 先读懂数据口径</a>
        <a href="#workspaces">03 · 八个工作台</a>
        {chapters.map(({ id, title }) => <a className="is-child" href={`#${id}`} key={id}>{title}</a>)}
        <a href="#workflow">04 · 推荐研究流程</a>
        <a href="#sources">05 · 数据来源</a>
        <a href="#faq">06 · 常见问题</a>
      </aside>

      <article className="guide-content">
        <section className="guide-section" id="start">
          <div className="guide-section-heading"><span>01</span><div><small>第一次使用</small><h2>注册、审核与登录</h2></div></div>
          <div className="guide-start-grid">
            <article><strong>1</strong><h3>提交注册</h3><p>在介绍首页右上角点击“注册”，只填写姓名和密码。姓名用于识别账号，密码请自行妥善保存。</p></article>
            <article><strong>2</strong><h3>等待管理员批准</h3><p>提交后进入审核队列。未批准的账号不能访问内部行情、资料、AI任务和用户数据。</p></article>
            <article><strong>3</strong><h3>登录工作台</h3><p>批准后使用姓名和密码登录。个人自选股、问股记录与后台任务按用户隔离，市场事实数据共享。</p></article>
          </div>
          <div className="guide-callout"><LockKeyhole /><div><strong>安全边界</strong><p>公开介绍页和本手册无需登录；应用页面、数据接口和用户资料仍需要有效账号。管理员设置不会出现在普通用户界面。</p></div></div>
        </section>

        <section className="guide-section" id="read-data">
          <div className="guide-section-heading"><span>02</span><div><small>阅读任何结论之前</small><h2>先确认时间、来源与性质</h2></div></div>
          <div className="guide-reading-grid">
            <article><ShieldCheck /><h3>时间</h3><p>先看交易日和具体时点。休市日通常展示最近交易日收盘，不应写成“今日实时”。</p></article>
            <article><DatabaseZap /><h3>来源</h3><p>区分 Tushare、腾讯行情、巨潮公告、知识星球、天眼查和公开股评。</p></article>
            <article><Search /><h3>性质</h3><p>公司公告属于事实材料；研报、机构段子和股评属于观点，需要继续核验。</p></article>
          </div>
          <p className="guide-rule">最基本的使用原则：<b>行情看零轴和交易日，事实看原文，观点看证据，AI结果看引用。</b></p>
        </section>

        <section className="guide-section guide-workspaces" id="workspaces">
          <div className="guide-section-heading"><span>03</span><div><small>核心功能</small><h2>八个工作台怎么用</h2></div></div>
          {chapters.map(({ id, eyebrow, title, summary, screenshot, alt, icon: Icon, steps, result, href, action }, index) => <section className="guide-chapter" id={id} key={id}>
            <div className="guide-chapter-top"><span>{String(index + 1).padStart(2, '0')}</span><div><small>{eyebrow}</small><h3><Icon />{title}</h3><p>{summary}</p></div></div>
            <button className="guide-screenshot" type="button" onClick={() => setLightbox({ src: screenshot, alt })} aria-label={`放大查看${title}截图`}>
              <img src={screenshot} alt={alt} loading="lazy" />
              <span><Expand />点击放大真实页面截图</span>
            </button>
            <div className="guide-steps"><h4>操作步骤</h4><ol>{steps.map((step, stepIndex) => <li key={step}><strong>{stepIndex + 1}</strong><span>{step}</span></li>)}</ol></div>
            <div className="guide-result"><CheckCircle2 /><div><strong>你会得到</strong><p>{result}</p></div></div>
            <Link className="guide-open-page" to={href}>{action}<ArrowRight /></Link>
          </section>)}
        </section>

        <section className="guide-section" id="workflow">
          <div className="guide-section-heading"><span>04</span><div><small>推荐方法</small><h2>一套可复用的研究流程</h2></div></div>
          <div className="guide-workflow">
            <article><strong>问题</strong><p>写清楚要研究的行业、公司、时间范围和决策问题。</p></article>
            <ArrowRight />
            <article><strong>证据</strong><p>召回行情、公告、研报、机构语料、企业事实和公开讨论。</p></article>
            <ArrowRight />
            <article><strong>核验</strong><p>检查交易日、来源、原文、观点冲突和缺失信息。</p></article>
            <ArrowRight />
            <article><strong>跟踪</strong><p>放入自选、情报监控或量化任务，持续观察验证条件。</p></article>
          </div>
        </section>

        <section className="guide-section" id="sources">
          <div className="guide-section-heading"><span>05</span><div><small>数据说明</small><h2>来源、用途与核验方式</h2></div></div>
          <div className="guide-source-table" role="table" aria-label="平台数据来源说明">
            <div role="row"><b role="columnheader">来源</b><b role="columnheader">主要用途</b><b role="columnheader">使用时检查</b></div>
            {sourceRows.map(([source, use, check]) => <div role="row" key={source}><strong role="cell">{source}</strong><span role="cell">{use}</span><span role="cell">{check}</span></div>)}
          </div>
        </section>

        <section className="guide-section" id="faq">
          <div className="guide-section-heading"><span>06</span><div><small>遇到问题时</small><h2>常见问题</h2></div></div>
          <div className="guide-faq">
            <details><summary>页面上的数据为什么和另一个页面不同？</summary><p>先比较股票代码、交易日、时点和来源。若其中一个仍是旧交易日，可切回页面等待自动更新；不要只比较颜色或一个涨跌幅数字。</p></details>
            <details><summary>为什么搜索结果为空？</summary><p>先清除过细的筛选条件，再确认选择的是标题检索还是全文检索、日期范围是否覆盖目标资料。数据一站式获取只返回符合条件的本地库结果。</p></details>
            <details><summary>后台任务可以离开页面吗？</summary><p>可以。量化、行业调研、录音纪要和文件打包均以后台任务运行。返回对应任务中心即可查看进度和已完成结果。</p></details>
            <details><summary>如何确认AI摘要不是编造的？</summary><p>打开引用的原文、公告、研报或转写文本核对。没有原文支持的内容只能作为待核验线索，不应直接作为事实使用。</p></details>
            <details><summary>这些结果可以直接作为买卖建议吗？</summary><p>不可以。平台用于整理事实、观点和验证研究假设；行情可能延迟，上游数据可能缺失，AI也可能误判。投资决策需由用户独立完成。</p></details>
          </div>
          <div className="guide-end"><HelpCircle /><div><h3>仍然不知道从哪里开始？</h3><p>先进入市场总览核对交易日，再把关注公司加入自选股；这是最短的入门路径。</p></div><Link to="/app">进入市场总览<ArrowRight /></Link></div>
        </section>
      </article>
    </div>

    <footer className="guide-footer"><span>乐子乌超级价值使用手册 · 基于 2026-08-31 当前版本</span><div><Link to="/">产品介绍</Link><Link to="/app">研究平台</Link></div></footer>

    {lightbox ? <div className="guide-lightbox" role="dialog" aria-modal="true" aria-label="页面截图预览" onClick={() => setLightbox(null)}>
      <button type="button" onClick={() => setLightbox(null)} aria-label="关闭截图预览"><X /></button>
      <img src={lightbox.src} alt={lightbox.alt} onClick={(event) => event.stopPropagation()} />
    </div> : null}
  </main>;
};

export default UserGuidePage;
