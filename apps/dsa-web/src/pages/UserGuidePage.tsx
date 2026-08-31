import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  ArrowLeft, ArrowRight, BarChart3, BookOpen, Bot, Building2,
  CheckCircle2, ChevronRight, DatabaseZap, Download, Expand, FileSearch,
  Filter, FlaskConical, HelpCircle, KeyRound, Landmark, LineChart,
  ListChecks, LockKeyhole, Mic2, Network, Radar, Search, ShieldCheck,
  Star, TableProperties, Target, UserCheck, Workflow, X,
} from 'lucide-react';
import './UserGuidePage.css';

type Category = '全部' | '每日使用' | '公司研究' | '行业题材' | '机构语料' | '数据量化' | '系统管理';
type Hotspot = { number: number; x: number; y: number; title: string; detail: string };
type GuideModule = {
  name: string; route?: string; purpose: string; data: string; actions: string[]; output: string;
};
type GuideChapter = {
  id: string; category: Exclude<Category, '全部'>; eyebrow: string; title: string; summary: string;
  icon: LucideIcon; href: string; action: string; screenshot?: string; alt?: string; hotspots?: Hotspot[];
  purpose: string; data: string[]; outputs: string[]; steps: string[]; modules: GuideModule[]; verify: string[];
};

const chapters: GuideChapter[] = [
  {
    id: 'market', category: '每日使用', eyebrow: '开盘前 / 盘中 / 收盘后', title: '市场总览', icon: BarChart3,
    summary: '用统一时间口径查看核心指数、分时与K线、市场广度、行业分布和个人自选股。',
    href: '/app', action: '进入市场总览', screenshot: '/landing/screens/market-overview.jpg', alt: '市场总览实机页面截图',
    purpose: '先回答市场处于什么状态，再决定今天需要研究哪些股票、行业和事件。',
    data: ['腾讯/新浪盘中快照与分钟线', 'Tushare 指数日线、全市场日线和资金数据', 'SQLite 共享行情缓存', '个人自选股与统一事件库'],
    outputs: ['当前或最近交易日的指数状态', '上涨/下跌家数和涨跌幅分桶', '行业领涨领跌分布', '自选股行情与最新证据入口'],
    hotspots: [
      { number: 1, x: 34, y: 15, title: '核心指数切换', detail: '每张卡都显示交易日、收盘/盘中口径、点位、涨跌幅和来源；点击后主图随之切换。' },
      { number: 2, x: 35, y: 28, title: '四项口径确认', detail: '先看行情基准日、最新数据时间、市场广度覆盖股票数和自选覆盖数。' },
      { number: 3, x: 44, y: 42, title: '周期与范围', detail: '分时/日K/周K/月K/年K与当日/5日组合，零轴统一使用上一交易日收盘。' },
      { number: 4, x: 74, y: 65, title: '主行情图', detail: '悬浮查看对应时间点的价格、涨跌幅和成交量；图上日期必须与标题口径一致。' },
    ],
    steps: ['先读顶部更新时间、交易日和数据来源，不先看颜色。', '点击核心指数切换主图，按研究周期选择分时或K线。', '查看市场广度和涨跌分桶，判断上涨是否有扩散。', '对照行业分布，再切换自选股确认个股是否跟随市场。', '点击最新情报或分析入口，进入公司证据链。'],
    modules: [
      { name: '核心指数与主K线', route: '/app', purpose: '查看A股核心指数并切换主图。', data: '指数日线 + 盘中分钟快照', actions: ['切换八个核心指数', '切换分时/日K/周K/月K/年K', '切换当日/5日', '悬浮读取时间点'], output: '指数价格、昨收零轴、涨跌幅、成交量和数据时间。' },
      { name: '市场广度', purpose: '判断上涨/下跌覆盖面，不被单一指数误导。', data: '全市场有效交易股票', actions: ['查看上涨/下跌/平盘', '查看七档涨跌分桶', '核对涨停与跌停家数'], output: '市场赚钱效应和极端波动分布。' },
      { name: '行业涨跌分布', purpose: '发现当日强弱行业与轮动方向。', data: '实时行业板块快照或最近交易日收盘', actions: ['查看行业分桶', '阅读领涨/领跌行业', '核对行业数据日期'], output: '行业扩散程度和强弱排序。' },
      { name: '海外市场与自选股', purpose: '补充跨市场背景并快速进入个人关注标的。', data: '海外指数最近收盘 + 个人自选股', actions: ['逐项核对海外交易日', '切换自选股', '打开全部情报'], output: '跨市场背景和个股研究入口。' },
    ],
    verify: ['休市日应明确显示“某日收盘”，不能把最近交易日写成今日。', '分时零轴必须是上一交易日收盘价。', '指数卡、主图和悬浮框的代码、日期、涨跌幅必须一致。'],
  },
  {
    id: 'research-center', category: '公司研究', eyebrow: '证据优先的决策入口', title: '研究决策台', icon: Target,
    summary: '把事实变化、行情确认、预期交叉、矛盾失效和待核验任务组织成一张研究证据包。',
    href: '/research-center', action: '进入研究决策台',
    purpose: '把平台里分散的数据变成可执行的研究顺序，回答“现在知道什么、还缺什么、下一步核验什么”。',
    data: ['统一事件库', '自选股与公司证据', '行情与财务快照', '数据源健康和覆盖情况'],
    outputs: ['公司证据包', '事实与观点的分层', '矛盾/失效条件', '研究门与待办核验清单'],
    steps: ['选择当前研究股票或研究对象。', '从事实变化开始，不先看AI结论。', '用行情确认和预期交叉检查观点是否已经反映。', '记录反例、矛盾和会让判断失效的条件。', '把缺失证据放入研究门，转到对应工作台补齐。'],
    modules: [
      { name: '01 事实变化', purpose: '只看可核验的新事实。', data: '公告、财务、股东治理、企业事实', actions: ['按时间阅读事实', '打开原文', '区分发生日与入库日'], output: '事实变化列表。' },
      { name: '02 行情确认', purpose: '确认事件后价格、成交和相对市场表现。', data: '个股/指数行情与技术指标', actions: ['核对事件前后走势', '比较指数/行业', '观察成交变化'], output: '市场是否确认该信息。' },
      { name: '03 预期交叉', purpose: '比较券商预测、机构段子预期和实际数据。', data: '研报预测 + 小作文预期抽取 + 财务实际值', actions: ['检查预测日期和目标期', '找分歧', '回到原文'], output: '一致预期与预期差线索。' },
      { name: '04 矛盾与失效', purpose: '避免只收集支持自己观点的证据。', data: '多空观点、风险项、相反行情', actions: ['列反证', '写失效阈值', '标记待核验'], output: '研究结论的边界。' },
      { name: '05 研究门', purpose: '把未解决问题转成下一步动作。', data: '缺口清单与来源状态', actions: ['分配到公告/研报/语料/量化', '记录完成状态'], output: '可追踪的核验任务。' },
    ],
    verify: ['事实、观点和模型推断必须分开。', '每个结论都应能回到来源、日期和原文。', '没有反证和失效条件的“结论”只能算假设。'],
  },
  {
    id: 'industry', category: '行业题材', eyebrow: '快速建立行业认知', title: '行业调研', icon: Landmark,
    summary: '输入一个行业，后台并行组织产业链、趋势、龙头、痛点、应用场景、验证指标和长篇报告。',
    href: '/industry-research', action: '进入行业调研', screenshot: '/landing/screens/industry-research.jpg', alt: '行业调研实机页面截图',
    purpose: '在尽可能短的时间内建立可继续验证的行业框架，而不是只生成一篇不可追溯的长文。',
    data: ['两年研报链接与摘要库', '机构段子与录音纪要', '公司公告、财务与行情', '企业事实、新闻与题材关系'],
    outputs: ['产业链地图', '趋势与拐点', '龙头候选对比', '痛点与应用场景', '访谈提纲', '带引用的长篇报告'],
    hotspots: [
      { number: 1, x: 43, y: 34, title: '研究主题', detail: '填写行业名称并选择回看范围；可用关注问题限定研究边界。' },
      { number: 2, x: 76, y: 34, title: '启动后台任务', detail: '提交后任务在服务器继续执行，离开页面不影响进度。' },
      { number: 3, x: 48, y: 51, title: '任务与历史报告', detail: '按全部/运行中/已完成/失败筛选；点已完成任务重新打开结果。' },
      { number: 4, x: 56, y: 83, title: '四阶段方法', detail: '定义边界、建立产业链、验证龙头与趋势、形成结论，避免一步生成空泛报告。' },
    ],
    steps: ['输入具体行业，必要时增加地区、技术路线或关键问题。', '选择资料回看范围并启动后台任务。', '在任务框观察召回、去重、结构化和写作进度。', '先看研究地图和证据覆盖，再读公司对比与长篇报告。', '沿证据编号打开原始研报、公告或语料，补充开放问题。'],
    modules: [
      { name: '任务中心', purpose: '管理运行中、已完成与失败的行业研究。', data: '按用户隔离的后台任务库', actions: ['筛选任务状态', '查看真实进度', '重开历史报告', '失败后重试'], output: '可离页执行、可恢复的研究任务。' },
      { name: '研究地图', purpose: '先建立产业链结构和关键问题。', data: '跨渠道证据召回与实体关系', actions: ['浏览上游/中游/下游', '查看趋势/痛点/应用', '检查开放问题'], output: '行业认知骨架。' },
      { name: '公司对比', purpose: '比较龙头、环节位置和证据强弱。', data: '公司财务、公告、研报、行情与题材关系', actions: ['比较公司候选', '查看证据覆盖', '进入公司研究'], output: '公司候选与差异化线索。' },
      { name: '证据库', purpose: '核验报告中的每个关键判断。', data: '研报/公告/机构语料/企业事实原文索引', actions: ['按来源筛选', '按时间排序', '打开原文'], output: '可追溯引用账本。' },
      { name: '访谈问题', purpose: '把未知点变成可向专家或公司提问的问题。', data: '证据缺口与矛盾项', actions: ['按产业链环节查看', '复制问题', '补充答案'], output: '专家访谈/调研提纲。' },
      { name: '研究报告', purpose: '输出结论优先、证据可追溯的完整报告。', data: '前述全部结构化结果与引用', actions: ['读执行摘要', '按目录跳转', '核对引用', '继续补强'], output: '长篇行业报告与监控指标。' },
    ],
    verify: ['报告长度不等于质量，先看证据覆盖和引用。', '龙头公司必须说明所处环节和选择依据。', '趋势判断要同时写出证伪条件和后续监控指标。'],
  },
  {
    id: 'concepts', category: '行业题材', eyebrow: '六源共识与个股归因', title: '概念题材查看', icon: Network,
    summary: '把六套题材目录、分层关系、成分股和市场表现组织成共识地图，并解释个股Beta与独特Alpha。',
    href: '/concept-themes', action: '进入概念题材查看', screenshot: '/landing/screens/concept-themes.jpg', alt: '概念题材查看实机页面截图',
    purpose: '分清市场共识主线、单源标签、题材共振和公司独立驱动，避免只凭题材名称做判断。',
    data: ['同花顺行业/概念', '东方财富板块与题材', '开盘啦、通达信', '申万行业层级', '机构语料与行情归因'],
    outputs: ['题材家族和产业链层级', '多源共识成分', '题材轮动与生命周期', '个股题材权重', 'Beta/Alpha拆解与证据'],
    hotspots: [
      { number: 1, x: 55, y: 22, title: '真实数据规模', detail: '显示源题材节点、成分关系、归因结果和最新交易日，用于判断库是否准备完成。' },
      { number: 2, x: 54, y: 30, title: '多维筛选', detail: '关键词、层级、来源、共识门槛、规模与市场热度可以组合。' },
      { number: 3, x: 53, y: 44, title: '多源题材轮动', detail: '轮动来自题材内成分表现和可用交易日，不用题材名称热度冒充涨跌。' },
      { number: 4, x: 57, y: 67, title: '跨题材股票雷达', detail: '识别同时属于多个共识题材、具有独立证据或相对强势的股票。' },
      { number: 5, x: 71, y: 91, title: '题材研究区', detail: '选择20/60/120日，查看归属矩阵、权重、Beta/Alpha并导出CSV。' },
    ],
    steps: ['先从题材家族/产业链进入，不直接搜索相近别名。', '用2源+/3源+/4源+共识门槛过滤单源噪声。', '查看题材轮动、生命周期和机构语料热度。', '打开题材核对六源成分矩阵和加权成分股。', '进入股票视角，区分题材Beta和公司独特Alpha证据。'],
    modules: [
      { name: '题材宇宙与分层', purpose: '浏览全部规范题材、家族和产业链。', data: '六源原始节点 + 语义归并', actions: ['按层级/来源筛选', '搜索题材或股票', '切换规范题材/源节点'], output: '可解释的题材目录。' },
      { name: '轮动与生命周期', purpose: '查看题材市场阶段和相对强弱。', data: '题材成分历史行情', actions: ['切换排序', '查看轮动变化', '核对交易日'], output: '题材阶段与强弱线索。' },
      { name: '六源共识矩阵', purpose: '确认哪些来源共同认可成分关系。', data: '独立来源成分关系', actions: ['查看来源矩阵', '调整共识门槛', '查看成员变动'], output: '成分共识度和来源证据。' },
      { name: 'Beta / Alpha 研究', purpose: '拆分题材共同表现与公司独立表现。', data: '成分留一题材组合 + 沪深300 + 公司证据', actions: ['切换20/60/120日', '看四象限', '下钻公司证据', '导出CSV'], output: '研究归因，不是交易承诺。' },
      { name: '题材对比与自选暴露', purpose: '比较两个题材并查看自选股题材集中度。', data: '题材关系、行情与个人自选', actions: ['加入对比', '查看重叠成分', '检查自选暴露'], output: '题材重叠和组合集中风险。' },
    ],
    verify: ['“共识”按独立来源计数，不能把同源别名当多源。', 'Beta基准需要留一处理，避免把个股自身重复算入题材。', 'Alpha必须同时说明统计残差和公司独特证据。'],
  },
  {
    id: 'chat', category: '公司研究', eyebrow: '带数据上下文的对话研究', title: '问股', icon: Bot,
    summary: '围绕一只股票调用本地事实、行情和可用接口，流式展示取数与分析过程，并保留多会话研究记录。',
    href: '/chat', action: '进入问股',
    purpose: '用自然语言提出公司问题，让模型先找数据再回答，并把追问保留在同一上下文。',
    data: ['本地公司事实与统一事件库', '行情、财务、资金与研报接口', '机构段子和公告原文索引', '用户当前会话与自选股'],
    outputs: ['带数据时间和来源的回答', '后续核验建议', '可复制/导出的Markdown记录', '可转入自选股的研究对象'],
    steps: ['输入股票名称/代码和明确问题，最好附时间范围。', '选择通用分析或缠论、波浪、趋势、箱体、情绪周期等技能。', '观察取数步骤，确认回答使用了哪些本地数据或实时接口。', '在同一会话继续追问，不要每次重开上下文。', '复制单条回答或导出整个会话，重要结论回到原文核验。'],
    modules: [
      { name: '多会话研究', purpose: '把不同股票/主题分开管理。', data: '按用户隔离的会话记录', actions: ['新建/切换/删除会话', '继续追问', '查看历史'], output: '连续、可恢复的研究上下文。' },
      { name: '数据工具调用', purpose: '本地没有时再调用可用API。', data: '本地库优先 + 按需实时接口', actions: ['查看取数进度', '核对数据日期', '继续补问缺口'], output: '证据支持的回答。' },
      { name: '分析技能', purpose: '按问题选择不同研究方法。', data: '行情/技术/情绪/基本面上下文', actions: ['选择一个或多个技能', '切回通用分析', '用追问验证'], output: '明确方法边界的分析。' },
      { name: '导出与协作', purpose: '把研究结果带出平台。', data: '当前消息或完整会话', actions: ['复制回答', '导出单条Markdown', '导出会话Markdown', '发送通知'], output: '可复查的研究记录。' },
    ],
    verify: ['问题必须包含对象、时间和想验证的假设。', '模型没有列出数据日期时要追问。', '技术分析只是描述价格状态，不能替代公告和财务事实。'],
  },
  {
    id: 'essays', category: '机构语料', eyebrow: '知识星球增量库', title: '机构段子与录音', icon: Mic2,
    summary: '从今日研判到跨期洞察、全文检索、录音转写、趋势跟踪、每日报告和数据管理，完整利用非结构化语料。',
    href: '/essay-radar', action: '进入机构段子与录音', screenshot: '/landing/screens/essay-radar.jpg', alt: '机构段子洞察图谱实机页面截图',
    purpose: '把原始段子、附件和录音变成可检索、可引用、可验证、可回测的研究材料。',
    data: ['知识星球 MCP 增量正文', '图片/文件/录音链接', 'DeepSeek结构化分析', '阿里云录音转写', '个股与指数行情'],
    outputs: ['原文与结构化标签', '短中长期洞察', '语料与行情关联统计', '录音纪要和时间戳转写', '每日长篇报告'],
    hotspots: [
      { number: 1, x: 53, y: 20, title: '六个工作子页', detail: '今日研判、洞察图谱、检索与获取、趋势追踪、每日报告、数据管理各有独立用途。' },
      { number: 2, x: 54, y: 28, title: '研究时间窗', detail: '短期/中期/长期/自定义日期改变整个页面的数据窗口。' },
      { number: 3, x: 52, y: 37, title: '覆盖与质量', detail: '已分析语料、行情覆盖、已验证股票和多空分歧用于判断结论能否使用。' },
      { number: 4, x: 51, y: 56, title: '语料与行情关联', detail: '把真实日线和提及量放在同一时间轴，查看事件后1/5/10/20日表现和样本量。' },
    ],
    steps: ['今日先看“今日研判”和模型日报，获取新增线索。', '需要查历史时进入“检索与获取”，选择标题或全文搜索。', '勾选小作文导出Excel/原文，或勾选录音提交转写与AI纪要。', '用洞察图谱和趋势追踪检查主题、个股与行情的关系。', '到数据管理查看入库时间范围、分析覆盖和失败重试。'],
    modules: [
      { name: '今日研判', route: '/essay-radar', purpose: '优先处理新增且重要的机构语料。', data: '最近新增原文 + 自动分析结果', actions: ['看昨日新增', '看自选股信号', '打开原文', '核验高优先级线索'], output: '当日机构语料研究清单。' },
      { name: '洞察图谱', route: '/essay-radar/insights', purpose: '在不同时间尺度观察主题、个股和行情关系。', data: '结构化语料 + 实际日线', actions: ['切短/中/长期', '自定义日期', '看主题迁移', '看事件后收益与置信区间'], output: '语料—行情关联证据。' },
      { name: '检索与获取', route: '/essay-radar/feed', purpose: '查全库正文、附件与录音。', data: 'SQLite全文索引 + 媒体链接', actions: ['标题/全文检索', '按日期/情绪/分类/重要性筛选', '页面内看原文', '批量导出/下载'], output: '筛选后的原文、Excel或文件包。' },
      { name: '录音AI纪要', route: '/essay-radar/feed', purpose: '把选择的公司录音转成可读研究纪要。', data: '源音频 + 阿里云ASR + DeepSeek', actions: ['填写重点问题/热词/人数', '提交后台任务', '查看时间戳转写', '下载Word/MD/JSON/ZIP'], output: '带证据索引的录音小作文。' },
      { name: '趋势追踪', route: '/essay-radar/trends', purpose: '观察股票、标签和主题的日/周/月提及变化。', data: '全库提及与重要性', actions: ['切日/周/月', '看词云/提及云', '查看自选股趋势', '看30日高频股票'], output: '讨论动量与首次提及线索。' },
      { name: '每日报告', route: '/essay-radar/reports', purpose: '让各模型总结前一日新增语料。', data: '前一日新增且已分析语料', actions: ['切换日期和模型', '比较共识/分歧', '阅读长文', '查看候选股票和次日核验项'], output: '可追溯的机构语料日报。' },
      { name: '数据管理', route: '/essay-radar/system', purpose: '查看增量同步和AI分析覆盖。', data: 'MCP同步状态 + AI任务队列', actions: ['查看总量/日期范围', '看已分析/未分析/失败', '选择历史未分析数量', '重试失败'], output: '知识库与分析管线状态。' },
    ],
    verify: ['原文、AI标签和录音转写必须分层显示。', '事件后收益要看样本数、到期样本和置信区间。', '文件名搜索不能连带返回同帖内无关录音。', 'AI空摘要或失败任务应进入自动重试，而不是伪装成功。'],
  },
  {
    id: 'quant', category: '数据量化', eyebrow: '按用户隔离的后台研究任务', title: '量化回测与数据利用', icon: FlaskConical,
    summary: '从研究假设出发组合语料、行情、基本面和资金因子，后台执行并保存可复现结果、成本与稳健性。',
    href: '/essay-quant', action: '进入量化工作台', screenshot: '/landing/screens/quant-workbench.jpg', alt: '量化研究任务中心实机页面截图',
    purpose: '验证“某类信息是否有统计价值”，而不是展示一个脱离样本、成本和基准的收益数字。',
    data: ['机构段子事件与标签', '公告、研报、新闻事件', '个股/行业/指数行情', '财务、资金、技术因子', '任务数据快照'],
    outputs: ['事件窗和组合净值', '超额收益、回撤与成本', '95%置信区间', '分组/样本外/敏感性检验', '可复现任务档案'],
    hotspots: [
      { number: 1, x: 54, y: 15, title: '六个量化子页', detail: '任务中心、新建任务、AI建任务、结果研判、数据与方法、任务档案。' },
      { number: 2, x: 42, y: 22, title: '两个建任务入口', detail: '模板任务适合明确规则；自然语言入口先由模型填入受控参数，再交给服务器模板执行。' },
      { number: 3, x: 53, y: 34, title: '五段研究管线', detail: '语料与事实源、因子构建、样本与约束、执行、稳健性与归因。' },
      { number: 4, x: 53, y: 50, title: '运行中任务', detail: '后台执行，离开页面不会中断；这里只展示当前用户任务。' },
      { number: 5, x: 55, y: 82, title: '研究模板', detail: '事件研究、多因子、情报趋势共振、机构胜率、信号组合等具有不同输入与输出。' },
    ],
    steps: ['把观点写成可以验证的条件：信号、对象、入场、持有期、基准。', '选择模板或用自然语言生成受控任务参数。', '设置去重、首次提及、成本、组合规模和样本外检验。', '提交后台任务后切换页面，完成后回任务中心查看。', '先读样本与方法，再读净值、超额、回撤、置信区间和稳健性。'],
    modules: [
      { name: '任务中心', purpose: '统一查看排队、运行、完成和失败任务。', data: '按用户隔离的后台任务', actions: ['筛选状态', '查看阶段进度', '打开完成结果', '重试失败'], output: '可离页运行的任务队列。' },
      { name: '新建任务', purpose: '用可控表单构建研究。', data: '事件/因子/行情/财务数据目录', actions: ['选模板', '设样本/信号/持有期', '设成本与基准', '保存并运行'], output: '参数明确的量化任务。' },
      { name: 'AI建任务', purpose: '把自然语言假设翻译成白名单参数。', data: 'DeepSeek + 服务器端安全模板', actions: ['描述假设', '审阅生成参数', '修改约束', '确认执行'], output: '受控任务，不直接执行任意代码。' },
      { name: '结果研判', purpose: '解释结果能否用于后续研究。', data: '任务结果快照', actions: ['看净值/超额/回撤', '看事件窗', '看月度/分组', '看成本/CI/稳健性'], output: '结果、局限和停止采用条件。' },
      { name: '数据与方法', purpose: '知道每个指标由什么数据计算。', data: '数据资产目录与方法定义', actions: ['查看覆盖日期', '查看字段定义', '检查缺失与截止时间'], output: '可审计的方法说明。' },
      { name: '任务档案', purpose: '复现和比较历史实验。', data: '规则、数据哈希、截止时间和结果快照', actions: ['筛选个人任务', '重开历史结果', '比较参数'], output: '可复现研究账本。' },
    ],
    verify: ['样本数必须是去重后的研究事件，不是统一事件库全部记录。', '结果必须显示基准、成本、到期样本和数据截止日。', '机构预计算属于后台资产，不应混入普通用户任务。', '自然语言入口不能执行任意shell、SQL、文件或交易指令。'],
  },
  {
    id: 'monitor', category: '每日使用', eyebrow: '全部渠道的可用性与原始消息', title: '投资情报台', icon: Radar,
    summary: '把渠道总览、数据源BI、实时流水和龙虎榜拆成四页，既看消息，也看数据是否真的在更新。',
    href: '/investment-monitor', action: '进入投资情报台', screenshot: '/landing/screens/investment-monitor.jpg', alt: '投资情报台全渠道情报实机页面截图',
    purpose: '知道平台拥有什么渠道、每个渠道新不新、最新进了什么，以及如何回到原始信息。',
    data: ['公告、研报、新闻、财务和资金', '机构段子、企业事实与公开股评', '龙虎榜top_list/top_inst', '各采集器同步状态与统一事件库'],
    outputs: ['渠道库存与新鲜度', '原始消息流水', '数据源吞吐与失败信息', '每日/历史龙虎榜和席位'],
    hotspots: [
      { number: 1, x: 52, y: 15, title: '四个情报子页', detail: '全渠道情报看渠道；数据源BI看健康；实时流水查消息；龙虎榜看席位。' },
      { number: 2, x: 52, y: 22, title: '健康总览', detail: '渠道总数、真正有数据、实时巡检、延迟/失败和最后调度都来自真实状态。' },
      { number: 3, x: 55, y: 37, title: '按性质分组', detail: '官方披露、授权数据、研究与新闻、另类情报分栏，不把股评和公告混为一谈。' },
      { number: 4, x: 70, y: 53, title: '单渠道状态', detail: '每张卡显示存量、最近检查、最新事实、上轮收到与去重新增。' },
    ],
    steps: ['先在全渠道情报查看各渠道最新事实时间和同步状态。', '选择渠道，只看该来源的原始消息。', '进入数据源BI核对库存、30日量、最近检查、失败和底层接口。', '进入实时流水按来源、股票和关键词查消息并页面内看原文。', '需要席位研究时进入龙虎榜，切换每日或历史模式。'],
    modules: [
      { name: '全渠道情报', route: '/investment-monitor', purpose: '按渠道性质查看存量和最新消息。', data: '统一事件库 + 采集器状态', actions: ['选择渠道', '搜索当前渠道', '同步当前渠道', '查看原始消息'], output: '渠道级情报清单。' },
      { name: '数据源 BI', route: '/investment-monitor/bi', purpose: '回答“有什么、怎么样、怎么调用”。', data: '来源存量、事件趋势、同步日志', actions: ['按可用/需处理筛选', '搜索接口/提供方', '查看收到/新增/更新', '进入事件API'], output: '数据资产和健康审计。' },
      { name: '实时流水', route: '/investment-monitor/feed', purpose: '按入库时间检索原始消息。', data: '统一事件库最近消息', actions: ['筛选来源', '输入股票代码', '关键词检索', '在抽屉看原文'], output: '跨渠道原始消息流水。' },
      { name: '龙虎榜', route: '/investment-monitor/dragon-tiger', purpose: '查看每日上榜原因和营业部席位。', data: 'Tushare top_list/top_inst + 本地历史', actions: ['切换交易日', '直接刷新接口', '展开席位', '按日期/股票查历史', '看净额趋势'], output: '每日及历史龙虎榜研究。' },
    ],
    verify: ['“上轮收到”与“去重新增”不能混淆。', '最新事实时间和采集器检查时间不是同一概念。', '公开股评属于投资者观点，默认待核验。', '龙虎榜金额口径和单位必须与接口原始字段一致。'],
  },
  {
    id: 'watchlist', category: '公司研究', eyebrow: '一只股票的统一事实与行情', title: '自选股超级看板', icon: Star,
    summary: '新增股票后自动补齐历史资料，在一个页面共享行情、财务、资金、公告、研报、机构段子、预期、消息和股评。',
    href: '/super-watchlist', action: '进入自选股超级看板', screenshot: '/landing/screens/super-watchlist.jpg', alt: '自选股超级看板实机页面截图',
    purpose: '围绕个人关注股票建立持续更新的公司档案，所有子栏目使用同一个股票代码和共享数据库。',
    data: ['实时快照、分钟线与历史K线', 'Tushare财务/资金/研报', '巨潮公告', '知识星球语料', '天眼查与公开股评'],
    outputs: ['实时行情与图表', '事实时间线', '基本面/估值/筹码快照', '一致预期工作台', '全渠道公司证据'],
    hotspots: [
      { number: 1, x: 24, y: 23, title: '加入与管理自选', detail: '支持中文名称、拼音简称和股票代码；加入后自动补齐资料，可随时删除。' },
      { number: 2, x: 49, y: 22, title: '统一实时报价', detail: '大价格、左侧列表、行情统计和分时图必须来自同一最新快照。' },
      { number: 3, x: 80, y: 22, title: '深度研判', detail: '把当前股票的事实、预期、行情和风险送入研究分析，不改变底层数据。' },
      { number: 4, x: 62, y: 39, title: '分时与K线', detail: '分时/日K/周K/月K/年K、当日/5日都读取共享行情库。' },
      { number: 5, x: 63, y: 68, title: '昨收零轴', detail: '分时曲线以昨收为0%，鼠标悬浮值必须与图上时间点一致。' },
    ],
    steps: ['输入中文名称、拼音或代码，从候选中确认加入。', '先核对大价格、数据时间、来源和分时图是否一致。', '切换全景/财务/资金/公告研报/小作文等九个标签。', '点击事实时间线的原文，在页面内查看或跳转官方原文。', '用一致预期比较券商预测、机构段子预测和实际财务期。'],
    modules: [
      { name: '行情与图表', purpose: '统一显示当前股票实时快照和多周期行情。', data: '共享行情库', actions: ['切分时/K线', '切当日/5日', '悬浮读点', '核对昨收零轴'], output: '价格、涨跌、成交量与行情时间。' },
      { name: '全景', purpose: '快速阅读事实变化和基本面快照。', data: '跨渠道公司事实', actions: ['看事实驱动观察', '读时间线', '打开原文'], output: '公司事实摘要。' },
      { name: '财务估值', purpose: '查看收入、利润、现金流和估值口径。', data: 'Tushare财务指标与估值', actions: ['核对报告期', '比较同比', '查看PE/PB/ROE/利润率'], output: '基本面与估值快照。' },
      { name: '资金筹码', purpose: '观察成本、获利盘和技术状态。', data: '筹码、资金与技术指标', actions: ['看获利比例/成本', '看RSI/MACD/布林/CCI', '核对数据日'], output: '资金与价格状态。' },
      { name: '公告研报', purpose: '集中查看官方公告与券商研究。', data: '巨潮公告 + 研报库', actions: ['按时间阅读', '打开PDF/原文', '区分公告与观点'], output: '官方事实和研究观点。' },
      { name: '小作文', purpose: '按全称、简称和代码匹配相关机构语料。', data: '知识星球全文索引', actions: ['查看匹配原因', '页面内看原文', '查看附件'], output: '公司相关非结构化语料。' },
      { name: '一致预期', purpose: '合并券商预测和机构段子中的业绩/市值预期。', data: '最近20篇相关 + 5篇单股专属语料 + 券商预测', actions: ['核对预测日期/目标期', '查看引用', '重新分析'], output: '带时间轴的预期对比。' },
      { name: '消息渠道', purpose: '查看新闻、小作文和企业新提示。', data: '新闻 + 知识星球 + 天眼查', actions: ['按渠道筛选', '打开详情'], output: '公司消息流。' },
      { name: '股评监控', purpose: '查看公开投资者讨论。', data: '东方财富股吧公开内容', actions: ['查看原文', '观察观点分歧', '不按热度当事实'], output: '市场讨论线索。' },
      { name: '全部证据', purpose: '完整查看该股票统一事件库记录。', data: '全部已关联事件', actions: ['筛选来源', '按时间查看', '打开证据'], output: '公司证据总账。' },
    ],
    verify: ['大价格、列表价格、分时最新价和涨跌幅必须一致。', '新增自选股应触发历史回填，页面显示真实进度。', '小作文按公司简称也应匹配，但必须展示匹配依据。', '股评和机构观点不能标成公司事实。'],
  },
  {
    id: 'download', category: '数据量化', eyebrow: '筛选、确认、后台打包', title: '数据一站式获取', icon: Download,
    summary: '研报先查两年本地链接库，其他跨渠道需求按来源拆分执行；只导出用户筛选并确认的数据。',
    href: '/data-acquisition', action: '进入数据一站式获取', screenshot: '/landing/screens/data-acquisition.jpg', alt: '数据一站式获取实机页面截图',
    purpose: '把“我要什么数据”变成明确筛选条件、来源任务和可下载交付包，防止误导出全量无关内容。',
    data: ['两年Tushare研报元数据/PDF URL', '知识星球正文/文件/录音', '巨潮公告', 'Tushare各类接口', '天眼查与统一事件库'],
    outputs: ['人工筛选研报Excel/PDF链接', '按条件筛选的原文和附件包', '跨渠道分源清单', '真实字节进度与历史数据包'],
    hotspots: [
      { number: 1, x: 53, y: 13, title: '任务总览', detail: '显示可用渠道、规划模型、当前任务和历史数据包，不把后台预加载当用户任务。' },
      { number: 2, x: 49, y: 32, title: '本地研报库状态', detail: '查看两年研报总量、PDF链接数、日期范围和同步进度。' },
      { number: 3, x: 52, y: 55, title: '十二类筛选条件', detail: '标题、摘要、券商、公司、类型、行业、标签、作者、代码、日期、排序、PDF状态。' },
      { number: 4, x: 78, y: 78, title: '点击搜索', detail: '条件仅在点击后生效；搜索只查本地SQLite，不临时扫接口。' },
      { number: 5, x: 80, y: 91, title: '人工确认与导出', detail: '勾选真正需要的研报，再导出清单和PDF链接。' },
    ],
    steps: ['先判断是研报、机构段子、录音、公告还是跨渠道数据。', '研报使用本地筛选台，按标题/摘要/券商/公司/行业/日期等组合。', '点击搜索后逐条检查日期、类型、来源、摘要和PDF状态。', '勾选确认结果；文件需求要明确包含原文或PDF。', '提交后台打包，观察检索、下载、导出、压缩的真实进度。'],
    modules: [
      { name: '两年研报链接库', purpose: '像终端一样细筛研报，不用AI猜测主题。', data: 'Tushare研报元数据、摘要和PDF URL', actions: ['标题/摘要关键词', '券商/公司/类型/行业/标签/作者/代码', '日期与排序', '只看PDF', '勾选导出'], output: '人工确认的研报Excel和PDF链接。' },
      { name: '自然语言取数规划', purpose: '把复杂需求拆成每个来源的独立任务。', data: '已接入渠道能力目录 + DeepSeek规划', actions: ['描述范围/字段/格式', '审阅分源计划', '修改条件', '确认执行'], output: '可审计的来源级取数计划。' },
      { name: '来源独立执行', purpose: '每个渠道只获取符合条件的数据。', data: 'Tushare/巨潮/知识星球/天眼查/情报库', actions: ['查看各来源命中数', '单独失败/重试', '保留来源字段'], output: '分来源结果，不是全库倾倒。' },
      { name: '后台打包与历史包', purpose: '大文件下载和压缩不阻塞页面。', data: '选中结果与远端附件', actions: ['看阶段进度', '看真实字节进度', '离开页面', '从历史下载'], output: '可恢复的ZIP/Excel/JSON/CSV包。' },
    ],
    verify: ['搜索条件必须真正传入各渠道，不能退化成最近N条。', '摘要检索只代表接口摘要字段，不能冒充PDF全文。', '没有勾选的数据不应进入打包。', '下载进度必须来自已处理文件/字节，而不是前端动画。'],
  },
  {
    id: 'screening', category: '数据量化', eyebrow: '可选 AlphaSift 能力', title: 'AlphaSift 选股', icon: Filter,
    summary: '在功能启用时，用热点、策略和多因子条件生成A股候选，并展示因子、风险和上游降级信息。',
    href: '/screening', action: '进入 AlphaSift 选股',
    purpose: '把结构化筛选条件变成可审阅的候选列表，结果用于建立研究清单而不是自动交易。',
    data: ['A股行情快照', '热点题材', '策略因子', '平台公司与事件上下文', '可选模型重排'],
    outputs: ['候选股票与综合分', '因子拆解', '筛选理由和风险', '数据源降级/缺失说明'],
    steps: ['确认页面已显示A股市场和可用数据源。', '选择热点或策略模板，设置候选数量和条件。', '启动后台筛选任务并等待真实进度。', '打开候选查看因子得分、理由、风险和原始字段。', '把有研究价值的候选加入自选股继续核验。'],
    modules: [
      { name: '热点与策略', purpose: '定义候选池和筛选方法。', data: '热点题材与策略目录', actions: ['选择热点', '选择策略', '设置条件'], output: '明确的筛选口径。' },
      { name: '后台筛选任务', purpose: '避免长计算阻塞页面。', data: '快照和因子', actions: ['启动任务', '查看进度', '恢复会话任务'], output: '按用户会话保存的候选结果。' },
      { name: '候选诊断', purpose: '解释候选为何入选以及哪里不可靠。', data: '因子分、模型摘要、上游错误', actions: ['看分数/信号', '看前六因子', '看风险和降级'], output: '可审阅候选研究单。' },
    ],
    verify: ['上游失败时要显示降级来源，不能伪装完整结果。', '模型重排失败应回退本地因子并明确提示。', '候选理由必须能对应到因子或真实数据。'],
  },
  {
    id: 'admin', category: '系统管理', eyebrow: '仅管理员可见', title: '管理员后台', icon: KeyRound,
    summary: '集中管理注册审批、访问权限、数据源、同步任务、模型/API、用量和运行设置；普通用户不会看到密钥。',
    href: '/admin', action: '进入管理员后台',
    purpose: '把用户侧研究界面与高权限配置彻底分开，同时提供数据和服务的运行诊断。',
    data: ['用户与审批记录', '服务健康与采集器状态', '模型配置（脱敏）', '任务调度与用量统计'],
    outputs: ['用户批准/禁用结果', '数据源诊断', '同步调度状态', '模型用量和系统设置'],
    steps: ['使用管理员账号进入后台，先看运行摘要和健康探针。', '在访问管理审批注册申请并检查账号状态。', '在数据源和同步页处理失败/过期来源。', '在模型/API页配置服务；界面只显示脱敏值。', '修改调度或系统设置后，重新验证健康接口和前台关键页。'],
    modules: [
      { name: '后台总览', route: '/admin', purpose: '查看系统摘要和待处理事项。', data: '运行状态、注册申请、健康探针', actions: ['看待审批', '看访问矩阵', '看服务健康'], output: '管理员行动清单。' },
      { name: '数据源', route: '/admin/data-sources', purpose: '逐个诊断上游渠道。', data: '来源探针、最近成功与错误', actions: ['查看状态', '运行探针', '定位过期/失败'], output: '数据源可用性报告。' },
      { name: '同步与预计算', route: '/admin/sync', purpose: '管理采集调度和后台预计算。', data: '任务调度、游标和队列', actions: ['查看任务', '唤醒到期同步', '检查预计算'], output: '同步运行状态。' },
      { name: '访问管理', route: '/admin/access', purpose: '审批用户并管理账号访问。', data: '注册申请、账号和会话', actions: ['批准/拒绝', '禁用/恢复', '查看会话'], output: '生效的用户权限。' },
      { name: '模型与API', route: '/admin/api-models', purpose: '管理AI与外部接口配置。', data: '脱敏配置和探针结果', actions: ['设置模型', '测试连接', '检查并发与超时'], output: '可用的模型服务。' },
      { name: '用量', route: '/admin/usage', purpose: '查看模型、令牌和任务消耗。', data: '用量日志', actions: ['按用户/模型查看', '检查异常峰值'], output: '资源消耗审计。' },
      { name: '系统设置', route: '/admin/settings', purpose: '调整运行环境和定时任务。', data: '系统配置（敏感值脱敏）', actions: ['检查环境', '调整调度', '保存后验证'], output: '受控系统配置。' },
    ],
    verify: ['普通用户不能通过URL直接进入管理员页面。', '前端和接口都要校验权限，不能只隐藏导航。', '密钥、令牌和完整连接串不能出现在手册、日志或前端响应。', '任何高风险操作前先确认目标和可恢复性。'],
  },
];

const sourceRows = [
  ['Tushare Pro', '指数、个股行情、财务、资金、研报、新闻、龙虎榜', '交易日、接口字段、复权/单位、数据截止日'],
  ['腾讯 / 新浪行情', '盘中快照、分钟线、市场广度与行业快照', '最新时间、昨收零轴、代码映射、盘中/收盘口径'],
  ['巨潮资讯', '上市公司公告与PDF原文', '证券代码、公告发布时间、标题和官方原文'],
  ['知识星球 MCP', '机构段子、图片、文件和录音链接', '原文创建时间、星球来源、附件链接、增量游标'],
  ['阿里云语音识别', '录音转写与时间戳', '专业热词、说话人数、转写置信与源音频'],
  ['DeepSeek', '结构化标签、日报、录音纪要与研究规划', '引用、空响应/截断、模型与分析时间'],
  ['天眼查', '工商、风险、知识产权等企业事实', '企业主体、事实日期、接口返回字段'],
  ['公开股评', '东方财富股吧公开讨论', '观点属性、作者、发布时间和原文；不能当事实'],
  ['六源题材库', '同花顺、东方财富、开盘啦、通达信、申万等题材关系', '独立来源数、扫描覆盖率、成分关系和交易日'],
  ['本地 SQLite', '跨页面共享行情、事件、语料和任务索引', '最近同步时间、数据范围、去重键和来源字段'],
];
const categories: Category[] = ['全部', '每日使用', '公司研究', '行业题材', '机构语料', '数据量化', '系统管理'];
const quickTasks = [
  { title: '每天先判断市场', detail: '交易日 → 指数 → 广度 → 行业 → 自选', href: '#market', icon: LineChart },
  { title: '搞明白一家公司', detail: '自选股 → 事实 → 预期 → 原文 → 反证', href: '#watchlist', icon: Building2 },
  { title: '快速研究一个行业', detail: '定义边界 → 产业链 → 龙头 → 痛点 → 报告', href: '#industry', icon: Landmark },
  { title: '判断主线与个股归因', detail: '题材共识 → 成分矩阵 → Beta → Alpha', href: '#concepts', icon: Network },
  { title: '查机构段子或录音', detail: '标题/全文 → 筛选 → 原文/转写 → 导出', href: '#essays', icon: Mic2 },
  { title: '持续监控全渠道消息', detail: '渠道健康 → 实时流水 → 原文 → 龙虎榜', href: '#monitor', icon: Radar },
  { title: '验证一个量化假设', detail: '信号 → 样本 → 成本 → 回测 → 稳健性', href: '#quant', icon: FlaskConical },
  { title: '筛选并下载资料', detail: '细筛 → 人工确认 → 后台打包 → 下载', href: '#download', icon: FileSearch },
];
const workflowRows = [
  { title: '公司首次建档', pages: '自选股超级看板 → 研究决策台 → 问股', process: '加入股票并补齐半年资料；读事实与财务；再带上下文追问。', finish: '得到公司证据包、缺口和持续跟踪入口。' },
  { title: '重大事件核验', pages: '投资情报台 → 自选股 → 数据一站式获取', process: '发现公告/新闻；对照行情与预期；下载官方原文和相关研报。', finish: '形成事件时间线、市场反应和原始证据包。' },
  { title: '行业快速研究', pages: '行业调研 → 概念题材 → 研报库 → 自选股', process: '先建立产业链，再核对题材共识和公司成分，最后进入公司证据。', finish: '形成行业地图、公司候选和验证问题。' },
  { title: '机构观点验证', pages: '机构段子 → 自选股一致预期 → 量化回测', process: '检索原文与预测，按目标期整理，再做事件后走势与样本外检验。', finish: '区分可验证观点、统计关系和无效信号。' },
  { title: '批量资料交付', pages: '数据一站式获取 → 后台任务 → 历史包', process: '细化条件、人工勾选、后台下载压缩，完成后从历史任务获取。', finish: '只包含确认结果及来源/PDF链接的交付包。' },
];
const faqs = [
  ['为什么两个页面价格不一样？', '先比较股票代码、交易日、时间戳和数据源。自选列表、大价格、分时最新点应读取同一共享快照；如果仍不一致，应以最新时间的来源为准并反馈具体页面。'],
  ['为什么休市日显示上一交易日？', '行情事实只能来自实际交易日。页面应写清“某日收盘”，市场广度和行业分布也使用最近可用交易日，不能用空白或把旧数据写成今日。'],
  ['为什么搜索结果为空或不相关？', '确认选择的是标题检索还是全文检索，放宽日期/分类等条件后重新点击搜索。录音按文件名严格召回；研报条件只在点击搜索后生效。'],
  ['后台任务可以离开页面吗？', '可以。行业调研、量化、录音纪要和文件打包都在服务器后台运行。返回对应任务中心即可查看阶段、进度、完成结果或重试失败任务。'],
  ['AI为什么显示空摘要或分析失败？', '模型可能返回空内容、推理字段但正文为空、超时或截断。任务应进入失败/重试而不是伪装完成；请在任务账本重试并查看原始转写或原文。'],
  ['如何确认AI没有编造？', '沿引用打开公告、研报、原始段子或转写时间戳。没有原文支持的内容只能标记为模型推断或待核验，不能写成事实。'],
  ['数据源BI里的“收到”和“新增”有什么区别？', '“收到”是上游本轮返回条数；“新增”是去重后首次进入本地库的条数；“更新”是已有记录发生结构化更新。三者不能相加当作新情报。'],
  ['结果能直接作为买卖建议吗？', '不能。平台帮助整理事实、观点、预期和统计验证；上游数据可能延迟或缺失，AI会误判，回测也可能过拟合。最终决策和风险承担仍由用户独立完成。'],
];

const searchableText = (chapter: GuideChapter) => [
  chapter.title, chapter.summary, chapter.purpose, chapter.category,
  ...chapter.data, ...chapter.outputs, ...chapter.steps, ...chapter.verify,
  ...chapter.modules.flatMap((module) => [module.name, module.purpose, module.data, module.output, ...module.actions]),
].join(' ').toLowerCase();

const AnnotatedScreenshot = ({ chapter, onOpen }: { chapter: GuideChapter; onOpen: () => void }) => {
  if (!chapter.screenshot || !chapter.alt) return null;
  return <div className="guide-anatomy">
    <button className="guide-shot" type="button" onClick={onOpen} aria-label={`放大查看${chapter.title}截图`}>
      <img src={chapter.screenshot} alt={chapter.alt} loading="lazy" />
      {chapter.hotspots?.map((hotspot) => <span className="guide-hotspot" style={{ left: `${hotspot.x}%`, top: `${hotspot.y}%` }} key={hotspot.number}>{hotspot.number}</span>)}
      <span className="guide-shot-action"><Expand /> 放大并查看标注</span>
    </button>
    <ol className="guide-hotspot-list" aria-label={`${chapter.title}截图标注说明`}>
      {chapter.hotspots?.map((hotspot) => <li key={hotspot.number}><b>{hotspot.number}</b><div><strong>{hotspot.title}</strong><p>{hotspot.detail}</p></div></li>)}
    </ol>
  </div>;
};

const UserGuidePage = () => {
  const [lightbox, setLightbox] = useState<GuideChapter | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<Category>('全部');
  useEffect(() => { document.title = '完整使用手册 - 乐子乌超级价值'; }, []);
  useEffect(() => {
    if (!lightbox) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setLightbox(null); };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => { document.body.style.overflow = ''; window.removeEventListener('keydown', closeOnEscape); };
  }, [lightbox]);
  const normalizedQuery = query.trim().toLowerCase();
  const visibleChapters = useMemo(() => chapters.filter((chapter) =>
    (category === '全部' || chapter.category === category)
    && (!normalizedQuery || searchableText(chapter).includes(normalizedQuery))), [category, normalizedQuery]);
  const moduleCount = chapters.reduce((total, chapter) => total + chapter.modules.length, 0);
  const screenshotCount = chapters.filter((chapter) => chapter.screenshot).length;

  return <main className="guide-page">
    <header className="guide-header">
      <Link className="guide-brand" to="/"><span><DatabaseZap /></span><b>乐子乌超级价值</b><small>USER MANUAL</small></Link>
      <nav aria-label="手册顶部导航"><Link to="/"><ArrowLeft /> 产品介绍</Link><Link className="is-primary" to="/app">进入平台 <ArrowRight /></Link></nav>
    </header>

    <section className="guide-hero">
      <div className="guide-hero-copy">
        <span className="guide-kicker"><BookOpen /> COMPLETE OPERATIONS MANUAL · 2026-08-31</span>
        <h1>不是功能清单。<br /><em>是一套能照着完成研究的操作系统。</em></h1>
        <p>覆盖普通用户全部入口、子页面、后台任务和管理员功能。每个模块都写清目的、使用数据、具体操作、输出结果与核验标准；核心页面使用真实截图和编号标注。</p>
        <div className="guide-hero-actions"><a href="#find"><Search /> 搜索功能</a><a href="#workspaces">阅读完整手册 <ArrowRight /></a></div>
      </div>
      <div className="guide-hero-ledger" aria-label="手册覆盖统计">
        <span>MANUAL COVERAGE</span>
        <dl><div><dt>功能工作台</dt><dd>{chapters.length}</dd></div><div><dt>子模块</dt><dd>{moduleCount}</dd></div><div><dt>真实截图</dt><dd>{screenshotCount}</dd></div><div><dt>跨页流程</dt><dd>{workflowRows.length}</dd></div></dl>
        <p><CheckCircle2 /> 内容按当前生产功能编写；界面数值会随数据自动更新。</p>
      </div>
    </section>

    <section className="guide-finder" id="find" aria-labelledby="guide-finder-title">
      <div className="guide-finder-copy"><small>FIND A FUNCTION</small><h2 id="guide-finder-title">按你想完成的事情查手册</h2><p>输入“录音、龙虎榜、一致预期、研报PDF、Beta、后台任务”等任意功能词。</p></div>
      <label className="guide-search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索功能、数据、操作或输出…" aria-label="搜索使用手册" />{query ? <button type="button" onClick={() => setQuery('')} aria-label="清除手册搜索"><X /></button> : null}</label>
      <div className="guide-categories" aria-label="手册分类">{categories.map((item) => <button type="button" key={item} className={category === item ? 'is-active' : ''} onClick={() => setCategory(item)}>{item}</button>)}</div>
      <p className="guide-match" aria-live="polite">当前显示 {visibleChapters.length} / {chapters.length} 个工作台</p>
      <div className="guide-quick-grid">{quickTasks.map(({ title, detail, href, icon: Icon }) => <a href={href} key={title}><Icon /><span><strong>{title}</strong><small>{detail}</small></span><ChevronRight /></a>)}</div>
    </section>

    <div className="guide-layout">
      <aside className="guide-toc" aria-label="完整使用手册目录">
        <span>CONTENTS</span>
        <a href="#start">01 · 注册与权限</a><a href="#read-data">02 · 数据阅读规则</a><a href="#workspaces">03 · 全部工作台</a>
        {chapters.map((chapter) => <a className="is-child" href={`#${chapter.id}`} key={chapter.id}>{chapter.title}</a>)}
        <a href="#workflow">04 · 跨页面流程</a><a href="#sources">05 · 数据源字典</a><a href="#faq">06 · 故障与核验</a>
      </aside>

      <article className="guide-content">
        <section className="guide-section" id="start">
          <div className="guide-section-title"><span>01</span><div><small>ACCESS & IDENTITY</small><h2>注册、审批、登录与个人数据</h2></div></div>
          <div className="guide-start-grid">
            <article><b>01</b><UserCheck /><h3>提交注册</h3><p>在产品介绍页右上角点击“注册”，填写姓名和密码。姓名作为登录账号；不要重复注册同名账号。</p></article>
            <article><b>02</b><ShieldCheck /><h3>管理员审批</h3><p>申请进入后台待审批队列。批准前不能访问应用页面和内部接口；管理员可拒绝、禁用或恢复账号。</p></article>
            <article><b>03</b><LockKeyhole /><h3>登录与隔离</h3><p>登录后市场事实数据共享；自选股、问股会话、行业研究、量化任务和下载任务按用户保存。</p></article>
          </div>
          <div className="guide-rule"><LockKeyhole /><div><strong>权限边界</strong><p>产品介绍页、注册/登录页和本手册可公开访问；应用页面和数据接口需要有效会话；管理员页面同时在前端路由和后端接口校验权限。</p></div></div>
        </section>

        <section className="guide-section" id="read-data">
          <div className="guide-section-title"><span>02</span><div><small>READ BEFORE RESEARCH</small><h2>任何数字和结论先过这五关</h2></div></div>
          <div className="guide-five-checks">
            {[
              ['01', '对象', '股票代码、指数代码、公司主体是否正确。'], ['02', '时间', '交易日、发生日、发布时间、入库时间是否混淆。'],
              ['03', '来源', '官方披露、授权数据、机构观点、公开讨论分别是什么。'], ['04', '口径', '昨收零轴、复权、单位、同比/环比、样本去重如何定义。'],
              ['05', '证据', '能否打开原文；AI结论是否有引用、样本和失效条件。'],
            ].map(([n, title, text]) => <article key={n}><b>{n}</b><h3>{title}</h3><p>{text}</p></article>)}
          </div>
          <p className="guide-principle">行情看 <b>交易日与昨收零轴</b>；事实看 <b>官方原文</b>；观点看 <b>来源与目标期</b>；统计看 <b>样本、基准、成本和置信区间</b>；AI看 <b>引用和可证伪条件</b>。</p>
        </section>

        <section className="guide-section guide-workspaces" id="workspaces">
          <div className="guide-section-title"><span>03</span><div><small>ALL PRODUCT WORKSPACES</small><h2>每个功能到底怎么用</h2></div></div>
          {!visibleChapters.length ? <div className="guide-no-match"><Search /><h3>没有匹配的功能</h3><p>尝试搜索“行情、录音、研报、公告、任务、管理员”等更短关键词，或切回“全部”。</p><button type="button" onClick={() => { setQuery(''); setCategory('全部'); }}>清除筛选</button></div> : null}
          {visibleChapters.map((chapter) => {
            const Icon = chapter.icon;
            const index = chapters.findIndex((item) => item.id === chapter.id) + 1;
            return <section className="guide-chapter" id={chapter.id} key={chapter.id}>
              <header className="guide-chapter-head">
                <span>{String(index).padStart(2, '0')}</span>
                <div><small>{chapter.category} / {chapter.eyebrow}</small><h3><Icon /> {chapter.title}</h3><p>{chapter.summary}</p></div>
                <Link to={chapter.href}>{chapter.action} <ArrowRight /></Link>
              </header>
              <div className="guide-io-grid">
                <article><Target /><small>PURPOSE</small><h4>解决什么问题</h4><p>{chapter.purpose}</p></article>
                <article><DatabaseZap /><small>INPUT</small><h4>使用哪些数据</h4><ul>{chapter.data.map((item) => <li key={item}>{item}</li>)}</ul></article>
                <article><TableProperties /><small>OUTPUT</small><h4>最终得到什么</h4><ul>{chapter.outputs.map((item) => <li key={item}>{item}</li>)}</ul></article>
              </div>
              <AnnotatedScreenshot chapter={chapter} onOpen={() => setLightbox(chapter)} />
              <div className="guide-procedure"><div><small>OPERATING PROCEDURE</small><h4>推荐操作顺序</h4></div><ol>{chapter.steps.map((step, stepIndex) => <li key={step}><b>{String(stepIndex + 1).padStart(2, '0')}</b><span>{step}</span></li>)}</ol></div>
              <div className="guide-module-block">
                <div className="guide-subhead"><ListChecks /><div><small>DETAILED FEATURE MAP</small><h4>{chapter.title} · {chapter.modules.length} 个子模块</h4></div></div>
                <div className="guide-modules">{chapter.modules.map((module, moduleIndex) => <details key={module.name} open={moduleIndex === 0}>
                  <summary><b>{String(moduleIndex + 1).padStart(2, '0')}</b><span><strong>{module.name}</strong><small>{module.purpose}</small></span><ChevronRight /></summary>
                  <div className="guide-module-detail"><dl><div><dt>数据</dt><dd>{module.data}</dd></div><div><dt>可操作</dt><dd><ul>{module.actions.map((item) => <li key={item}>{item}</li>)}</ul></dd></div><div><dt>输出</dt><dd>{module.output}</dd></div></dl>{module.route ? <Link to={module.route}>打开此子页 <ArrowRight /></Link> : null}</div>
                </details>)}</div>
              </div>
              <div className="guide-verification"><ShieldCheck /><div><small>BEFORE YOU TRUST IT</small><h4>使用前核验</h4><ul>{chapter.verify.map((item) => <li key={item}>{item}</li>)}</ul></div></div>
            </section>;
          })}
        </section>

        <section className="guide-section" id="workflow">
          <div className="guide-section-title"><span>04</span><div><small>CROSS-PAGE PLAYBOOKS</small><h2>功能之间如何真正打通</h2></div></div>
          <div className="guide-workflow-table" role="table" aria-label="跨页面研究流程">
            <div role="row"><b role="columnheader">研究任务</b><b role="columnheader">经过页面</b><b role="columnheader">具体做法</b><b role="columnheader">完成标准</b></div>
            {workflowRows.map((row) => <div role="row" key={row.title}><strong role="cell">{row.title}</strong><span role="cell">{row.pages}</span><span role="cell">{row.process}</span><span role="cell">{row.finish}</span></div>)}
          </div>
          <div className="guide-flow"><article><b>01</b><strong>提出可验证问题</strong><p>对象、时间、假设和决策问题。</p></article><ArrowRight /><article><b>02</b><strong>召回多源证据</strong><p>事实、观点、行情和反证分开。</p></article><ArrowRight /><article><b>03</b><strong>核验与量化</strong><p>回原文，检查样本和预期目标期。</p></article><ArrowRight /><article><b>04</b><strong>持续监控</strong><p>放入自选、情报或后台任务。</p></article></div>
        </section>

        <section className="guide-section" id="sources">
          <div className="guide-section-title"><span>05</span><div><small>DATA SOURCE DICTIONARY</small><h2>数据来自哪里，使用时检查什么</h2></div></div>
          <div className="guide-source-table" role="table" aria-label="平台数据来源说明">
            <div role="row"><b role="columnheader">来源</b><b role="columnheader">主要用途</b><b role="columnheader">使用时必须检查</b></div>
            {sourceRows.map(([source, use, check]) => <div role="row" key={source}><strong role="cell">{source}</strong><span role="cell">{use}</span><span role="cell">{check}</span></div>)}
          </div>
          <div className="guide-data-pipeline"><Workflow /><div><small>SHARED DATA PIPELINE</small><h3>上游适配器 → 增量去重 → SQLite统一库 → 页面/API/AI/回测</h3><p>页面不应各自临时抓取一份互不相干的数据。共享事实进入统一库；实时快照保留来源和时间；AI只读取可追溯数据，不覆盖原始记录。</p></div></div>
        </section>

        <section className="guide-section" id="faq">
          <div className="guide-section-title"><span>06</span><div><small>TROUBLESHOOTING & TRUST</small><h2>常见问题与正确处理方式</h2></div></div>
          <div className="guide-faq">{faqs.map(([question, answer], index) => <details key={question}><summary><b>{String(index + 1).padStart(2, '0')}</b><span>{question}</span><ChevronRight /></summary><p>{answer}</p></details>)}</div>
          <div className="guide-end"><HelpCircle /><div><small>SHORTEST STARTING PATH</small><h3>第一次使用只做三件事</h3><p>核对市场总览的交易日 → 把关注公司加入自选股 → 从事实时间线打开一篇原文。熟悉这三步后，再进入机构语料、行业调研和量化验证。</p></div><Link to="/app">现在开始 <ArrowRight /></Link></div>
        </section>
      </article>
    </div>

    <footer className="guide-footer"><span>乐子乌超级价值 · 完整使用手册 · 当前版本 2026-08-31</span><nav><Link to="/">产品介绍</Link><Link to="/app">研究平台</Link><a href="#find">搜索手册</a></nav></footer>
    {lightbox?.screenshot && lightbox.alt ? <div className="guide-lightbox" role="dialog" aria-modal="true" aria-label="页面截图预览" onClick={() => setLightbox(null)}>
      <button type="button" onClick={() => setLightbox(null)} aria-label="关闭截图预览"><X /></button>
      <div className="guide-lightbox-shell" onClick={(event) => event.stopPropagation()}>
        <div className="guide-lightbox-image"><img src={lightbox.screenshot} alt={lightbox.alt} />{lightbox.hotspots?.map((hotspot) => <span className="guide-hotspot" style={{ left: `${hotspot.x}%`, top: `${hotspot.y}%` }} key={hotspot.number}>{hotspot.number}</span>)}</div>
        <aside><small>SCREEN ANATOMY</small><h2>{lightbox.title}</h2><ol>{lightbox.hotspots?.map((hotspot) => <li key={hotspot.number}><b>{hotspot.number}</b><div><strong>{hotspot.title}</strong><p>{hotspot.detail}</p></div></li>)}</ol></aside>
      </div>
    </div> : null}
  </main>;
};

export default UserGuidePage;
