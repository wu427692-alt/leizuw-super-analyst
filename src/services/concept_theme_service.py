# -*- coding: utf-8 -*-
"""Multi-source concept/theme graph and explainable stock exposure analytics."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import logging
import math
import re
import statistics
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import and_, case, desc, func, or_, select, update

from src.services.financial_data_service import (
    FinancialDataUpstreamError,
    TushareGatewayService,
)
from src.storage import (
    ConceptExposureRecord,
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptSyncRunRecord,
    ConceptThemeRecord,
    ConceptThemeSnapshotRecord,
    DatabaseManager,
    EssayAnalysisRecord,
    MarketIndexBar,
    MonitoringEventRecord,
    ResearchNote,
    StockDaily,
    UserWatchlistItem,
    utc_naive_now,
)

logger = logging.getLogger(__name__)


class ConceptThemeError(RuntimeError):
    """A recoverable concept data or analytics error."""


SOURCE_LABELS = {
    "ths": "同花顺概念/行业",
    "dc_board": "东方财富板块",
    "dc_theme": "东方财富题材库",
    "kpl": "开盘啦题材",
    "tdx": "通达信板块",
    "sw": "申万行业",
}

SOURCE_RELIABILITY = {
    "sw": 1.00,
    "dc_theme": 0.96,
    "kpl": 0.92,
    "dc_board": 0.86,
    "ths": 0.82,
    "tdx": 0.74,
}

# Catalogs are auditable source nodes, while consensus votes must represent
# independent providers.  The two Eastmoney catalogs are useful separate
# taxonomies, but counting both as independent votes would inflate consensus.
SOURCE_PROVIDERS = {
    "ths": "同花顺",
    "dc_board": "东方财富",
    "dc_theme": "东方财富",
    "kpl": "开盘啦",
    "tdx": "通达信",
    "sw": "申万",
}


def _source_provider(source: Any) -> str:
    return SOURCE_PROVIDERS.get(str(source or ""), str(source or ""))


def _independent_source_count(sources: Iterable[Any]) -> int:
    return len({_source_provider(source) for source in sources if str(source or "").strip()})


def _independent_source_strength(sources: Iterable[Any]) -> float:
    """Use the strongest catalog within a provider, never double-count it."""
    provider_weights: Dict[str, float] = {}
    for source in sources:
        key = str(source or "")
        provider = _source_provider(key)
        provider_weights[provider] = max(provider_weights.get(provider, 0.0), SOURCE_RELIABILITY.get(key, 0.7))
    return sum(provider_weights.values())


def _provider_sql(column: Any) -> Any:
    return case((column.in_(("dc_board", "dc_theme")), "东方财富"), else_=column)


DRIVER_CATEGORY_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("业绩与预期", ("业绩", "利润", "营收", "收入", "预增", "预亏", "目标价", "盈利预测")),
    ("订单与客户", ("订单", "中标", "客户", "供应商", "合作", "框架协议")),
    ("产品与技术", ("产品", "技术", "研发", "专利", "验证", "投产", "扩产", "产能")),
    ("资本与治理", ("回购", "增持", "减持", "股东", "并购", "重组", "定增", "股权")),
    ("风险与监管", ("处罚", "诉讼", "监管", "风险", "终止", "问询", "立案")),
    ("行业供需", ("供需", "涨价", "降价", "价格", "景气", "库存")),
)

STRUCTURAL_FAMILY_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("区域与地理", (
        "华东", "华南", "华北", "华中", "西南", "西北", "东北", "城市群", "都市圈",
        "京津冀", "长三角", "珠三角", "海峡西岸", "深圳特区", "一体化",
        "北京市", "上海市", "天津市", "重庆市", "杭州市", "苏州市", "广州市", "深圳市",
        "南京市", "成都市", "无锡市", "宁波市", "合肥市", "长沙市", "绍兴市", "武汉市", "常州市",
        "台州市", "厦门市", "嘉兴市", "青岛市", "西安市", "地方国企广东省",
        "长江三角",
    )),
    ("公司治理与资本事件", (
        "股权转让", "控制权变更", "回购", "增持", "减持", "定增", "股权激励", "股权分散",
        "并购重组", "重组预案", "可转债", "转债标的", "含H股", "AH股", "关税战", "员工持股",
        "整体上市", "股权集中", "参股新三板", "参股新股", "摘帽", "SPAC", "主营变更",
    )),
    ("国资与所有制", ("地方国企", "地方国资", "央企", "国资控股", "国资入股")),
    ("宏观与跨境", ("人民币贬值", "贬值受益", "海外业务", "中概股", "红筹股", "俄乌冲突", "出口管制", "对日反制")),
    ("行业供需与价格", ("涨价", "降价", "库存周期", "供给收缩")),
    ("业绩与财务特征", (
        "中报预增", "中报首亏", "中报预减", "中报扭亏", "一季报预增", "三季报预增", "业绩预升", "连续亏损", "扣非亏损",
        "亏损股", "微利股", "绩优股", "高应收款", "商誉减值", "久不分红", "自由现金流",
        "预计转亏", "高负债率",
    )),
    ("机构持仓与资金偏好", (
        "机构重仓", "QFII", "基金重仓", "社保重仓", "社保新进", "私募重仓", "私募新进",
        "券商重仓", "北上重仓", "陆股通重仓", "金仓", "密集调研", "明星股", "知名公司",
        "基金增仓", "基金减仓", "基金独门", "国家大基金持股", "大基金持股", "券商金股",
    )),
    ("指数与交易风格", (
        "创业板综", "富时罗素", "标准普尔", "MSCI", "中证500", "上证380", "上证180", "沪深300",
        "HS300", "深成500", "宽基ETF", "大盘股", "中盘股", "小盘股", "微盘股", "成长", "价值",
        "高股息", "高分红", "高市盈率", "低市盈率", "高市净率", "低市净率", "低价股", "百元股",
        "破增发价", "破发行价", "破发股", "破净", "趋势股", "强势", "高振幅", "超跌", "周期股",
        "非周期股", "行业龙头", "次新", "ST股", "活跃小盘", "活跃股", "低安全分", "专项贷款",
        "微盘精选", "最近多板", "两年新股", "近已解禁", "科技风格", "先进制造风格", "红利股",
        "热股", "高贝塔", "户数增加", "户数减少", "送转潜力", "创业成份", "深证100", "中特估",
        "漂亮100", "通达信88", "高融资盘", "昨日涨停", "昨日高换手", "风险提示", "含B股", "AB股",
        "机构吸筹", "即将解禁", "题材股", "活跃ETF", "WSB",
    )),
)

NON_NARRATIVE_FAMILIES = {
    "市场风格与宽基", "指数与交易风格", "机构持仓与资金偏好",
    "业绩与财务特征", "公司治理与资本事件", "区域与地理",
}


def _classify_unique_driver(title: Any, summary: Any) -> Tuple[str, str]:
    title_text = str(title or "").lower()
    summary_text = str(summary or "").lower()
    text = f"{title_text} {summary_text}"
    # 标题是编辑者给出的主事件，权重高于摘要中的背景词；同分时保留规则优先级，
    # 避免“新产品完成客户验证”被摘要里的“客户”误判为订单事件。
    best_score = 0
    category = "其他公司事实"
    for name, keywords in DRIVER_CATEGORY_RULES:
        score = (
            sum(2 for word in keywords if word.lower() in title_text)
            + sum(1 for word in keywords if word.lower() in summary_text)
        )
        if score > best_score:
            best_score = score
            category = name
    positive = sum(word in text for word in ("超预期", "增长", "上调", "中标", "回购", "增持", "突破", "落地", "投产", "扩产"))
    negative = sum(word in text for word in ("不及预期", "低于预期", "下滑", "亏损", "减持", "处罚", "诉讼", "终止", "下调", "风险", "问询"))
    direction = "positive" if positive > negative else "negative" if negative > positive else "neutral"
    return category, direction

THS_TYPE_MAP = {
    "N": ("concept", 3),
    "I": ("industry", 1),
    "R": ("region", 2),
    "S": ("feature", 3),
    "ST": ("style", 3),
    "TH": ("theme", 2),
    "BB": ("broad", 0),
}

FAMILY_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("AI算力与数字基础设施", ("AI", "人工智能", "算力", "光模块", "光通信", "光纤", "CPO", "服务器", "数据中心", "液冷", "PCB", "铜缆", "硅光", "存储", "数字身份", "电子身份证", "云计算", "5G", "6G", "F5G", "物联网", "区块链", "网络安全", "数据安全", "安防", "大数据", "数据要素", "数据确权", "时空大数据", "数字孪生", "数字经济", "数字货币", "移动支付", "跨境支付", "信创", "国资云", "云办公", "远程办公", "工业互联网", "智慧城市", "智慧政务", "财税数字化", "量子科技", "元宇宙", "虚拟现实", "混合现实", "虚拟数字人", "空间计算", "ChatGPT", "DeepSeek", "英伟达", "Web3", "NFT", "数字水印", "华为昇腾", "华为欧拉", "华为鸿蒙", "鸿蒙", "ERP", "MLOps", "SaaS", "国产软件", "边缘计算", "东数西算", "星闪", "超清视频")),
    ("半导体与先进电子", ("半导体", "芯片", "集成电路", "光刻", "封测", "消费电子", "电子元件", "汽车电子", "军工电子", "电子布", "电子特气", "电子纸", "电子后视镜", "电子车牌", "电子信息", "被动元件", "先进封装", "OLED", "MiniLED", "MicroLED", "LED", "MLCC", "玻璃基板", "传感器", "无线耳机", "智能穿戴", "智能家居", "苹果", "小米", "华为海思", "EDA", "折叠屏", "柔性屏", "华为手机", "富士康")),
    ("先进制造与机器人", ("机器人", "自动化", "工业母机", "数控", "智能制造", "机械", "设备", "人形", "3D打印", "减速器", "机器视觉", "新型工业化", "专精特新", "工业软件", "海工装备", "高端装备")),
    ("低空经济与商业航天", ("低空", "无人机", "eVTOL", "飞行汽车", "航空", "航天", "卫星", "军工", "大飞机", "军民融合", "国产航母", "中船系", "成飞")),
    ("汽车与智能驾驶", ("汽车", "车联网", "智能驾驶", "无人驾驶", "锂电", "充电桩", "一体化压铸", "特斯拉", "比亚迪", "宁德时代", "换电", "高压快充", "激光雷达", "EDR")),
    ("新能源与电力系统", ("光伏", "太阳能", "风电", "储能", "电力", "电网", "核电", "氢能", "新能源", "电池", "天然气", "页岩气", "可燃冰", "可控核聚变", "特高压", "虚拟电厂", "抽水蓄能", "超级电容", "超超临界", "光热发电", "空气能热泵", "建筑节能", "POE胶膜", "复合集流体", "无线充电", "华为数字能源", "超导")),
    ("医药健康", ("医药", "医疗", "创新药", "生物", "疫苗", "中药", "CXO", "CRO", "器械", "减肥药", "辅助生殖", "医美", "幽门螺杆菌", "肝炎", "维生素", "基因测序", "流感", "NMN", "重组蛋白", "民营医院", "脑机接口", "养老", "CAR-T", "干细胞", "血氧仪", "病毒防治", "青蒿素", "仿制药", "DRG", "家庭医生", "猴痘", "高压氧舱")),
    ("农业与食品", ("农业", "种植", "粮食", "玉米", "转基因", "农机", "土地流转", "供销社", "化肥", "猪肉", "鸡肉", "乳业", "人造肉", "预制菜", "水产", "养殖", "种业", "农产品", "工业大麻")),
    ("消费与品牌", ("消费", "白酒", "食品", "旅游", "零售", "家电", "电商", "电子商务", "传媒", "游戏", "电子竞技", "电子游戏", "电子烟", "啤酒", "宠物经济", "婴童", "托育", "体育产业", "在线教育", "职业教育", "烟草", "代糖", "小红书", "腾讯", "阿里巴巴", "百度", "抖音", "快手", "拼多多", "网红经济", "C2M")),
    ("资源与周期", ("有色", "煤炭", "钢铁", "化工", "稀土", "黄金", "石油", "金属", "资源", "锂矿", "盐湖提锂", "石墨烯", "碳纤维", "钛白粉", "草甘膦", "有机硅", "PVDF", "环氧丙烷", "新材料", "培育钻石")),
    ("基础设施与交通物流", ("高铁", "轨道交通", "航运", "港口", "物流", "冷链", "地下管网", "新型城镇化", "智慧灯杆", "公路", "铁路", "机场", "水利", "基建", "PPP", "工程建设", "装配式建筑", "房屋检测")),
    ("生态环保", ("环保", "节能", "固废", "垃圾分类", "土壤修复", "核污染防治", "碳中和", "碳交易", "净水", "污水", "再生资源")),
    ("金融地产", ("银行", "证券", "保险", "金融", "地产", "房地产", "创投", "独角兽", "租售同权", "物业管理", "融资融券", "沪股通", "深股通", "参股券商", "证金持股", "举牌")),
    ("政策改革与区域", ("国企改革", "一带一路", "自贸", "新区", "区域", "振兴", "政策", "共同富裕", "中俄贸易", "统一大市场", "西部大开发", "海峡两岸", "粤港澳", "自由贸易港", "海洋经济", "中字头", "知识产权")),
    ("AI算力与数字基础设施", ("华为", "阿里", "字节跳动", "蚂蚁集团", "稳定币", "工业互联", "互联网服务", "通信技术", "信息安全", "人脸识别", "词元", "数字政务", "国产操作系统")),
    ("新能源与电力系统", ("风能", "BIPV", "雅江水电", "雅下水电", "超临界发电")),
    ("医药健康", ("口罩", "病原体", "生育", "三胎", "人脑工程", "基因", "体外诊断", "细胞免疫")),
    ("消费与品牌", ("IP经济", "谷子经济", "内贸流通", "体育", "家用电器", "味蕾经济", "犒赏经济", "线上送礼物", "现代服务业")),
    ("基础设施与交通物流", ("新型城镇", "智能交通", "装配建筑", "绿色建筑")),
    ("汽车与智能驾驶", ("智能座舱", "毫米波雷达", "两轮车")),
    ("半导体与先进电子", ("中芯国际", "中芯")),
    ("新能源与电力系统", ("柔性直流输电", "西藏水电")),
    ("生态环保", ("降解塑料", "可降解塑料")),
    ("金融地产", ("物业投资", "期货", "财产管理", "化债AMC")),
    ("资源与周期", ("氟",)),
    ("政策改革与区域", ("反内卷", "出海", "新质生产力", "数字乡村")),
    ("AI算力与数字基础设施", (
        "Kimi", "MCP", "移动互联网", "互联网+", "软件外包", "WiFi", "IPv6", "VPN", "UWB",
        "高带宽内存", "超节点", "超聚变", "摩尔线程", "谷歌", "AMD", "太赫兹", "数字阅读",
    )),
    ("半导体与先进电子", (
        "IGBT", "碳化硅", "氮化镓", "磷化铟", "裸眼3D", "3D摄像头", "屏下摄像", "纳米银",
    )),
    ("先进制造与机器人", ("工业4.0", "灯塔工厂", "磁悬浮压缩机", "PEEK", "发电机")),
    ("汽车与智能驾驶", ("胎压监测", "AEBS", "无人车辆")),
    ("低空经济与商业航天", ("空间站", "军贸")),
    ("新能源与电力系统", ("地热能", "植物照明")),
    ("医药健康", ("阿尔茨海默", "熊去氧胆酸", "肝素", "免疫治疗", "医废处理")),
    ("消费与品牌", (
        "博彩", "足球", "世界杯", "赛马", "彩票", "免税", "退税商店", "超级品牌", "品牌服饰",
        "餐饮连锁", "家具", "玩具", "邮轮", "外卖", "冰雪经济", "化妆品", "社区团购", "盲盒经济",
        "影视", "户外露营", "调味品", "地摊经济", "首发经济",
    )),
    ("资源与周期", (
        "民爆", "特钢", "工业气体", "染料", "石墨电极", "水泥", "丙烯酸", "白银", "铝",
        "碳基材料", "氦气", "PTFE", "油气设服", "抗菌面料",
    )),
    ("基础设施与交通物流", ("ETC", "基础建设", "快递", "船舶制造", "磁悬浮", "海绵城市")),
    ("生态环保", ("PM2.5", "尾气治理")),
    ("金融地产", ("REITs", "券商", "化债(AMC", "蚂蚁金服")),
)

CLUSTER_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("光通信产业链", ("CPO", "NPO", "光模块", "光通信", "光器件", "硅光", "光芯片", "光纤")),
    ("PCB/CCL产业链", ("PCB", "CCL", "覆铜板", "电子布", "玻纤布")),
    ("AI数据中心", ("算力", "数据中心", "服务器", "液冷", "超节点")),
    ("半导体设备材料", ("光刻", "晶圆", "半导体设备", "电子气体", "先进封装")),
    ("低空经济", ("低空", "EVTOL", "无人机", "空管")),
    ("商业航天", ("商业航天", "卫星", "火箭", "航天")),
    ("人形机器人", ("人形机器人", "减速器", "丝杠", "灵巧手")),
    ("智能驾驶", ("智能驾驶", "无人驾驶", "激光雷达", "车联网")),
    ("创新药", ("创新药", "ADC", "双抗", "减肥药", "小核酸")),
    ("新型电力系统", ("储能", "电网", "电力", "虚拟电厂", "特高压")),
)

CANONICAL_ALIASES = {
    "共封装光学": "CPO/共封装光学",
    "共封装光学CPO": "CPO/共封装光学",
    "CPO": "CPO/共封装光学",
    "CPO概念": "CPO/共封装光学",
    "CPO(共封装光学)": "CPO/共封装光学",
    "光模块": "光通信/光模块",
    "光通信": "光通信/光模块",
    "光通信模块": "光通信/光模块",
    "光通信设备": "光通信/光模块",
    "低空经济概念": "低空经济",
    "飞行汽车": "eVTOL/飞行汽车",
    "飞行汽车(eVTOL)": "eVTOL/飞行汽车",
    "eVTOL": "eVTOL/飞行汽车",
    "人形机器人概念": "人形机器人",
    "AI算力": "AI算力",
    "算力概念": "AI算力",
    "AIPC": "AI PC",
    "AI PC概念": "AI PC",
    "氢能源": "氢能",
    "钠电池": "钠离子电池",
    "核能核电": "核电",
    "核电核能": "核电",
    "HIT电池": "HJT电池",
}


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip().replace("-", "")[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").date() if text else None
    except ValueError:
        return None


def _ts_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    if re.fullmatch(r"\d{6}", text):
        suffix = "SH" if text.startswith(("5", "6", "9")) else "BJ" if text.startswith(("4", "8")) else "SZ"
        return f"{text}.{suffix}"
    return text


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[（(]?(?:概念|板块|指数)[）)]?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "", text)
    return text or str(value or "").strip()


def canonicalize_theme(name: Any) -> str:
    normalized = _normalize_name(name)
    key = re.sub(r"[/+·—_\-（）()\s]", "", normalized).upper()
    for alias, target in CANONICAL_ALIASES.items():
        alias_key = re.sub(r"[/+·—_\-（）()\s]", "", alias).upper()
        if key == alias_key:
            return target
    return normalized


def theme_family(name: str, theme_type: str) -> str:
    if theme_type == "industry":
        return "申万/市场行业体系"
    if theme_type == "region":
        return "区域与地理"
    if theme_type in {"style", "feature", "broad"}:
        return "市场风格与宽基"
    upper = name.upper()
    if upper == "ST":
        return "指数与交易风格"
    if upper == "MR":
        return "半导体与先进电子"
    for family, keywords in STRUCTURAL_FAMILY_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return family
    for family, keywords in FAMILY_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return family
    return "其他市场题材"


def theme_cluster(name: str, family: str) -> str:
    raw_name = str(name or "").strip()
    if family == "申万/市场行业体系":
        # Preserve the economic industry instead of collapsing every Shenwan
        # level into one generic cluster.  Roman suffixes describe hierarchy,
        # not independent narratives.
        industry = re.sub(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+(?:\(A股\))?$", "", raw_name).strip()
        industry = re.sub(r"\(A股\)$", "", industry).strip()
        return industry or family
    upper = raw_name.upper()
    for cluster, keywords in CLUSTER_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return cluster
    return family


class ConceptThemeService:
    """Shared concept catalog, evidence weights, and return attribution service."""

    _market_date_lock = threading.Lock()
    _market_date_value: Optional[date] = None
    _market_date_checked_at: Optional[datetime] = None
    _taxonomy_lock = threading.Lock()
    _taxonomy_cache: Dict[int, Tuple[datetime, Dict[str, Any]]] = {}

    def __init__(self, *, gateway: Optional[TushareGatewayService] = None, db: Optional[DatabaseManager] = None):
        self.gateway = gateway or TushareGatewayService()
        self.db = db or DatabaseManager.get_instance()

    @contextmanager
    def _read_scope(self):
        """Read without a commit so returned ORM attributes are not expired."""
        session = self.db.get_session()
        try:
            yield session
        finally:
            session.close()

    def _taxonomy_index(self) -> Dict[str, Any]:
        """Build the semantic hierarchy once per process instead of on every page click."""
        cache_key = id(self.db)
        now = utc_naive_now()
        cached = self.__class__._taxonomy_cache.get(cache_key)
        if cached and (now - cached[0]).total_seconds() < 300:
            return cached[1]
        with self.__class__._taxonomy_lock:
            cached = self.__class__._taxonomy_cache.get(cache_key)
            if cached and (now - cached[0]).total_seconds() < 300:
                return cached[1]
            with self._read_scope() as session:
                rows = session.execute(select(
                    ConceptThemeRecord.id, ConceptThemeRecord.name, ConceptThemeRecord.theme_type,
                )).all()
            by_id: Dict[int, Tuple[str, str]] = {}
            by_canonical: Dict[str, Tuple[str, str]] = {}
            type_by_canonical: Dict[str, str] = {}
            family_counts: Dict[str, int] = defaultdict(int)
            cluster_families: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for theme_id, name, kind in rows:
                canonical = canonicalize_theme(name)
                family_name = theme_family(canonical, kind)
                cluster_name = theme_cluster(canonical, family_name)
                by_id[int(theme_id)] = (family_name, cluster_name)
                by_canonical.setdefault(canonical, (family_name, cluster_name))
                type_by_canonical.setdefault(canonical, kind)
                family_counts[family_name] += 1
                cluster_families[family_name][cluster_name] += 1
            value = {
                "by_id": by_id,
                "by_canonical": by_canonical,
                "type_by_canonical": type_by_canonical,
                "family_counts": dict(sorted(family_counts.items(), key=lambda item: -item[1])),
                "cluster_families": {
                    family_name: dict(sorted(values.items(), key=lambda item: -item[1]))
                    for family_name, values in cluster_families.items()
                },
            }
            self.__class__._taxonomy_cache[cache_key] = (now, value)
            return value

    def latest_market_date(self) -> date:
        now = datetime.now()
        cached_at = self.__class__._market_date_checked_at
        cached_value = self.__class__._market_date_value
        if cached_value is not None and cached_at is not None and (now - cached_at).total_seconds() < 1800:
            return cached_value

        with self.__class__._market_date_lock:
            cached_at = self.__class__._market_date_checked_at
            cached_value = self.__class__._market_date_value
            if cached_value is not None and cached_at is not None and (now - cached_at).total_seconds() < 1800:
                return cached_value
            # Constituents and close-to-close attribution use the latest completed
            # exchange session. A malformed weekend StockDaily row must never be
            # forwarded as a provider trade_date (some concept APIs then return 0 rows).
            cutoff = date.today() if now.hour >= 16 else date.today() - timedelta(days=1)
            resolved: Optional[date] = None
            if getattr(self.gateway, "available", False):
                try:
                    calendar = self.gateway.query("trade_cal", params={
                        "exchange": "SSE",
                        "start_date": (cutoff - timedelta(days=24)).strftime("%Y%m%d"),
                        "end_date": cutoff.strftime("%Y%m%d"),
                        "is_open": "1",
                    })["rows"]
                    candidates = [_parse_date(row.get("cal_date")) for row in calendar]
                    resolved = max((item for item in candidates if item and item <= cutoff), default=None)
                except FinancialDataUpstreamError:
                    logger.debug("concept market calendar unavailable; using local completed session")
            if resolved is None:
                with self._read_scope() as session:
                    candidates = session.execute(
                        select(StockDaily.date).where(StockDaily.date <= cutoff)
                        .distinct().order_by(desc(StockDaily.date)).limit(12)
                    ).scalars().all()
                resolved = next((item for item in candidates if item.weekday() < 5), None)
            if resolved is None:
                resolved = cutoff
                while resolved.weekday() >= 5:
                    resolved -= timedelta(days=1)
            self.__class__._market_date_value = resolved
            self.__class__._market_date_checked_at = now
            return resolved

    def _semantic_cluster_for(self, canonical_name: str, taxonomy: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Resolve a canonical theme to one independent economic narrative.

        Several vendors may label the same driver as CPO, optical modules or
        optical communication. These remain separate auditable catalog nodes,
        but must not be counted as independent stock narratives.
        """
        taxonomy = taxonomy or self._taxonomy_index()
        mapped = taxonomy.get("by_canonical", {}).get(canonical_name)
        if mapped:
            return mapped
        family_name = theme_family(canonical_name, "concept")
        return family_name, theme_cluster(canonical_name, family_name)

    @staticmethod
    def _overlap_profile(themes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        groups: Dict[str, Dict[str, Any]] = {}
        for theme in themes:
            family_name = str(theme.get("family") or "其他市场题材")
            cluster_name = str(theme.get("cluster") or family_name)
            # Named industrial clusters are stronger identities than their
            # broader family. If two upstream labels land in the same named
            # cluster through different family heuristics, count them once.
            key = cluster_name if cluster_name != family_name else f"{family_name}::{cluster_name}"
            group = groups.setdefault(key, {
                "family": family_name, "cluster": cluster_name, "themes": [],
                "weight_sum": 0.0, "effective_weight": 0.0, "representative": None,
            })
            group["themes"].append(str(theme.get("canonical_name") or ""))
            weight = float(theme.get("weight_score") or 0)
            group["weight_sum"] += weight
            group["effective_weight"] = max(float(group["effective_weight"]), weight)
            representative = group["representative"]
            if representative is None or weight > float(representative.get("weight_score") or 0):
                group["representative"] = theme
        ranked = sorted(groups.values(), key=lambda item: -float(item["effective_weight"]))
        raw_count = len(themes)
        independent_count = len(ranked)
        total_weight = sum(float(item["effective_weight"]) for item in ranked)
        dominant_share = float(ranked[0]["effective_weight"]) / total_weight if ranked and total_weight else 0.0
        overlap_rate = (raw_count - independent_count) / raw_count if raw_count else 0.0
        return {
            "independent_cluster_count": independent_count,
            "overlap_rate": round(overlap_rate * 100, 1),
            "dominant_cluster_share": round(dominant_share * 100, 1),
            "dominant_cluster": ranked[0]["cluster"] if ranked else None,
            "clusters": [{
                "family": item["family"], "cluster": item["cluster"],
                "theme_count": len(item["themes"]), "themes": item["themes"],
                "weight_share": round((float(item["effective_weight"]) / total_weight * 100) if total_weight else 0.0, 1),
            } for item in ranked],
            "representatives": [item["representative"] for item in ranked if item["representative"] is not None],
            "method": "同一语义簇内的相近标签只计作一条独立主线；原始标签仍完整保留用于审计。",
        }

    def sync_catalog(self, *, market_date: Optional[date] = None) -> Dict[str, Any]:
        if not self.gateway.available:
            raise ConceptThemeError("Tushare 未配置，无法更新题材目录")
        target = market_date or self.latest_market_date()
        run_id = self._start_run("catalog", target, "读取六套题材目录")
        source_stats: Dict[str, Any] = {}
        normalized: List[Dict[str, Any]] = []
        try:
            ths = self.gateway.query("ths_index", params={})["rows"]
            source_stats["ths"] = len(ths)
            for row in ths:
                kind, level = THS_TYPE_MAP.get(str(row.get("type") or "N"), ("concept", 3))
                normalized.append(self._theme_row(
                    source="ths", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=level, count=row.get("count"), market_date=target, payload=row,
                ))

            day_text = target.strftime("%Y%m%d")
            dc = self.gateway.query("dc_index", params={"trade_date": day_text})["rows"]
            source_stats["dc_board"] = len(dc)
            for row in dc:
                raw_type = str(row.get("idx_type") or "概念板块")
                kind = "industry" if "行业" in raw_type else "region" if "地域" in raw_type else "concept"
                normalized.append(self._theme_row(
                    source="dc_board", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=_safe_int(row.get("level")) or (1 if kind == "industry" else 3),
                    count=(_safe_int(row.get("up_num")) or 0) + (_safe_int(row.get("down_num")) or 0),
                    market_date=_parse_date(row.get("trade_date")) or target,
                    pct_change=row.get("pct_change"), payload=row,
                ))

            dc_theme = self.gateway.query("dc_concept", params={"trade_date": day_text})["rows"]
            source_stats["dc_theme"] = len(dc_theme)
            for row in dc_theme:
                rank = _safe_float(row.get("sort"))
                heat = max(0.0, min(100.0, 101.0 - math.sqrt(rank or 10_000)))
                normalized.append(self._theme_row(
                    source="dc_theme", code=row.get("theme_code"), name=row.get("name"), theme_type="theme",
                    level=3, market_date=_parse_date(row.get("trade_date")) or target,
                    heat=heat, pct_change=row.get("pct_change"), fund_flow=row.get("main_change"), payload=row,
                ))

            kpl = self.gateway.query("kpl_concept", params={"trade_date": day_text})["rows"]
            source_stats["kpl"] = len(kpl)
            for row in kpl:
                normalized.append(self._theme_row(
                    source="kpl", code=row.get("ts_code"), name=row.get("name"), theme_type="theme", level=3,
                    count=(_safe_int(row.get("z_t_num")) or 0) + (_safe_int(row.get("up_num")) or 0),
                    market_date=_parse_date(row.get("trade_date")) or target,
                    heat=min(100.0, (_safe_float(row.get("z_t_num")) or 0) * 8.0), payload=row,
                ))

            tdx = self.gateway.query("tdx_index", params={"trade_date": day_text})["rows"]
            source_stats["tdx"] = len(tdx)
            for row in tdx:
                raw_type = str(row.get("idx_type") or "概念板块")
                kind = "industry" if "行业" in raw_type else "region" if "地区" in raw_type else "concept"
                normalized.append(self._theme_row(
                    source="tdx", code=row.get("ts_code"), name=row.get("name"), theme_type=kind,
                    level=1 if kind == "industry" else 3, count=row.get("idx_count"),
                    market_date=_parse_date(row.get("trade_date")) or target, payload=row,
                ))

            sw_count = 0
            for level_text, level_number in (("L1", 1), ("L2", 2), ("L3", 3)):
                rows = self.gateway.query("index_classify", params={"level": level_text, "src": "SW2021"})["rows"]
                sw_count += len(rows)
                for row in rows:
                    normalized.append(self._theme_row(
                        source="sw", code=row.get("index_code"), name=row.get("industry_name"),
                        theme_type="industry", level=level_number, parent=row.get("parent_code"),
                        market_date=target, payload=row,
                    ))
            source_stats["sw"] = sw_count

            saved = self._upsert_themes(normalized)
            self._finish_run(run_id, status="completed", progress=100, stage="目录更新完成",
                             themes=len(normalized), source_stats=source_stats)
            return {"status": "completed", "market_date": target.isoformat(), "seen": len(normalized),
                    "saved": saved, "sources": source_stats}
        except Exception as exc:
            self._finish_run(run_id, status="failed", progress=100, stage="目录更新失败",
                             themes=len(normalized), source_stats=source_stats, error=exc)
            raise ConceptThemeError(f"题材目录更新失败：{str(exc)[:300]}") from exc

    def refresh_theme(self, theme_id: int, *, calculate: bool = True) -> Dict[str, Any]:
        with self._read_scope() as session:
            theme = session.get(ConceptThemeRecord, int(theme_id))
            if theme is None:
                raise ConceptThemeError("题材不存在")
            snapshot = self._theme_dict(theme)
        rows = self._fetch_theme_members(snapshot)
        # Most component endpoints cap a single response at 2,000 or more rows.
        # Only a non-empty response safely below that boundary can be treated as complete.
        replace_existing = 0 < len(rows) < 1900
        saved = self._upsert_memberships(snapshot, rows, replace_existing=replace_existing)
        exposures = self.calculate_canonical_exposures(snapshot["canonical_name"], horizon_days=60) if calculate else 0
        return {"theme_id": theme_id, "received": len(rows), "saved": saved, "exposures": exposures}

    def refresh_stock(self, ts_code: str, *, calculate: bool = True) -> Dict[str, Any]:
        code = _ts_code(ts_code)
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
            raise ConceptThemeError("股票代码格式不正确")
        target = self.latest_market_date()
        day_text = target.strftime("%Y%m%d")
        catalog = self._theme_lookup()
        fetched: List[Tuple[str, Dict[str, Any]]] = []
        source_stats: Dict[str, int] = {}
        calls = (
            ("ths", "ths_member", {"con_code": code}),
            ("dc_board", "dc_member", {"con_code": code, "trade_date": day_text}),
            ("dc_theme", "dc_concept_cons", {"ts_code": code, "trade_date": day_text}),
            ("kpl", "kpl_concept_cons", {"con_code": code, "trade_date": day_text}),
            ("tdx", "tdx_member", {"con_code": code, "trade_date": day_text}),
        )
        for source, api_name, params in calls:
            try:
                rows = self.gateway.query(api_name, params=params)["rows"]
                source_stats[source] = len(rows)
                fetched.extend((source, row) for row in rows)
            except FinancialDataUpstreamError as exc:
                logger.warning("concept stock membership source failed: %s %s", source, exc)
                source_stats[source] = -1
        try:
            sw = self.gateway.query("index_member_all", params={"ts_code": code, "is_new": "Y"})["rows"]
            source_stats["sw"] = len(sw) * 3
            for row in sw:
                for level in (1, 2, 3):
                    fetched.append(("sw", {
                        "ts_code": row.get(f"l{level}_code"), "con_code": code, "con_name": row.get("name"),
                        "industry_name": row.get(f"l{level}_name"), "trade_date": day_text,
                    }))
        except FinancialDataUpstreamError as exc:
            logger.warning("concept stock SW membership source failed: %s", exc)
            source_stats["sw"] = -1

        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        missing: List[Dict[str, Any]] = []
        for source, row in fetched:
            source_code = str(row.get("theme_code") or row.get("ts_code") or "")
            theme = catalog.get((source, source_code))
            if theme is None:
                name = row.get("name") if source in {"kpl", "dc_theme"} else row.get("industry_name")
                if not name:
                    continue
                missing.append(self._theme_row(
                    source=source, code=source_code, name=name,
                    theme_type="industry" if source == "sw" else "theme", level=3,
                    market_date=_parse_date(row.get("trade_date")) or target, payload=row,
                ))
        if missing:
            self._upsert_themes(missing)
            catalog = self._theme_lookup()
        for source, row in fetched:
            source_code = str(row.get("theme_code") or row.get("ts_code") or "")
            theme = catalog.get((source, source_code))
            if theme:
                grouped[int(theme["id"])].append(row)
        saved = 0
        canonicals: set[str] = set()
        for theme_id, rows in grouped.items():
            theme = next(value for value in catalog.values() if int(value["id"]) == theme_id)
            saved += self._upsert_memberships(theme, rows, force_stock=code)
            canonicals.add(str(theme["canonical_name"]))
        exposure_count = 0
        if calculate:
            for canonical in canonicals:
                exposure_count += self.calculate_canonical_exposures(canonical, horizon_days=60, only_stock=code)
        return {"ts_code": code, "memberships": saved, "exposures": exposure_count, "sources": source_stats}

    def overview(
        self, *, query: str = "", theme_type: str = "", source: str = "", family: str = "", cluster: str = "",
        min_sources: int = 1, sort_by: str = "heat", view: str = "source", page: int = 1, page_size: int = 80,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = max(12, min(page_size, 200))
        stock_matches: List[Dict[str, Any]] = []
        taxonomy = self._taxonomy_index()
        with self._read_scope() as session:
            statement = select(ConceptThemeRecord)
            count_stmt = select(func.count(ConceptThemeRecord.id))
            filters = []
            if query.strip():
                term = f"%{query.strip()}%"
                stock_theme_ids = select(ConceptMembershipRecord.theme_id).where(or_(
                    ConceptMembershipRecord.ts_code.like(term),
                    ConceptMembershipRecord.stock_name.like(term),
                ))
                filters.append(or_(ConceptThemeRecord.name.like(term), ConceptThemeRecord.canonical_name.like(term),
                                   ConceptThemeRecord.source_code.like(term), ConceptThemeRecord.id.in_(stock_theme_ids)))
            if theme_type:
                filters.append(ConceptThemeRecord.theme_type == theme_type)
            if source:
                filters.append(ConceptThemeRecord.source == source)
            if int(min_sources) > 1:
                consensus_names = select(ConceptThemeRecord.canonical_name).group_by(
                    ConceptThemeRecord.canonical_name,
                ).having(func.count(func.distinct(_provider_sql(ConceptThemeRecord.source))) >= min(5, int(min_sources)))
                filters.append(ConceptThemeRecord.canonical_name.in_(consensus_names))
            if family or cluster:
                matching_ids = [theme_id for theme_id, (family_name, cluster_name) in taxonomy["by_id"].items()
                                if (not family or family_name == family) and (not cluster or cluster_name == cluster)]
                filters.append(ConceptThemeRecord.id.in_(matching_ids) if matching_ids else ConceptThemeRecord.id < 0)
            if filters:
                statement = statement.where(and_(*filters))
                count_stmt = count_stmt.where(and_(*filters))
            if sort_by == "name":
                statement = statement.order_by(ConceptThemeRecord.canonical_name.asc())
            elif sort_by == "size":
                statement = statement.order_by(desc(ConceptThemeRecord.constituent_count), ConceptThemeRecord.name.asc())
            elif sort_by == "change":
                statement = statement.order_by(desc(ConceptThemeRecord.pct_change), desc(ConceptThemeRecord.heat_score))
            else:
                statement = statement.order_by(desc(ConceptThemeRecord.heat_score), desc(ConceptThemeRecord.market_date), ConceptThemeRecord.name.asc())
            if view == "canonical":
                source_rows = session.execute(statement).scalars().all()
                grouped_rows: Dict[str, ConceptThemeRecord] = {}
                for row in source_rows:
                    grouped_rows.setdefault(row.canonical_name, row)
                grouped_values = list(grouped_rows.values())
                total = len(grouped_values)
                rows = grouped_values[(page - 1) * page_size:page * page_size]
            else:
                rows = session.execute(statement.offset((page - 1) * page_size).limit(page_size)).scalars().all()
                total = int(session.execute(count_stmt).scalar_one())
            items = [self._theme_dict(row) for row in rows]
            page_canonicals = {item["canonical_name"] for item in items}
            canonical_coverage = {
                canonical: {"source_count": int(source_count or 0), "node_count": int(node_count or 0)}
                for canonical, source_count, node_count in session.execute(select(
                    ConceptThemeRecord.canonical_name,
                    func.count(func.distinct(_provider_sql(ConceptThemeRecord.source))),
                    func.count(ConceptThemeRecord.id),
                ).where(
                    ConceptThemeRecord.canonical_name.in_(page_canonicals),
                ).group_by(ConceptThemeRecord.canonical_name)).all()
            } if page_canonicals else {}
            canonical_stock_counts = dict(session.execute(select(
                ConceptThemeRecord.canonical_name,
                func.count(func.distinct(ConceptMembershipRecord.ts_code)),
            ).join(
                ConceptMembershipRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptThemeRecord.canonical_name.in_(page_canonicals),
                ConceptMembershipRecord.active.is_(True),
            ).group_by(ConceptThemeRecord.canonical_name)).all()) if view == "canonical" and page_canonicals else {}
            for item in items:
                coverage = canonical_coverage.get(item["canonical_name"], {})
                item["canonical_source_count"] = int(coverage.get("source_count", 0))
                item["canonical_node_count"] = int(coverage.get("node_count", 0))
                if view == "canonical":
                    item["constituent_count"] = int(canonical_stock_counts.get(item["canonical_name"], 0) or 0)
            latest_run = session.execute(select(ConceptSyncRunRecord).order_by(desc(ConceptSyncRunRecord.id)).limit(1)).scalar_one_or_none()
            source_counts = dict(session.execute(
                select(ConceptThemeRecord.source, func.count(ConceptThemeRecord.id)).group_by(ConceptThemeRecord.source)
            ).all())
            source_catalog_rows = session.execute(select(
                ConceptThemeRecord.source,
                func.max(ConceptThemeRecord.market_date),
                func.max(ConceptThemeRecord.updated_at),
            ).group_by(ConceptThemeRecord.source)).all()
            membered_by_source = dict(session.execute(select(
                ConceptThemeRecord.source, func.count(func.distinct(ConceptMembershipRecord.theme_id)),
            ).join(
                ConceptMembershipRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(ConceptMembershipRecord.active.is_(True)).group_by(ConceptThemeRecord.source)).all())
            attempted_by_source = dict(session.execute(select(
                ConceptThemeRecord.source, func.count(func.distinct(ConceptMembershipSyncState.theme_id)),
            ).join(
                ConceptMembershipSyncState, ConceptMembershipSyncState.theme_id == ConceptThemeRecord.id,
            ).group_by(ConceptThemeRecord.source)).all())
            type_counts = dict(session.execute(
                select(ConceptThemeRecord.theme_type, func.count(ConceptThemeRecord.id)).group_by(ConceptThemeRecord.theme_type)
            ).all())
            membership_count = int(session.execute(select(func.count(ConceptMembershipRecord.id)).where(ConceptMembershipRecord.active.is_(True))).scalar_one())
            membered_theme_count = int(session.execute(select(func.count(func.distinct(ConceptMembershipRecord.theme_id))).where(
                ConceptMembershipRecord.active.is_(True))).scalar_one())
            attempted_theme_count = int(session.execute(select(func.count(ConceptMembershipSyncState.id))).scalar_one())
            failed_theme_count = int(session.execute(select(func.count(ConceptMembershipSyncState.id)).where(
                ConceptMembershipSyncState.status == "failed",
            )).scalar_one())
            exposure_count = int(session.execute(select(func.count(ConceptExposureRecord.id))).scalar_one())
            latest_exposure_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date))).scalar_one_or_none()
            latest_market_date = session.execute(select(func.max(ConceptThemeRecord.market_date))).scalar_one_or_none()
            if query.strip():
                stock_term = f"%{query.strip()}%"
                matched_stocks = session.execute(select(
                    ConceptMembershipRecord.ts_code,
                    func.max(ConceptMembershipRecord.stock_name),
                    func.count(func.distinct(ConceptThemeRecord.canonical_name)),
                    func.count(func.distinct(_provider_sql(ConceptMembershipRecord.source))),
                ).join(
                    ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
                ).where(
                    ConceptMembershipRecord.active.is_(True),
                    or_(
                        ConceptMembershipRecord.ts_code.like(stock_term),
                        ConceptMembershipRecord.stock_name.like(stock_term),
                    ),
                ).group_by(ConceptMembershipRecord.ts_code).order_by(
                    desc(func.count(func.distinct(_provider_sql(ConceptMembershipRecord.source)))),
                    desc(func.count(func.distinct(ConceptThemeRecord.canonical_name))),
                ).limit(8)).all()
                stock_matches = [{
                    "ts_code": code, "name": name or code,
                    "theme_count": int(theme_count or 0), "source_count": int(source_count or 0),
                } for code, name, theme_count, source_count in matched_stocks]
        family_counts = taxonomy["family_counts"]
        cluster_families = taxonomy["cluster_families"]
        theme_total = sum(source_counts.values())
        classified_count = max(0, theme_total - family_counts.get("其他市场题材", 0))
        source_health = {}
        for source_name, source_market_date, source_updated_at in source_catalog_rows:
            catalog_nodes = int(source_counts.get(source_name, 0) or 0)
            attempted = int(attempted_by_source.get(source_name, 0) or 0)
            membered = int(membered_by_source.get(source_name, 0) or 0)
            source_health[source_name] = {
                "catalog_nodes": catalog_nodes,
                "market_date": source_market_date.isoformat() if source_market_date else None,
                "updated_at": source_updated_at.isoformat() if source_updated_at else None,
                "attempted_themes": attempted,
                "membered_themes": membered,
                "scan_coverage_pct": round(attempted / max(1, catalog_nodes) * 100, 1),
                "status": "fresh" if source_market_date and source_market_date == latest_market_date else "lagging",
            }
        fresh_catalogs = sum(1 for item in source_health.values() if item["status"] == "fresh")
        quality_warnings = []
        if latest_exposure_date and latest_market_date and latest_exposure_date < latest_market_date:
            quality_warnings.append(f"归因截止 {latest_exposure_date.isoformat()}，落后目录交易日")
        if fresh_catalogs < len(SOURCE_LABELS):
            quality_warnings.append(f"{len(SOURCE_LABELS) - fresh_catalogs} 套目录交易日滞后")
        if failed_theme_count:
            quality_warnings.append(f"{failed_theme_count} 个成分节点等待重试")
        return {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "view": "canonical" if view == "canonical" else "source",
            "stock_matches": stock_matches,
            "summary": {
                "themes": theme_total, "memberships": membership_count, "exposures": exposure_count,
                "classified_themes": classified_count,
                "semantic_coverage_pct": round(classified_count / max(1, theme_total) * 100, 1),
                "membered_themes": membered_theme_count,
                "attempted_themes": attempted_theme_count,
                "failed_themes": failed_theme_count,
                "scan_coverage_pct": round(attempted_theme_count / max(1, sum(source_counts.values())) * 100, 1),
                "membership_coverage_pct": round(membered_theme_count / max(1, sum(source_counts.values())) * 100, 1),
                "sources": source_counts, "source_health": source_health, "types": type_counts, "families": family_counts,
                "cluster_families": cluster_families,
                "market_date": latest_market_date.isoformat() if latest_market_date else "",
                "quality": {
                    "catalog_date": latest_market_date.isoformat() if latest_market_date else None,
                    "exposure_date": latest_exposure_date.isoformat() if latest_exposure_date else None,
                    "fresh_catalogs": fresh_catalogs,
                    "total_catalogs": len(SOURCE_LABELS),
                    "failed_themes": failed_theme_count,
                    "warnings": quality_warnings,
                },
            },
            "sync": self._run_dict(latest_run) if latest_run else None,
            "methodology": self.methodology(),
        }

    def theme_detail(self, theme_id: int, *, refresh_if_empty: bool = True, horizon_days: int = 60) -> Dict[str, Any]:
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        current_market_date = self.latest_market_date()
        with self._read_scope() as session:
            theme = session.get(ConceptThemeRecord, int(theme_id))
            if theme is None:
                raise ConceptThemeError("题材不存在")
            canonical = theme.canonical_name
            raw_themes = session.execute(
                select(ConceptThemeRecord).where(ConceptThemeRecord.canonical_name == canonical)
                .order_by(desc(ConceptThemeRecord.heat_score))
            ).scalars().all()
            theme_ids = [row.id for row in raw_themes]
            member_counts = dict(session.execute(select(
                ConceptMembershipRecord.theme_id, func.count(ConceptMembershipRecord.id)
            ).where(
                ConceptMembershipRecord.theme_id.in_(theme_ids), ConceptMembershipRecord.active.is_(True)
            ).group_by(ConceptMembershipRecord.theme_id)).all()) if theme_ids else {}
            exposure_total = int(session.execute(select(func.count(ConceptExposureRecord.id)).where(
                ConceptExposureRecord.canonical_name == canonical,
                ConceptExposureRecord.horizon_days == horizon,
                ConceptExposureRecord.as_of_date == current_market_date,
            )).scalar_one())
        if refresh_if_empty:
            # Fill every source node belonging to the canonical theme. One
            # provider alone cannot form market consensus.
            refreshed_source = False
            for raw_theme in raw_themes[:8]:
                if int(member_counts.get(raw_theme.id, 0)) > 0:
                    continue
                try:
                    self.refresh_theme(raw_theme.id, calculate=False)
                    refreshed_source = True
                except Exception as exc:  # One unavailable source must not hide the usable sources.
                    logger.warning("concept source node refresh failed: %s %s", raw_theme.id, exc)
            if exposure_total == 0 or refreshed_source:
                self.calculate_canonical_exposures(canonical, horizon_days=horizon)
        with self._read_scope() as session:
            raw_themes = session.execute(
                select(ConceptThemeRecord).where(ConceptThemeRecord.canonical_name == canonical)
                .order_by(desc(ConceptThemeRecord.heat_score))
            ).scalars().all()
            theme_ids = [row.id for row in raw_themes]
            memberships = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptMembershipRecord.theme_id.in_(theme_ids), ConceptMembershipRecord.active.is_(True))
            ).all()
            latest_exposure_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.canonical_name == canonical,
                ConceptExposureRecord.horizon_days == horizon,
            )).scalar_one_or_none()
            exposures = session.execute(
                select(ConceptExposureRecord).where(
                    ConceptExposureRecord.canonical_name == canonical,
                    ConceptExposureRecord.horizon_days == horizon,
                    ConceptExposureRecord.as_of_date == latest_exposure_date,
                )
                .order_by(desc(ConceptExposureRecord.weight_score)).limit(800)
            ).scalars().all()
        exposure_by_stock = {row.ts_code: row for row in exposures}
        stocks: Dict[str, Dict[str, Any]] = {}
        for membership, raw_theme in memberships:
            item = stocks.setdefault(membership.ts_code, {
                "ts_code": membership.ts_code, "name": membership.stock_name, "sources": [], "reasons": [],
                "source_count": 0, "weight_score": 0.0, "beta": None, "alpha_annualized": None,
                "r_squared": None, "confidence": "insufficient",
            })
            if raw_theme.source not in item["sources"]:
                item["sources"].append(raw_theme.source)
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
        for code, item in stocks.items():
            item["source_count"] = _independent_source_count(item["sources"])
            exposure = exposure_by_stock.get(code)
            if exposure:
                item.update(self._exposure_dict(exposure))
            item["reasons"] = item["reasons"][:3]
        ordered = sorted(stocks.values(), key=lambda value: (-value["weight_score"], -value["source_count"], value["name"]))
        consensus_distribution = {
            "strong": sum(1 for item in ordered if item["source_count"] >= 3),
            "confirmed": sum(1 for item in ordered if item["source_count"] == 2),
            "single_source": sum(1 for item in ordered if item["source_count"] == 1),
        }
        return {
            "theme": {**self._theme_dict(raw_themes[0]), "canonical_name": canonical} if raw_themes else {"id": theme_id},
            "source_nodes": [self._theme_dict(row) for row in raw_themes],
            "stocks": ordered,
            "total_stocks": len(ordered),
            "consensus_stocks": sum(1 for item in ordered if item["source_count"] >= 2),
            "consensus_distribution": consensus_distribution,
            "attribution_ready": sum(1 for item in ordered if item["beta"] is not None),
            "institution_corpus": self._theme_corpus_consensus(canonical),
            "related_themes": self._related_theme_affinity(canonical, list(stocks)),
            "horizon_days": horizon,
            "methodology": self.methodology(),
        }

    def _related_theme_affinity(self, canonical_name: str, stock_codes: Sequence[str], *, limit: int = 12) -> Dict[str, Any]:
        """Measure real constituent overlap with adjacent canonical themes."""
        codes = [code for code in dict.fromkeys(stock_codes) if code]
        if not codes:
            return {"items": [], "method": "当前题材没有可用于关系计算的有效成分。"}
        with self._read_scope() as session:
            target_type = session.execute(select(func.max(ConceptThemeRecord.theme_type)).where(
                ConceptThemeRecord.canonical_name == canonical_name,
            )).scalar_one_or_none()
            shared_rows = session.execute(select(
                ConceptThemeRecord.canonical_name,
                func.max(ConceptThemeRecord.theme_type).label("theme_type"),
                func.count(func.distinct(ConceptMembershipRecord.ts_code)).label("shared"),
            ).join(
                ConceptMembershipRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptMembershipRecord.active.is_(True),
                ConceptMembershipRecord.ts_code.in_(codes),
                ConceptThemeRecord.canonical_name != canonical_name,
            ).group_by(
                ConceptThemeRecord.canonical_name,
            ).order_by(desc("shared")).limit(max(24, int(limit) * 4))).all()
            candidates = [name for name, _, _ in shared_rows]
            total_rows = session.execute(select(
                ConceptThemeRecord.canonical_name,
                func.count(func.distinct(ConceptMembershipRecord.ts_code)),
            ).join(
                ConceptMembershipRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptMembershipRecord.active.is_(True),
                ConceptThemeRecord.canonical_name.in_(candidates),
            ).group_by(ConceptThemeRecord.canonical_name)).all() if candidates else []
        totals = {name: int(total or 0) for name, total in total_rows}
        target_total = len(codes)
        items = []
        for name, related_type, shared_value in shared_rows:
            shared = int(shared_value or 0)
            other_total = totals.get(name, 0)
            if shared < 2 or other_total <= 0:
                continue
            union = target_total + other_total - shared
            family = theme_family(name, str(related_type or "theme"))
            related_cluster = theme_cluster(name, family)
            target_family = theme_family(canonical_name, str(target_type or "theme"))
            target_cluster = theme_cluster(canonical_name, target_family)
            jaccard_pct = round(shared / max(1, union) * 100, 1)
            relation_type = (
                "高度重叠" if related_cluster == target_cluster and jaccard_pct >= 35
                else "同主题簇" if related_cluster == target_cluster
                else "同题材家族" if family == target_family
                else "跨题材共现"
            )
            items.append({
                "canonical_name": name,
                "family": family,
                "cluster": related_cluster,
                "relation_type": relation_type,
                "shared_stocks": shared,
                "target_coverage_pct": round(shared / max(1, target_total) * 100, 1),
                "jaccard_pct": jaccard_pct,
                "target_exclusive_stocks": max(0, target_total - shared),
                "other_total_stocks": other_total,
            })
        items.sort(key=lambda value: (-value["jaccard_pct"], -value["shared_stocks"], value["canonical_name"]))
        return {
            "items": items[:max(4, min(int(limit), 20))],
            "target_total_stocks": target_total,
            "method": "按当前有效成分计算真实交集与 Jaccard；相近不等于因果，也不把同产业链标签自动合并为同一题材。",
        }

    def _theme_corpus_consensus(self, canonical_name: str, *, days: int = 90) -> Dict[str, Any]:
        """Summarize AI-structured institution notes without calling them market facts."""
        tokens = [item.strip() for item in re.split(r"[/、|]", canonical_name) if len(item.strip()) >= 2]
        tokens = list(dict.fromkeys([canonical_name, *tokens]))[:6]
        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.extend((
                EssayAnalysisRecord.themes_json.like(pattern), EssayAnalysisRecord.tags_json.like(pattern),
                EssayAnalysisRecord.industries_json.like(pattern),
            ))
        if not clauses:
            return {"total": 0, "bullish": 0, "bearish": 0, "neutral": 0, "score": 0, "items": []}
        cutoff = utc_naive_now() - timedelta(days=max(30, min(int(days), 365)))
        with self._read_scope() as session:
            rows = session.execute(select(
                EssayAnalysisRecord.topic_id, EssayAnalysisRecord.sentiment,
                EssayAnalysisRecord.importance_score, EssayAnalysisRecord.confidence_score,
                EssayAnalysisRecord.summary, EssayAnalysisRecord.model,
                ResearchNote.title, ResearchNote.created_at,
            ).join(
                ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id,
            ).where(
                EssayAnalysisRecord.status == "completed", ResearchNote.created_at >= cutoff, or_(*clauses),
            ).order_by(desc(ResearchNote.created_at), desc(EssayAnalysisRecord.importance_score)).limit(600)).all()
        counts = {"bullish": 0, "bearish": 0, "neutral": 0}
        weighted_sum = total_weight = 0.0
        recent_cutoff = utc_naive_now() - timedelta(days=14)
        prior_cutoff = utc_naive_now() - timedelta(days=28)
        recent = prior = 0
        items = []
        for topic_id, sentiment, importance, confidence, summary, model, title, created_at in rows:
            raw = str(sentiment or "neutral").lower()
            tone = "bullish" if raw in {"bullish", "positive", "看多", "利多", "正面"} else "bearish" if raw in {"bearish", "negative", "看空", "利空", "负面"} else "neutral"
            counts[tone] += 1
            weight = max(.1, float(confidence or .5)) * max(20.0, float(importance or 50.0))
            weighted_sum += (1 if tone == "bullish" else -1 if tone == "bearish" else 0) * weight
            total_weight += weight
            if created_at and created_at >= recent_cutoff:
                recent += 1
            elif created_at and created_at >= prior_cutoff:
                prior += 1
            if len(items) < 6:
                items.append({
                    "topic_id": topic_id, "title": title or canonical_name,
                    "summary": str(summary or "")[:220], "sentiment": tone,
                    "importance": int(importance or 0), "confidence": round(float(confidence or 0), 2),
                    "model": model, "created_at": created_at.isoformat() if created_at else None,
                    "url": f"/essay-radar/feed?topic={topic_id}",
                })
        return {
            "total": len(rows), **counts,
            "score": round(weighted_sum / total_weight * 100, 1) if total_weight else 0.0,
            "recent_14d": recent, "prior_window": prior,
            "volume_change_pct": round((recent / prior - 1) * 100, 1) if prior else None,
            "truncated": len(rows) >= 600,
            "window_days": max(30, min(int(days), 365)), "items": items,
            "method": "只统计AI已结构化且主题字段明确命中的机构段子；情绪是语料观点，不等于事实或投资建议。",
        }

    def institution_theme_radar(self, *, days: int = 30, limit: int = 16) -> Dict[str, Any]:
        """Discover emerging narratives in the user's institution corpus.

        AI-derived topics never become provider consensus automatically. They
        are exposed as a separate candidate layer and carry their note, stock
        and provider evidence so a researcher can decide whether to promote or
        reject them.
        """
        window = max(7, min(int(days), 90))
        output_limit = max(8, min(int(limit), 40))
        cutoff = utc_naive_now() - timedelta(days=window)
        recent_cutoff = utc_naive_now() - timedelta(days=min(7, window))
        with self._read_scope() as session:
            provider_rows = session.execute(select(
                ConceptThemeRecord.canonical_name, ConceptThemeRecord.source,
            )).all()
            stock_identity_rows = session.execute(select(
                ConceptMembershipRecord.stock_name, ConceptMembershipRecord.ts_code,
            ).where(
                ConceptMembershipRecord.active.is_(True),
                ConceptMembershipRecord.stock_name.is_not(None),
            ).distinct()).all()
            rows = session.execute(select(ResearchNote, EssayAnalysisRecord).join(
                EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id,
            ).where(
                ResearchNote.created_at >= cutoff,
                EssayAnalysisRecord.status == "completed",
            ).order_by(desc(ResearchNote.created_at)).limit(5000)).all()
        provider_sources: Dict[str, set[str]] = defaultdict(set)
        for canonical_name, source_name in provider_rows:
            provider_sources[canonicalize_theme(str(canonical_name or ""))].add(str(source_name or ""))
        stock_code_by_name = {
            str(stock_name).strip(): _ts_code(ts_code)
            for stock_name, ts_code in stock_identity_rows if str(stock_name or "").strip() and _ts_code(ts_code)
        }
        stock_name_by_code = {
            _ts_code(ts_code): str(stock_name).strip()
            for stock_name, ts_code in stock_identity_rows if str(stock_name or "").strip() and _ts_code(ts_code)
        }
        ignored = {"信息不足", "其他", "其他主题", "无", "未知", "行业", "公司", "股票", "市场"}
        stats: Dict[str, Dict[str, Any]] = {}
        for note, analysis in rows:
            created_at = note.created_at or utc_naive_now()
            raw_themes = _json(analysis.themes_json, [])
            if not isinstance(raw_themes, list):
                continue
            themes = [canonicalize_theme(str(value).strip()) for value in raw_themes if str(value).strip()]
            mentions = _json(analysis.stock_mentions_json, [])
            note_stocks: List[Tuple[str, str]] = []
            for mention in mentions if isinstance(mentions, list) else []:
                if not isinstance(mention, dict):
                    continue
                if float(mention.get("confidence") or .5) < .35:
                    continue
                code = _ts_code(mention.get("ts_code") or mention.get("code"))
                name = str(mention.get("name") or code).strip()
                code = code or stock_code_by_name.get(name, "")
                name = stock_name_by_code.get(code, name)
                if code or name:
                    note_stocks.append((code, name))
            for canonical_name in dict.fromkeys(themes):
                if canonical_name in ignored or not 2 <= len(canonical_name) <= 36:
                    continue
                item = stats.setdefault(canonical_name, {
                    "canonical_name": canonical_name, "note_ids": set(), "recent_7d": 0,
                    "prior_count": 0, "importance_sum": 0.0, "bullish": 0, "bearish": 0,
                    "neutral": 0, "stocks": defaultdict(int), "latest_at": None, "samples": [],
                })
                if note.topic_id in item["note_ids"]:
                    continue
                item["note_ids"].add(note.topic_id)
                if created_at >= recent_cutoff:
                    item["recent_7d"] += 1
                else:
                    item["prior_count"] += 1
                sentiment = str(analysis.sentiment or "neutral").lower()
                sentiment_key = sentiment if sentiment in {"bullish", "bearish"} else "neutral"
                item[sentiment_key] += 1
                importance = float(analysis.importance_score or 50)
                confidence = float(analysis.confidence_score or .5)
                item["importance_sum"] += importance * max(.2, min(confidence, 1.0))
                for stock in note_stocks:
                    item["stocks"][stock] += 1
                if item["latest_at"] is None or created_at > item["latest_at"]:
                    item["latest_at"] = created_at
                if len(item["samples"]) < 3:
                    item["samples"].append({
                        "title": str(note.title or "")[:100],
                        "topic_id": note.topic_id,
                        "date": created_at.isoformat(),
                        "url": f"/essay-radar/feed?topic={note.topic_id}",
                    })
        items = []
        for canonical_name, item in stats.items():
            note_count = len(item["note_ids"])
            if note_count < 2:
                continue
            sources = provider_sources.get(canonical_name, set())
            provider_count = _independent_source_count(sources)
            recent = int(item["recent_7d"])
            baseline_days = max(1, window - min(7, window))
            prior_week_equivalent = float(item["prior_count"]) / baseline_days * 7
            acceleration = ((recent - prior_week_equivalent) / max(1.0, prior_week_equivalent)) * 100
            average_evidence = float(item["importance_sum"]) / note_count
            concentration_score = min(20.0, recent / max(1, note_count) * 30)
            acceleration_score = max(-5.0, min(10.0, acceleration / 100))
            discovery_score = min(100.0,
                math.log1p(note_count) * 7 + concentration_score
                + min(provider_count, 3) * 4 + min(14.0, average_evidence * .18)
                + acceleration_score
            )
            ranked_stocks = sorted(item["stocks"].items(), key=lambda value: (-value[1], value[0]))[:6]
            items.append({
                "canonical_name": canonical_name,
                "status": "provider_consensus" if provider_count >= 2 else "provider_single" if provider_count == 1 else "corpus_candidate",
                "provider_count": provider_count,
                "provider_sources": sorted(sources),
                "note_count": note_count,
                "recent_7d": recent,
                "acceleration_pct": round(acceleration, 1) if prior_week_equivalent >= 1 else None,
                "baseline_week": round(prior_week_equivalent, 1),
                "discovery_score": round(discovery_score, 1),
                "sentiment": {"bullish": item["bullish"], "neutral": item["neutral"], "bearish": item["bearish"]},
                "stocks": [{"ts_code": code, "name": name, "mentions": count} for (code, name), count in ranked_stocks],
                "latest_at": item["latest_at"].isoformat() if item["latest_at"] else None,
                "samples": item["samples"],
            })
        items.sort(key=lambda value: (-value["discovery_score"], -value["recent_7d"], -value["note_count"], value["canonical_name"]))
        return {
            "items": items[:output_limit], "total_candidates": len(items), "window_days": window,
            "as_of_at": max((item["latest_at"] for item in items if item["latest_at"]), default=None),
            "method": "近窗机构段子 AI 主题与明确股票提及聚合；供应商未确认的主题只进入候选层，不自动计入市场共识或题材权重。",
        }

    def theme_lifecycle(self, *, days: int = 30, limit: int = 12) -> Dict[str, Any]:
        """Join provider rotation and AI-structured institution corpus by semantic cluster.

        This is deliberately rule based and descriptive: it only labels a stage
        when a real market cluster exists on both sides. Corpus-only candidates
        remain in the discovery radar and never become market consensus here.
        """
        window = max(14, min(int(days), 60))
        row_limit = max(4, min(int(limit), 20))
        rotation = self.rotation(days=min(60, window), limit=60)
        corpus = self.institution_theme_radar(days=window, limit=40)
        market_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        corpus_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

        def lifecycle_key(canonical_name: str, family: str, cluster: str) -> Tuple[str, str]:
            # Broad family buckets such as "医药健康" or "消费与品牌" are
            # navigation aids, not proof that two narratives are the same.
            # Only a specific industry cluster or an exact canonical name may
            # join market and corpus evidence.
            if cluster and cluster != family and cluster != "其他市场题材":
                return ("cluster", f"{family}\x1f{cluster}")
            return ("exact", canonicalize_theme(canonical_name))

        for item in rotation["items"]:
            family = str(item.get("family") or "")
            cluster = str(item.get("cluster") or "")
            canonical_name = str(item.get("canonical_name") or "")
            if family in NON_NARRATIVE_FAMILIES or not canonical_name:
                continue
            market_groups[lifecycle_key(canonical_name, family, cluster)].append(item)
        for item in corpus["items"]:
            canonical_name = str(item.get("canonical_name") or "")
            family = theme_family(canonical_name, "theme")
            cluster = theme_cluster(canonical_name, family)
            if family in NON_NARRATIVE_FAMILIES or not canonical_name:
                continue
            corpus_groups[lifecycle_key(canonical_name, family, cluster)].append(item)
        items: List[Dict[str, Any]] = []
        for match_key in sorted(set(market_groups) & set(corpus_groups)):
            market_items = sorted(market_groups[match_key], key=lambda value: -float(value.get("rotation_score") or 0))
            corpus_items = sorted(corpus_groups[match_key], key=lambda value: -float(value.get("discovery_score") or 0))
            representative = market_items[0]
            family = str(representative.get("family") or "")
            cluster = str(representative.get("cluster") or "") if match_key[0] == "cluster" else str(representative.get("canonical_name") or "")
            momentum = float(representative.get("momentum_5d") or 0)
            recent = sum(int(value.get("recent_7d") or 0) for value in corpus_items)
            note_count = sum(int(value.get("note_count") or 0) for value in corpus_items)
            acceleration_values = [
                (float(value["acceleration_pct"]), max(1, int(value.get("note_count") or 0)))
                for value in corpus_items if value.get("acceleration_pct") is not None
            ]
            acceleration = (
                sum(value * weight for value, weight in acceleration_values) / sum(weight for _, weight in acceleration_values)
                if acceleration_values else None
            )
            if acceleration is not None and acceleration >= 25 and momentum > 0:
                stage, interpretation = "共识扩张", "机构讨论加速且价格窗口同步转强，下一步核验成分扩散与成交确认。"
            elif acceleration is not None and acceleration >= 25:
                stage, interpretation = "语料先行", "机构讨论先加速但价格窗口尚未确认，只作为待验证线索。"
            elif momentum > 0 and recent <= 2:
                stage, interpretation = "价格驱动", "价格窗口走强但近期机构语料较少，需排查事件或交易性因素。"
            elif momentum < 0 and recent >= 3:
                stage, interpretation = "分歧退潮", "讨论仍有密度但价格窗口转弱，重点核验预期是否已被交易。"
            else:
                stage, interpretation = "交叉观察", "市场与语料已有真实交集，但尚不足以归入更明确阶段。"
            lifecycle_score = max(0.0, min(100.0,
                float(representative.get("rotation_score") or 0) * .55
                + float(corpus_items[0].get("discovery_score") or 0) * .45
            ))
            items.append({
                "family": family, "cluster": cluster, "stage": stage,
                "score": round(lifecycle_score, 1),
                "market_date": representative.get("market_date"),
                "market_momentum_5d": round(momentum, 2),
                "market_change": representative.get("pct_change"),
                "market_source_count": max(int(value.get("source_count") or 0) for value in market_items),
                "corpus_notes": note_count, "corpus_recent_7d": recent,
                "corpus_acceleration_pct": round(acceleration, 1) if acceleration is not None else None,
                "market_themes": [value["canonical_name"] for value in market_items[:5]],
                "corpus_themes": [value["canonical_name"] for value in corpus_items[:5]],
                "interpretation": interpretation,
            })
        stage_priority = {"共识扩张": 5, "语料先行": 4, "分歧退潮": 3, "价格驱动": 2, "交叉观察": 1}
        items.sort(key=lambda value: (-stage_priority[value["stage"]], -value["score"], value["cluster"]))
        return {
            "items": items[:row_limit], "total": len(items), "window_days": window,
            "market_date": rotation.get("latest_date"), "corpus_as_of_at": corpus.get("as_of_at"),
            "method": "只在供应商市场题材与AI结构化机构语料名称精确一致，或共同落入明确产业簇时交叉分期；宽泛家族不参与拼接，阶段为可复核规则而非收益预测。",
        }

    def watchlist_theme_map(self, stock_codes: Iterable[str], *, horizon_days: int = 60) -> Dict[str, Any]:
        """Aggregate user watchlist exposures without mixing users or dates."""
        codes = list(dict.fromkeys(_ts_code(code) for code in stock_codes if _ts_code(code)))
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        if not codes:
            return {"stocks": [], "themes": [], "stock_count": 0, "horizon_days": horizon, "as_of_date": None}
        with self._read_scope() as session:
            latest_dates = dict(session.execute(select(
                ConceptExposureRecord.ts_code, func.max(ConceptExposureRecord.as_of_date),
            ).where(
                ConceptExposureRecord.ts_code.in_(codes),
                ConceptExposureRecord.horizon_days == horizon,
            ).group_by(ConceptExposureRecord.ts_code)).all())
            filters = [and_(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.as_of_date == as_of,
            ) for code, as_of in latest_dates.items() if as_of]
            rows = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.horizon_days == horizon,
                or_(*filters) if filters else ConceptExposureRecord.id < 0,
            )).scalars().all()
        taxonomy = self._taxonomy_index()
        by_stock: Dict[str, List[ConceptExposureRecord]] = defaultdict(list)
        for row in rows:
            by_stock[row.ts_code].append(row)
        theme_groups: Dict[str, Dict[str, Any]] = {}
        stocks = []
        for code in codes:
            all_rows = sorted(by_stock.get(code, []), key=lambda row: -float(row.weight_score or 0))
            eligible = []
            for row in all_rows:
                family, cluster = self._semantic_cluster_for(row.canonical_name, taxonomy)
                if int(row.source_count or 0) < 2 or family in NON_NARRATIVE_FAMILIES:
                    continue
                eligible.append({**self._leader_exposure(row), "family": family, "cluster": cluster})
            overlap = self._overlap_profile(eligible)
            representatives = overlap["representatives"]
            stock_name = next((row.stock_name for row in all_rows if row.stock_name), code)
            stocks.append({
                "ts_code": code, "name": stock_name,
                "as_of_date": latest_dates.get(code).isoformat() if latest_dates.get(code) else None,
                "raw_theme_count": len(eligible),
                "independent_cluster_count": overlap["independent_cluster_count"],
                "overlap_rate": overlap["overlap_rate"],
                "dominant_theme": representatives[0] if representatives else None,
                "themes": representatives[:5],
            })
            for exposure in representatives:
                cluster_name = str(exposure.get("cluster") or exposure.get("canonical_name") or "其他")
                group = theme_groups.setdefault(cluster_name, {
                    "cluster": cluster_name, "family": exposure.get("family"), "stocks": [],
                    "weights": [], "themes": set(),
                })
                group["stocks"].append({"ts_code": code, "name": stock_name})
                group["weights"].append(float(exposure.get("weight_score") or 0))
                group["themes"].add(str(exposure.get("canonical_name") or ""))
        theme_items = [{
            "cluster": group["cluster"], "family": group["family"],
            "stock_count": len(group["stocks"]), "stocks": group["stocks"],
            "average_weight": round(statistics.mean(group["weights"]), 1) if group["weights"] else 0.0,
            "themes": sorted(value for value in group["themes"] if value)[:6],
        } for group in theme_groups.values()]
        theme_items.sort(key=lambda item: (-item["stock_count"], -item["average_weight"], item["cluster"]))
        stocks.sort(key=lambda item: (-item["independent_cluster_count"], item["name"]))
        latest_watchlist_date = max((value for value in latest_dates.values() if value), default=None)
        covered_stocks = [item for item in stocks if int(item["independent_cluster_count"] or 0) > 0]
        top_theme = theme_items[0] if theme_items else None
        top_coverage = round(
            float(top_theme["stock_count"]) / max(1, len(stocks)) * 100, 1,
        ) if top_theme else 0.0
        concentration_level = (
            "高" if len(stocks) >= 2 and top_coverage >= 67
            else "中" if len(stocks) >= 2 and top_coverage >= 40
            else "低"
        )
        average_clusters = round(statistics.mean(
            [float(item["independent_cluster_count"] or 0) for item in stocks]
        ), 1) if stocks else 0.0
        top_cluster = str(top_theme["cluster"]) if top_theme else ""
        divergent_stocks = [
            {"ts_code": item["ts_code"], "name": item["name"]}
            for item in stocks
            if str((item.get("dominant_theme") or {}).get("cluster") or "") != top_cluster
        ] if top_cluster else []
        return {
            "stocks": stocks, "themes": theme_items[:12], "stock_count": len(stocks),
            "horizon_days": horizon,
            "as_of_date": latest_watchlist_date.isoformat() if latest_watchlist_date else None,
            "concentration": {
                "level": concentration_level,
                "top_cluster": top_cluster or None,
                "top_coverage_pct": top_coverage,
                "shared_cluster_count": sum(1 for item in theme_items if int(item["stock_count"]) >= 2),
                "covered_stock_count": len(covered_stocks),
                "average_cluster_count": average_clusters,
                "divergent_stocks": divergent_stocks,
                "interpretation": (
                    f"{top_cluster}覆盖{int(top_theme['stock_count'])}/{len(stocks)}只自选股；这是等股票数量的题材暴露，不代表持仓市值权重。"
                    if top_theme else "当前自选股尚无达到多源门槛的业务题材归因。"
                ),
            },
            "method": "只聚合当前用户自选股在各自最新归因日的多源业务主线；相近标签按独立语义簇去重，地域、风格和宽基不计入集中度。集中度按股票数量等权，不读取或推断持仓金额。",
        }

    def membership_change_ledger(self, *, days: int = 7, limit: int = 24) -> Dict[str, Any]:
        """Show source membership changes without mislabeling baseline imports as market events."""
        window = max(1, min(int(days), 30))
        output_limit = max(8, min(int(limit), 60))
        cutoff = utc_naive_now() - timedelta(days=window)
        candidate_limit = max(800, min(6_000, output_limit * 120))
        with self._read_scope() as session:
            rows = session.execute(select(
                ConceptMembershipRecord, ConceptThemeRecord,
            ).join(
                ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(or_(
                ConceptMembershipRecord.first_seen_at >= cutoff,
                and_(
                    ConceptMembershipRecord.active.is_(False),
                    ConceptMembershipRecord.updated_at >= cutoff,
                ),
            )).order_by(desc(ConceptMembershipRecord.updated_at)).limit(candidate_limit)).all()
        taxonomy = self._taxonomy_index()
        grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        baseline_ignored = 0
        for membership, theme in rows:
            state = "removed" if not membership.active and membership.updated_at >= cutoff else "added"
            event_at = membership.updated_at if state == "removed" else membership.first_seen_at
            # A constituent captured shortly after a new source theme is first
            # created is the initial baseline, not evidence of a market change.
            if state == "added" and abs((membership.first_seen_at - theme.first_seen_at).total_seconds()) < 6 * 3600:
                baseline_ignored += 1
                continue
            family, cluster = self._semantic_cluster_for(theme.canonical_name, taxonomy)
            key = (state, membership.ts_code, theme.canonical_name)
            item = grouped.setdefault(key, {
                "state": state,
                "ts_code": membership.ts_code,
                "name": membership.stock_name or membership.ts_code,
                "canonical_name": theme.canonical_name,
                "family": family,
                "cluster": cluster,
                "sources": set(),
                "event_at": event_at,
                "market_dates": set(),
                "reasons": [],
            })
            item["sources"].add(membership.source)
            if membership.market_date:
                item["market_dates"].add(membership.market_date.isoformat())
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
            if event_at > item["event_at"]:
                item["event_at"] = event_at
        items = []
        for item in grouped.values():
            sources = sorted(item.pop("sources"))
            dates = sorted(item.pop("market_dates"), reverse=True)
            item.update({
                "sources": sources,
                "source_count": _independent_source_count(sources),
                "event_at": item["event_at"].isoformat(),
                "market_date": dates[0] if dates else None,
                "reasons": item["reasons"][:3],
            })
            items.append(item)
        items.sort(key=lambda item: (int(item["source_count"]), str(item["event_at"])), reverse=True)
        return {
            "items": items[:output_limit],
            "added": sum(1 for item in items if item["state"] == "added"),
            "removed": sum(1 for item in items if item["state"] == "removed"),
            "baseline_ignored": baseline_ignored,
            "window_days": window,
            "cutoff_at": cutoff.isoformat(),
            "method": "只显示旧题材目录后续捕获的新增/退出成分；题材首次建库六小时内的基线成分不计作市场变化。时间表示平台供应商成分入库时间，不等于公司公告日。",
        }

    def stock_lens(self, ts_code: str, *, refresh_if_empty: bool = True, horizon_days: int = 60) -> Dict[str, Any]:
        code = _ts_code(ts_code)
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        with self._read_scope() as session:
            count = int(session.execute(select(func.count(ConceptMembershipRecord.id)).where(
                ConceptMembershipRecord.ts_code == code, ConceptMembershipRecord.active.is_(True)
            )).scalar_one())
        if count == 0 and refresh_if_empty:
            self.refresh_stock(code, calculate=True)
        with self._read_scope() as session:
            rows = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptMembershipRecord.ts_code == code, ConceptMembershipRecord.active.is_(True))
            ).all()
            latest_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.horizon_days == horizon,
            )).scalar_one_or_none()
            exposures = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.as_of_date == latest_date if latest_date else ConceptExposureRecord.id < 0,
                ConceptExposureRecord.horizon_days == horizon,
            ).order_by(desc(ConceptExposureRecord.weight_score))).scalars().all()
            latest_by_horizon = dict(session.execute(select(
                ConceptExposureRecord.horizon_days, func.max(ConceptExposureRecord.as_of_date),
            ).where(
                ConceptExposureRecord.ts_code == code,
                ConceptExposureRecord.horizon_days.in_((20, 60, 120)),
            ).group_by(ConceptExposureRecord.horizon_days)).all())
            profile_filters = [and_(
                ConceptExposureRecord.horizon_days == window,
                ConceptExposureRecord.as_of_date == profile_date,
            ) for window, profile_date in latest_by_horizon.items() if profile_date]
            profile_rows = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.ts_code == code,
                or_(*profile_filters) if profile_filters else ConceptExposureRecord.id < 0,
            )).scalars().all()
        grouped: Dict[str, Dict[str, Any]] = {}
        stock_name = ""
        for membership, theme in rows:
            stock_name = stock_name or membership.stock_name
            family_name = theme_family(theme.canonical_name, theme.theme_type)
            item = grouped.setdefault(theme.canonical_name, {
                "canonical_name": theme.canonical_name, "family": family_name,
                "cluster": theme_cluster(theme.canonical_name, family_name),
                "theme_type": theme.theme_type, "sources": [], "reasons": [], "theme_ids": [],
            })
            if theme.source not in item["sources"]:
                item["sources"].append(theme.source)
            item["theme_ids"].append(theme.id)
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
        exposure_map = {row.canonical_name: row for row in exposures}
        profile_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in profile_rows:
            profile_map[row.canonical_name].append({
                "horizon_days": int(row.horizon_days),
                "as_of_date": row.as_of_date.isoformat(),
                "beta": row.beta,
                "residual_return": row.residual_return,
                "r_squared": row.r_squared,
                "observations": int(row.observations or 0),
                "confidence": row.confidence,
            })
        for values in profile_map.values():
            values.sort(key=lambda item: item["horizon_days"])
        themes = []
        for canonical, item in grouped.items():
            exposure = exposure_map.get(canonical)
            values = {**item, "source_count": _independent_source_count(item["sources"]), "reasons": item["reasons"][:4]}
            if exposure:
                values.update(self._exposure_dict(exposure))
            else:
                values.update({"weight_score": 0.0, "beta": None, "alpha_annualized": None,
                               "r_squared": None, "confidence": "insufficient"})
            profiles = profile_map.get(canonical, [])
            valid_betas = [float(item["beta"]) for item in profiles
                           if item["beta"] is not None and item["confidence"] in {"medium", "high"}]
            if len(valid_betas) < 2:
                stability = "insufficient"
            elif max(valid_betas) - min(valid_betas) <= 0.35:
                stability = "stable"
            else:
                stability = "shifting"
            values["horizon_profile"] = profiles
            values["beta_stability"] = stability
            themes.append(values)
        themes.sort(key=lambda value: (-value["weight_score"], -value["source_count"]))
        consensus_themes = [item for item in themes if item["source_count"] >= 2]
        narrative_consensus = [item for item in consensus_themes
                               if item.get("theme_type") not in {"region", "style", "feature", "broad"}
                               and item.get("family") not in NON_NARRATIVE_FAMILIES]
        overlap_profile = self._overlap_profile(narrative_consensus or consensus_themes)
        primary_themes = list(overlap_profile.pop("representatives"))[:5]
        unique_themes = sorted([
            item for item in themes
            if item["source_count"] == 1
            and (item.get("specificity_score") or 0) >= 50
            and (item.get("weight_score") or 0) > 0
        ], key=lambda value: (
            -(value.get("specificity_score") or 0),
            -(value.get("residual_return") or -999),
        ))[:6]
        drivers = self._unique_driver_evidence(code, stock_name)
        driver_categories: Dict[str, int] = defaultdict(int)
        driver_directions: Dict[str, int] = defaultdict(int)
        for driver in drivers:
            driver_categories[str(driver.get("category") or "其他公司事实")] += 1
            driver_directions[str(driver.get("direction") or "neutral")] += 1
        return {
            "ts_code": code, "name": stock_name, "as_of_date": latest_date.isoformat() if latest_date else None,
            "themes": themes, "primary_themes": primary_themes, "unique_themes": unique_themes,
            "overlap_audit": overlap_profile,
            "unique_drivers": drivers,
            "unique_driver_summary": {
                "categories": dict(sorted(driver_categories.items(), key=lambda item: (-item[1], item[0]))),
                "directions": dict(driver_directions),
                "method": "按公司事实标题与摘要做可审计语义归类；方向只描述证据措辞，不等于股价方向或因果 Alpha。",
            },
            "horizon_days": horizon,
            "summary": {
                "theme_count": len(themes), "source_count": _independent_source_count(
                    source for item in themes for source in item["sources"]
                ),
                "consensus_count": sum(1 for item in themes if item["source_count"] >= 2),
                "independent_cluster_count": overlap_profile["independent_cluster_count"],
                "theme_overlap_rate": overlap_profile["overlap_rate"],
                "alpha_positive_count": sum(1 for item in themes if (item.get("alpha_annualized") or 0) > 0),
                "stable_beta_count": sum(1 for item in themes if item.get("beta_stability") == "stable"),
                "persistent_alpha_count": sum(1 for item in themes if sum(
                    1 for point in item.get("horizon_profile", [])
                    if point.get("residual_return") is not None and float(point["residual_return"]) > 0
                    and point.get("confidence") in {"medium", "high"}
                ) >= 2),
            },
            "methodology": self.methodology(),
        }

    def market_consensus_leaders(
        self, *, horizon_days: int = 60, limit: int = 24, mode: str = "consensus",
    ) -> Dict[str, Any]:
        """Rank stocks across themes without turning one noisy exposure into a signal.

        The radar only uses the latest completed attribution snapshot for the
        selected horizon. Consensus ranking requires at least two independent
        source catalogs; alpha/beta rankings additionally require a usable
        regression confidence level.
        """
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        mode = mode if mode in {"consensus", "alpha", "beta", "specificity"} else "consensus"
        limit = max(8, min(int(limit), 60))
        taxonomy = self._taxonomy_index()
        with self._read_scope() as session:
            latest_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.horizon_days == horizon,
            )).scalar_one_or_none()
            rows = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.horizon_days == horizon,
                ConceptExposureRecord.as_of_date == latest_date if latest_date else ConceptExposureRecord.id < 0,
                ConceptExposureRecord.weight_score >= 35,
            )).scalars().all()
        grouped: Dict[str, List[ConceptExposureRecord]] = defaultdict(list)
        for row in rows:
            grouped[row.ts_code].append(row)
        items: List[Dict[str, Any]] = []
        for code, values in grouped.items():
            values.sort(key=lambda row: (-float(row.weight_score or 0), -int(row.source_count or 0)))
            consensus = [row for row in values if int(row.source_count or 0) >= 2]
            valid = [row for row in values if row.confidence in {"medium", "high"} and row.beta is not None]
            valid_consensus = [row for row in valid if int(row.source_count or 0) >= 2]
            if not consensus and mode != "specificity":
                continue
            cluster_candidates = consensus or values
            narrative_candidates = []
            for row in cluster_candidates:
                family_name, _cluster_name = self._semantic_cluster_for(row.canonical_name, taxonomy)
                if (taxonomy.get("type_by_canonical", {}).get(row.canonical_name, "concept")
                        not in {"region", "style", "feature", "broad"}
                        and family_name not in NON_NARRATIVE_FAMILIES):
                    narrative_candidates.append(row)
            cluster_candidates = narrative_candidates or cluster_candidates
            overlap_input = []
            for row in cluster_candidates:
                family_name, cluster_name = self._semantic_cluster_for(row.canonical_name, taxonomy)
                overlap_input.append({
                    "canonical_name": row.canonical_name, "family": family_name,
                    "cluster": cluster_name, "weight_score": float(row.weight_score or 0),
                })
            overlap_profile = self._overlap_profile(overlap_input)
            row_by_name = {row.canonical_name: row for row in values}
            primary = [row_by_name[item["canonical_name"]] for item in overlap_profile.pop("representatives")[:4]]
            beta_focus = max(valid_consensus or valid, key=lambda row: abs(float(row.beta or 0)), default=None)
            alpha_focus = max(valid_consensus or valid, key=lambda row: float(row.residual_return or -999), default=None)
            divergence_focus = min(valid_consensus or valid, key=lambda row: float(row.residual_return or 999), default=None)
            specificity_focus = max(values, key=lambda row: float(row.specificity_score or 0), default=None)
            top_weights = [float(row.weight_score or 0) for row in primary]
            average_weight = statistics.mean(top_weights) if top_weights else 0.0
            consensus_count = len(consensus)
            representative_names = {row.canonical_name for row in primary}
            positive_alpha_count = sum(1 for row in valid_consensus
                                       if row.canonical_name in representative_names and float(row.residual_return or 0) > 0)
            source_breadth = max((int(row.source_count or 0) for row in values), default=0)
            independent_clusters = int(overlap_profile["independent_cluster_count"])
            score = min(100.0, max(0.0,
                average_weight * 0.55 + min(independent_clusters, 6) * 5.0
                + min(source_breadth, 6) * 2.5 - float(overlap_profile["overlap_rate"]) * 0.08
            ))
            if mode == "alpha" and alpha_focus is None:
                continue
            if mode == "beta" and beta_focus is None:
                continue
            item = {
                "ts_code": code,
                "name": values[0].stock_name or code,
                "as_of_date": latest_date.isoformat() if latest_date else None,
                "radar_score": round(score, 1),
                "total_theme_count": len(values),
                "consensus_theme_count": consensus_count,
                "independent_cluster_count": independent_clusters,
                "theme_overlap_rate": overlap_profile["overlap_rate"],
                "dominant_cluster": overlap_profile["dominant_cluster"],
                "dominant_cluster_share": overlap_profile["dominant_cluster_share"],
                "positive_alpha_count": positive_alpha_count,
                "source_breadth": source_breadth,
                "average_weight": round(average_weight, 1),
                "primary_themes": [self._leader_exposure(row) for row in primary],
                "beta_focus": self._leader_exposure(beta_focus) if beta_focus else None,
                "alpha_focus": self._leader_exposure(alpha_focus) if alpha_focus else None,
                "divergence_focus": self._leader_exposure(divergence_focus) if divergence_focus else None,
                "specificity_focus": self._leader_exposure(specificity_focus) if specificity_focus else None,
            }
            items.append(item)
        sort_key = {
            "alpha": lambda item: (-float((item["alpha_focus"] or {}).get("residual_return") or -999), -item["independent_cluster_count"], -item["radar_score"]),
            "beta": lambda item: (-abs(float((item["beta_focus"] or {}).get("beta") or 0)), -item["independent_cluster_count"], -item["radar_score"]),
            "specificity": lambda item: (-float((item["specificity_focus"] or {}).get("specificity_score") or 0), -item["radar_score"]),
            "consensus": lambda item: (-item["independent_cluster_count"], -item["radar_score"], -item["source_breadth"]),
        }[mode]
        items.sort(key=sort_key)
        return {
            "items": items[:limit], "total_candidates": len(items), "mode": mode,
            "horizon_days": horizon, "as_of_date": latest_date.isoformat() if latest_date else None,
            "method": "同一归因截止日聚合；共识榜按独立语义簇而非相近标签数量排序，Beta/Alpha榜还要求回归置信度达到中或高。",
        }

    def cluster_detail(
        self, family: str, cluster: str, *, horizon_days: int = 60, limit: int = 80,
    ) -> Dict[str, Any]:
        """Aggregate the stocks below a semantic cluster while preserving source themes."""
        family = str(family or "").strip()
        cluster = str(cluster or "").strip()
        if not family or not cluster:
            raise ConceptThemeError("题材家族和二级主题不能为空")
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        taxonomy = self._taxonomy_index()
        theme_ids = [theme_id for theme_id, names in taxonomy["by_id"].items() if names == (family, cluster)]
        if not theme_ids:
            raise ConceptThemeError("二级主题不存在")
        with self._read_scope() as session:
            rows = session.execute(select(ConceptMembershipRecord, ConceptThemeRecord).join(
                ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptMembershipRecord.theme_id.in_(theme_ids), ConceptMembershipRecord.active.is_(True),
            )).all()
            canonical_names = sorted({theme.canonical_name for _, theme in rows})
            latest_date = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                ConceptExposureRecord.horizon_days == horizon,
                ConceptExposureRecord.canonical_name.in_(canonical_names),
            )).scalar_one_or_none() if canonical_names else None
            exposures = session.execute(select(ConceptExposureRecord).where(
                ConceptExposureRecord.horizon_days == horizon,
                ConceptExposureRecord.as_of_date == latest_date if latest_date else ConceptExposureRecord.id < 0,
                ConceptExposureRecord.canonical_name.in_(canonical_names),
            )).scalars().all() if canonical_names else []
        exposure_map = {(row.ts_code, row.canonical_name): row for row in exposures}
        stocks: Dict[str, Dict[str, Any]] = {}
        for membership, theme in rows:
            item = stocks.setdefault(membership.ts_code, {
                "ts_code": membership.ts_code, "name": membership.stock_name or membership.ts_code,
                "sources": set(), "canonical_names": set(), "reasons": [], "exposures": [],
            })
            item["sources"].add(theme.source)
            item["canonical_names"].add(theme.canonical_name)
            exposure = exposure_map.get((membership.ts_code, theme.canonical_name))
            if exposure and exposure not in item["exposures"]:
                item["exposures"].append(exposure)
            if membership.reason and membership.reason not in item["reasons"]:
                item["reasons"].append(membership.reason)
        output = []
        for item in stocks.values():
            ranked_exposures = sorted(item["exposures"], key=lambda row: -float(row.weight_score or 0))
            weights = [float(row.weight_score or 0) for row in ranked_exposures[:3]]
            average_weight = statistics.mean(weights) if weights else 0.0
            source_count = _independent_source_count(item["sources"])
            theme_count = len(item["canonical_names"])
            dominant = ranked_exposures[0] if ranked_exposures else None
            cluster_score = min(100.0, average_weight * .65 + min(theme_count, 6) * 4 + min(source_count, 6) * 2.5)
            output.append({
                "ts_code": item["ts_code"], "name": item["name"],
                "cluster_score": round(cluster_score, 1), "theme_count": theme_count,
                "source_count": source_count, "sources": sorted(item["sources"]),
                "themes": sorted(item["canonical_names"]), "reasons": item["reasons"][:3],
                "dominant_exposure": self._leader_exposure(dominant),
            })
        output.sort(key=lambda item: (-item["cluster_score"], -item["source_count"], -item["theme_count"], item["name"]))
        return {
            "family": family, "cluster": cluster, "items": output[:max(12, min(int(limit), 200))],
            "total_stocks": len(output), "theme_nodes": len(theme_ids), "canonical_themes": len(canonical_names),
            "source_count": _independent_source_count(theme.source for _, theme in rows),
            "as_of_date": latest_date.isoformat() if latest_date else None, "horizon_days": horizon,
            "method": "聚合二级主题下的全部原始节点和成分关系；综合分用于研究排序，Beta/Alpha仅引用股票权重最高的代表题材，不冒充二级主题回归。",
        }

    @staticmethod
    def _leader_exposure(row: Optional[ConceptExposureRecord]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "canonical_name": row.canonical_name,
            "weight_score": round(float(row.weight_score or 0), 1),
            "source_count": int(row.source_count or 0),
            "beta": row.beta,
            "residual_return": row.residual_return,
            "specificity_score": row.specificity_score,
            "confidence": row.confidence,
        }

    def calculate_canonical_exposures(
        self, canonical_name: str, *, horizon_days: int = 60, only_stock: Optional[str] = None,
    ) -> int:
        horizon = max(20, min(int(horizon_days), 250))
        as_of = self.latest_market_date()
        start = as_of - timedelta(days=max(120, horizon * 3))
        with self._read_scope() as session:
            memberships = session.execute(
                select(ConceptMembershipRecord, ConceptThemeRecord)
                .join(ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id)
                .where(ConceptThemeRecord.canonical_name == canonical_name,
                       ConceptMembershipRecord.active.is_(True))
            ).all()
        if not memberships:
            return 0
        by_stock: Dict[str, Dict[str, Any]] = {}
        for membership, theme in memberships:
            item = by_stock.setdefault(membership.ts_code, {"name": membership.stock_name, "sources": set(),
                                                              "reasons": [], "theme_counts": []})
            item["sources"].add(theme.source)
            if membership.reason:
                item["reasons"].append(membership.reason)
            item["theme_counts"].append(max(1, theme.constituent_count or 0))
        codes = sorted(by_stock)
        if not codes:
            return 0
        storage_codes = [code[:6] for code in codes]
        with self._read_scope() as session:
            prices = session.execute(select(StockDaily.code, StockDaily.date, StockDaily.close).where(
                StockDaily.code.in_(storage_codes), StockDaily.date >= start, StockDaily.date <= as_of,
                StockDaily.close.is_not(None), StockDaily.close > 0,
            )).all()
            market_rows = session.execute(select(MarketIndexBar.timestamp, MarketIndexBar.close).where(
                MarketIndexBar.symbol == "000300.SH", MarketIndexBar.frequency == "1D",
                MarketIndexBar.timestamp >= datetime.combine(start, datetime.min.time()),
                MarketIndexBar.timestamp <= datetime.combine(as_of, datetime.max.time()),
            ).order_by(MarketIndexBar.timestamp)).all()
            theme_breadths = dict(session.execute(select(
                ConceptMembershipRecord.ts_code,
                func.count(func.distinct(ConceptThemeRecord.canonical_name)),
            ).join(
                ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
            ).where(
                ConceptMembershipRecord.ts_code.in_(codes), ConceptMembershipRecord.active.is_(True),
                ConceptThemeRecord.theme_type.in_(("theme", "concept")),
            ).group_by(ConceptMembershipRecord.ts_code)).all())
        price_map: Dict[str, Dict[date, float]] = defaultdict(dict)
        code_lookup = {code[:6]: code for code in codes}
        for code, day, close in prices:
            mapped = code_lookup.get(str(code)[:6])
            if mapped and close:
                price_map[mapped][day] = float(close)
        returns: Dict[str, Dict[date, float]] = {}
        for code, points in price_map.items():
            ordered = sorted(points.items())[-(horizon + 8):]
            returns[code] = {ordered[index][0]: ordered[index][1] / ordered[index - 1][1] - 1.0
                             for index in range(1, len(ordered)) if ordered[index - 1][1] > 0}
        market_points = [(row[0].date(), float(row[1])) for row in market_rows if row[1]]
        market_returns = {market_points[index][0]: market_points[index][1] / market_points[index - 1][1] - 1.0
                          for index in range(1, len(market_points)) if market_points[index - 1][1] > 0}
        days = sorted({day for values in returns.values() for day in values})[-horizon:]
        theme_daily: Dict[date, Tuple[float, int]] = {}
        for day in days:
            values = [series[day] for series in returns.values() if day in series and abs(series[day]) < 0.25]
            if len(values) >= 3:
                theme_daily[day] = (float(np.mean(values)), len(values))
        local_evidence = self._local_theme_evidence(canonical_name, by_stock)
        records: List[Dict[str, Any]] = []
        for code, meta in by_stock.items():
            if only_stock and code != _ts_code(only_stock):
                continue
            series = returns.get(code, {})
            y_values: List[float] = []
            x_values: List[List[float]] = []
            for day in days:
                if day not in series or day not in theme_daily or day not in market_returns:
                    continue
                stock_ret = series[day]
                mean_ret, count = theme_daily[day]
                leave_one_out = ((mean_ret * count) - stock_ret) / (count - 1) if count > 1 else mean_ret
                if max(abs(stock_ret), abs(leave_one_out), abs(market_returns[day])) >= 0.25:
                    continue
                y_values.append(stock_ret)
                x_values.append([1.0, leave_one_out, market_returns[day]])
            beta = market_beta = alpha = residual_return = r_squared = None
            beta_standard_error = beta_t_stat = beta_ci_low = beta_ci_high = None
            observations = len(y_values)
            if observations >= 20:
                x = np.asarray(x_values, dtype=float)
                y = np.asarray(y_values, dtype=float)
                coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
                fitted = x @ coefficients
                residuals = y - fitted
                total_ss = float(np.sum((y - np.mean(y)) ** 2))
                beta = round(float(coefficients[1]), 4)
                market_beta = round(float(coefficients[2]), 4)
                alpha = round(float(coefficients[0]) * 252 * 100, 2)
                # With an intercept, ordinary residuals sum to zero by design.
                # The useful window statistic is the intercept contribution
                # compounded across the actually observed research window.
                daily_alpha = float(coefficients[0])
                # The regression uses arithmetic daily returns, so the window
                # contribution must compound arithmetically. exp(alpha*n)
                # assumes a log-return model and overstates large intercepts.
                residual_return = round(((1.0 + daily_alpha) ** observations - 1.0) * 100, 2) if daily_alpha > -1 else None
                r_squared = round(max(0.0, 1.0 - float(np.sum(residuals ** 2)) / total_ss), 4) if total_ss else 0.0
                degrees_of_freedom = observations - x.shape[1]
                if degrees_of_freedom > 0:
                    residual_variance = float(np.sum(residuals ** 2)) / degrees_of_freedom
                    covariance = residual_variance * np.linalg.pinv(x.T @ x)
                    raw_standard_error = math.sqrt(max(0.0, float(covariance[1, 1])))
                    if raw_standard_error > 0:
                        beta_standard_error = round(raw_standard_error, 4)
                        beta_t_stat = round(float(coefficients[1]) / raw_standard_error, 3)
                        beta_ci_low = round(float(coefficients[1]) - 1.96 * raw_standard_error, 4)
                        beta_ci_high = round(float(coefficients[1]) + 1.96 * raw_standard_error, 4)
            source_count = _independent_source_count(meta["sources"])
            reason_count = len(meta["reasons"])
            longest_reason = max((len(reason) for reason in meta["reasons"]), default=0)
            provider_reason_score = min(100.0, 25.0 + min(50.0, longest_reason * 0.25) + min(25.0, reason_count * 8.0))
            corpus = local_evidence.get(code, {"count": 0, "score": 0.0, "items": []})
            local_evidence_score = float(corpus.get("score") or 0.0)
            reason_quality = min(100.0, provider_reason_score + 0.30 * local_evidence_score)
            source_strength = _independent_source_strength(meta["sources"])
            consensus = min(100.0, 100.0 * (
                0.55 * min(1.0, source_strength / 2.5) +
                0.45 * min(1.0, source_count / 3.0)
            ))
            typical_size = statistics.median(meta["theme_counts"] or [500])
            theme_scarcity = max(24.0, 100.0 - math.log10(max(2.0, typical_size)) * 27.0)
            stock_theme_breadth = max(1, int(theme_breadths.get(code) or 1))
            stock_focus = max(20.0, 100.0 - math.log2(stock_theme_breadth) * 10.0)
            specificity = 0.65 * theme_scarcity + 0.35 * stock_focus
            market_score = self._canonical_market_score(canonical_name)
            weight = round(0.36 * consensus + 0.29 * reason_quality + 0.20 * market_score + 0.15 * specificity, 1)
            confidence = "high" if (
                observations >= 55 and source_count >= 2 and (r_squared or 0) >= 0.15 and abs(beta_t_stat or 0) >= 2.0
            ) else "medium" if (
                observations >= 35 and source_count >= 1 and (r_squared or 0) >= 0.05 and abs(beta_t_stat or 0) >= 1.0
            ) else "low" if observations >= 20 else "insufficient"
            components = {
                "consensus": round(consensus, 1), "relevance": round(reason_quality, 1),
                "market": round(market_score, 1), "specificity": round(specificity, 1),
                "catalog_count": len(meta["sources"]), "provider_count": source_count,
                "provider_consensus_version": 1,
                "window_alpha_compounding_version": 1,
                "formula": "36%来源共识 + 29%业务证据 + 20%市场热度 + 15%题材专属性",
                "regression": "个股日收益 = Alpha + Beta题材×剔除自身后的题材等权收益 + Beta市场×沪深300收益",
                "window_alpha_formula": "窗口Alpha=(1+回归日截距)^有效样本数-1",
                "beta_standard_error": beta_standard_error,
                "beta_t_stat": beta_t_stat,
                "beta_ci_low": beta_ci_low,
                "beta_ci_high": beta_ci_high,
                "confidence_rule": "置信度同时约束样本数、来源数、R²与Beta t统计量",
                "theme_scarcity": round(theme_scarcity, 1),
                "stock_focus": round(stock_focus, 1),
                "stock_theme_breadth": stock_theme_breadth,
                "provider_reason_score": round(provider_reason_score, 1),
                "local_corpus_score": round(local_evidence_score, 1),
                "local_corpus_evidence_count": int(corpus.get("count") or 0),
                "local_corpus_window_days": 365,
            }
            evidence_items = [*meta["reasons"][:6], *list(corpus.get("items") or [])[:4]]
            records.append({
                "ts_code": code, "stock_name": meta["name"], "canonical_name": canonical_name,
                "as_of_date": as_of, "horizon_days": horizon, "weight_score": weight,
                "consensus_score": consensus, "relevance_score": reason_quality,
                "market_score": market_score, "specificity_score": specificity,
                "beta": beta, "market_beta": market_beta, "alpha_annualized": alpha,
                "residual_return": residual_return, "r_squared": r_squared, "observations": observations,
                "confidence": confidence, "source_count": source_count,
                "evidence_count": len(meta["reasons"]) + int(corpus.get("count") or 0),
                "components_json": json.dumps(components, ensure_ascii=False),
                "evidence_json": json.dumps(evidence_items, ensure_ascii=False),
                "unique_drivers_json": "[]", "calculated_at": utc_naive_now(),
            })
        if not records:
            return 0
        with self.db.session_scope() as session:
            for values in records:
                existing = session.execute(select(ConceptExposureRecord).where(
                    ConceptExposureRecord.ts_code == values["ts_code"],
                    ConceptExposureRecord.canonical_name == canonical_name,
                    ConceptExposureRecord.as_of_date == as_of,
                    ConceptExposureRecord.horizon_days == horizon,
                )).scalar_one_or_none()
                if existing is None:
                    session.add(ConceptExposureRecord(**values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
        return len(records)

    def reconcile_exposure_provider_counts(self, *, limit: int = 80) -> Dict[str, int]:
        """Upgrade legacy exposure rows from catalog votes to provider votes.

        This is deliberately incremental so a production restart never locks
        the shared SQLite database for a long migration.  Each worker cycle
        repairs a bounded set of canonical themes and records the method
        version inside the existing component audit payload.
        """
        marker = '%"provider_consensus_version": 1%'
        with self._read_scope() as session:
            canonicals = session.execute(select(ConceptExposureRecord.canonical_name).where(
                or_(ConceptExposureRecord.components_json.is_(None), ~ConceptExposureRecord.components_json.like(marker)),
            ).distinct().limit(max(1, min(int(limit), 500)))).scalars().all()
        updated = 0
        for canonical in canonicals:
            with self.db.session_scope() as session:
                memberships = session.execute(select(
                    ConceptMembershipRecord.ts_code, ConceptThemeRecord.source,
                ).join(
                    ConceptThemeRecord, ConceptMembershipRecord.theme_id == ConceptThemeRecord.id,
                ).where(
                    ConceptThemeRecord.canonical_name == canonical,
                    ConceptMembershipRecord.active.is_(True),
                )).all()
                sources_by_stock: Dict[str, set[str]] = defaultdict(set)
                for code, source in memberships:
                    sources_by_stock[str(code)].add(str(source))
                exposures = session.execute(select(ConceptExposureRecord).where(
                    ConceptExposureRecord.canonical_name == canonical,
                    or_(ConceptExposureRecord.components_json.is_(None), ~ConceptExposureRecord.components_json.like(marker)),
                )).scalars().all()
                for exposure in exposures:
                    sources = sources_by_stock.get(exposure.ts_code, set())
                    provider_count = _independent_source_count(sources)
                    source_strength = _independent_source_strength(sources)
                    consensus = min(100.0, 100.0 * (
                        0.55 * min(1.0, source_strength / 2.5)
                        + 0.45 * min(1.0, provider_count / 3.0)
                    ))
                    exposure.source_count = provider_count
                    exposure.consensus_score = round(consensus, 1)
                    exposure.weight_score = round(
                        0.36 * consensus
                        + 0.29 * float(exposure.relevance_score or 0)
                        + 0.20 * float(exposure.market_score or 0)
                        + 0.15 * float(exposure.specificity_score or 0),
                        1,
                    )
                    components = _json(exposure.components_json, {})
                    components.update({
                        "consensus": round(consensus, 1),
                        "catalog_count": len(sources),
                        "provider_count": provider_count,
                        "provider_consensus_version": 1,
                    })
                    exposure.components_json = json.dumps(components, ensure_ascii=False)
                    updated += 1
        return {"themes": len(canonicals), "exposures": updated}

    def reconcile_window_alpha_compounding(self, *, limit: int = 1000) -> Dict[str, int]:
        """Incrementally repair legacy window Alpha values without refetching prices."""
        marker = '%"window_alpha_compounding_version": 1%'
        limit = max(1, min(int(limit), 5000))
        with self.db.session_scope() as session:
            rows = session.execute(select(ConceptExposureRecord).where(
                or_(
                    ConceptExposureRecord.components_json.is_(None),
                    ~ConceptExposureRecord.components_json.like(marker),
                ),
            ).order_by(ConceptExposureRecord.id.asc()).limit(limit)).scalars().all()
            updated = 0
            skipped = 0
            for exposure in rows:
                annual_alpha = exposure.alpha_annualized
                observations = int(exposure.observations or 0)
                components = _json(exposure.components_json, {})
                if annual_alpha is not None and observations > 0:
                    daily_alpha = float(annual_alpha) / 252.0 / 100.0
                    exposure.residual_return = (
                        round(((1.0 + daily_alpha) ** observations - 1.0) * 100.0, 2)
                        if daily_alpha > -1.0 else None
                    )
                    components["legacy_alpha_reconciled_from"] = "alpha_annualized/252"
                    updated += 1
                else:
                    skipped += 1
                components.update({
                    "window_alpha_compounding_version": 1,
                    "window_alpha_formula": "窗口Alpha=(1+回归日截距)^有效样本数-1",
                })
                exposure.components_json = json.dumps(components, ensure_ascii=False)
        return {"examined": len(rows), "updated": updated, "skipped": skipped}

    def backfill_multi_horizon_profiles(
        self, *, stock_limit: int = 2, themes_per_stock: int = 3,
    ) -> Dict[str, int]:
        """Incrementally add 20/120-day profiles for priority research stocks."""
        stock_limit = max(1, min(int(stock_limit), 10))
        themes_per_stock = max(1, min(int(themes_per_stock), 8))
        market_date = self.latest_market_date()
        with self._read_scope() as session:
            watchlist_codes = [_ts_code(code) for code in session.execute(
                select(UserWatchlistItem.symbol).distinct().limit(stock_limit * 3)
            ).scalars().all()]
            ranked_codes = [str(code) for code, _ in session.execute(select(
                ConceptExposureRecord.ts_code, func.max(ConceptExposureRecord.weight_score),
            ).where(
                ConceptExposureRecord.horizon_days == 60,
                ConceptExposureRecord.source_count >= 2,
            ).group_by(ConceptExposureRecord.ts_code).order_by(
                desc(func.max(ConceptExposureRecord.weight_score)),
            ).limit(stock_limit * 3)).all()]
        priority_codes = list(dict.fromkeys([*watchlist_codes, *ranked_codes]))
        attempted = completed = exposures_saved = failed = processed_stocks = 0
        for code in priority_codes:
            if processed_stocks >= stock_limit:
                break
            with self._read_scope() as session:
                latest_60 = session.execute(select(func.max(ConceptExposureRecord.as_of_date)).where(
                    ConceptExposureRecord.ts_code == code,
                    ConceptExposureRecord.horizon_days == 60,
                )).scalar_one_or_none()
                primary_rows = session.execute(select(ConceptExposureRecord).where(
                    ConceptExposureRecord.ts_code == code,
                    ConceptExposureRecord.horizon_days == 60,
                    ConceptExposureRecord.as_of_date == latest_60 if latest_60 else ConceptExposureRecord.id < 0,
                    ConceptExposureRecord.source_count >= 2,
                ).order_by(desc(ConceptExposureRecord.weight_score)).limit(themes_per_stock)).scalars().all()
                canonicals = [row.canonical_name for row in primary_rows]
                existing = {
                    (str(canonical), int(window)): profile_date
                    for canonical, window, profile_date in session.execute(select(
                        ConceptExposureRecord.canonical_name,
                        ConceptExposureRecord.horizon_days,
                        func.max(ConceptExposureRecord.as_of_date),
                    ).where(
                        ConceptExposureRecord.ts_code == code,
                        ConceptExposureRecord.canonical_name.in_(canonicals) if canonicals else ConceptExposureRecord.id < 0,
                        ConceptExposureRecord.horizon_days.in_((20, 120)),
                    ).group_by(
                        ConceptExposureRecord.canonical_name, ConceptExposureRecord.horizon_days,
                    )).all()
                }
            if not canonicals:
                continue
            processed_stocks += 1
            for canonical in canonicals:
                for window in (20, 120):
                    if existing.get((canonical, window)) == market_date:
                        continue
                    attempted += 1
                    try:
                        exposures_saved += self.calculate_canonical_exposures(
                            canonical, horizon_days=window, only_stock=code,
                        )
                        completed += 1
                    except Exception as exc:  # One profile must not block the next.
                        failed += 1
                        logger.debug("concept multi-horizon backfill failed for %s %s %s: %s", code, canonical, window, exc)
        return {
            "stocks": processed_stocks, "attempted": attempted,
            "completed": completed, "failed": failed, "exposures": exposures_saved,
        }

    def _local_theme_evidence(
        self, canonical_name: str, by_stock: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Map recent AI-structured institution notes to explicit stock/theme pairs.

        A note contributes only when its AI theme/tag fields mention the current
        canonical theme and its stock_mentions field explicitly identifies a
        member stock.  This deliberately avoids treating a generic industry
        paragraph as evidence for every constituent.
        """
        if not by_stock:
            return {}
        tokens = [item.strip() for item in re.split(r"[/、|]", canonical_name) if len(item.strip()) >= 2]
        tokens = list(dict.fromkeys([canonical_name, *tokens]))[:6]
        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.extend((
                EssayAnalysisRecord.themes_json.like(pattern),
                EssayAnalysisRecord.tags_json.like(pattern),
                EssayAnalysisRecord.industries_json.like(pattern),
            ))
        if not clauses:
            return {}
        cutoff = utc_naive_now() - timedelta(days=365)
        with self._read_scope() as session:
            rows = session.execute(select(
                EssayAnalysisRecord.topic_id,
                EssayAnalysisRecord.stock_mentions_json,
                EssayAnalysisRecord.confidence_score,
                EssayAnalysisRecord.importance_score,
                EssayAnalysisRecord.summary,
                ResearchNote.title,
            ).join(
                ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id,
            ).where(
                EssayAnalysisRecord.status == "completed",
                EssayAnalysisRecord.updated_at >= cutoff,
                or_(*clauses),
            ).order_by(
                desc(EssayAnalysisRecord.importance_score), desc(EssayAnalysisRecord.updated_at),
            ).limit(800)).all()
        code_lookup = {code[:6]: code for code in by_stock}
        name_lookup = {
            re.sub(r"\s+", "", str(meta.get("name") or "")): code
            for code, meta in by_stock.items() if meta.get("name")
        }
        mapped: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "topic_ids": set(), "confidence": [], "importance": [], "items": [],
        })
        for topic_id, mentions_json, confidence, importance, summary, title in rows:
            mentions = _json(mentions_json, [])
            if not isinstance(mentions, list):
                continue
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                raw_code = _ts_code(mention.get("ts_code") or mention.get("code") or "")
                code = raw_code if raw_code in by_stock else code_lookup.get(raw_code[:6])
                if not code:
                    code = name_lookup.get(re.sub(r"\s+", "", str(mention.get("name") or "")))
                if not code or topic_id in mapped[code]["topic_ids"]:
                    continue
                mapped[code]["topic_ids"].add(topic_id)
                mention_confidence = _safe_float(mention.get("confidence"))
                mapped[code]["confidence"].append(mention_confidence if mention_confidence is not None else (_safe_float(confidence) or 0.5))
                mapped[code]["importance"].append(_safe_float(importance) or 50.0)
                rationale = str(mention.get("rationale") or "").strip()
                excerpt = rationale or str(summary or "").strip()[:180]
                mapped[code]["items"].append({
                    "kind": "institution_corpus", "topic_id": topic_id,
                    "title": str(title or "机构语料题材证据")[:120],
                    "summary": excerpt[:240], "source": "机构段子 AI 结构化标签",
                })
        result: Dict[str, Dict[str, Any]] = {}
        for code, value in mapped.items():
            count = len(value["topic_ids"])
            average_confidence = statistics.mean(value["confidence"] or [0.5])
            average_importance = statistics.mean(value["importance"] or [50.0])
            score = min(100.0, count * 11.0 + average_confidence * 35.0 + average_importance * 0.22)
            result[code] = {
                "count": count, "score": round(score, 1),
                "items": value["items"][:8],
            }
        return result

    def sync_status(self) -> Dict[str, Any]:
        with self._read_scope() as session:
            latest = session.execute(select(ConceptSyncRunRecord).order_by(desc(ConceptSyncRunRecord.id)).limit(1)).scalar_one_or_none()
            counts = {
                "themes": int(session.execute(select(func.count(ConceptThemeRecord.id))).scalar_one()),
                "memberships": int(session.execute(select(func.count(ConceptMembershipRecord.id))).scalar_one()),
                "exposures": int(session.execute(select(func.count(ConceptExposureRecord.id))).scalar_one()),
                "stocks": int(session.execute(select(func.count(func.distinct(ConceptMembershipRecord.ts_code)))).scalar_one()),
                "membership_themes_attempted": int(session.execute(select(func.count(ConceptMembershipSyncState.id))).scalar_one()),
                "membership_themes_failed": int(session.execute(select(func.count(ConceptMembershipSyncState.id)).where(
                    ConceptMembershipSyncState.status == "failed",
                )).scalar_one()),
            }
        return {"available": self.gateway.available, "latest_run": self._run_dict(latest) if latest else None, **counts}

    def rotation(self, *, days: int = 20, limit: int = 24) -> Dict[str, Any]:
        """Aggregate daily source snapshots into a transparent theme rotation board."""
        window = max(5, min(int(days), 60))
        row_limit = max(8, min(int(limit), 60))
        cutoff = self.latest_market_date() - timedelta(days=window * 2 + 10)
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeSnapshotRecord).where(
                ConceptThemeSnapshotRecord.market_date >= cutoff,
                ConceptThemeSnapshotRecord.theme_type.in_(("theme", "concept")),
            ).order_by(ConceptThemeSnapshotRecord.market_date)).scalars().all()
        grouped: Dict[str, Dict[date, List[ConceptThemeSnapshotRecord]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            grouped[row.canonical_name][row.market_date].append(row)
        items: List[Dict[str, Any]] = []
        all_dates: set[date] = set()
        for canonical_name, by_date in grouped.items():
            points: List[Dict[str, Any]] = []
            for market_date, values in sorted(by_date.items())[-window:]:
                changes = [float(value.pct_change) for value in values if value.pct_change is not None]
                heats = [float(value.heat_score) for value in values if value.heat_score is not None]
                if not changes and not heats:
                    continue
                all_dates.add(market_date)
                points.append({
                    "date": market_date.isoformat(),
                    "pct_change": round(float(statistics.median(changes)), 3) if changes else None,
                    "heat_score": round(max(heats), 1) if heats else None,
                    "source_count": _independent_source_count(value.source for value in values),
                })
            if not points:
                continue
            latest = points[-1]
            recent_changes = [point["pct_change"] for point in points[-5:] if point["pct_change"] is not None]
            latest_change = float(latest["pct_change"] or 0.0)
            momentum_5d = round(sum(recent_changes), 3) if recent_changes else None
            heat = float(latest["heat_score"] or 45.0)
            source_count = int(latest["source_count"] or 0)
            rotation_score = max(0.0, min(100.0,
                42.0 + max(-24.0, min(24.0, latest_change * 5.0))
                + min(18.0, source_count * 6.0) + (heat - 50.0) * 0.16
            ))
            family_name = theme_family(canonical_name, "theme")
            items.append({
                "canonical_name": canonical_name, "family": family_name,
                "cluster": theme_cluster(canonical_name, family_name),
                "market_date": latest["date"], "pct_change": latest["pct_change"],
                "momentum_5d": momentum_5d, "heat_score": latest["heat_score"],
                "source_count": source_count, "rotation_score": round(rotation_score, 1),
                "history_days": len(points), "points": points,
            })
        items.sort(key=lambda item: (-item["rotation_score"], -item["source_count"], item["canonical_name"]))
        return {
            "items": items[:row_limit], "total": len(items), "window_days": window,
            "available_dates": len(all_dates),
            "latest_date": max(all_dates).isoformat() if all_dates else None,
            "method": "同一规范题材按来源日涨跌中位数聚合；轮动分由当日涨跌、来源数和热度构成，不是收益预测。",
        }

    def backfill_current_snapshots(self) -> Dict[str, int]:
        """Seed daily history from the latest catalog after schema upgrades."""
        now = utc_naive_now()
        saved = 0
        with self.db.session_scope() as session:
            themes = session.execute(select(ConceptThemeRecord).where(
                ConceptThemeRecord.market_date.is_not(None),
            )).scalars().all()
            existing = {(row.source, row.source_code, row.market_date) for row in session.execute(
                select(ConceptThemeSnapshotRecord)
            ).scalars().all()}
            for row in themes:
                key = (row.source, row.source_code, row.market_date)
                if key in existing:
                    continue
                session.add(ConceptThemeSnapshotRecord(
                    source=row.source, source_code=row.source_code, canonical_name=row.canonical_name,
                    theme_type=row.theme_type, market_date=row.market_date,
                    constituent_count=row.constituent_count or 0, heat_score=row.heat_score,
                    pct_change=row.pct_change, fund_flow=row.fund_flow, captured_at=now,
                ))
                saved += 1
        return {"saved": saved}

    def normalize_catalog_names(self) -> Dict[str, int]:
        """Reapply the current ontology to stored source nodes without touching source facts."""
        changed = 0
        scanned = 0
        with self.db.session_scope() as session:
            rows = session.execute(select(ConceptThemeRecord)).scalars().all()
            for row in rows:
                scanned += 1
                canonical = canonicalize_theme(row.name)
                if row.canonical_name != canonical:
                    row.canonical_name = canonical
                    row.updated_at = utc_naive_now()
                    changed += 1
        return {"scanned": scanned, "changed": changed}

    @staticmethod
    def methodology() -> Dict[str, Any]:
        return {
            "version": "concept-consensus-v1.64",
            "principles": [
                "不同数据源的原始题材分别保留，规范名只用于聚合，不覆盖原始归属。",
                "六套目录用于审计；东方财富板块与题材库同属一个提供方，共识计票只算一票。",
                "题材权重是可解释的市场共识评分，不等于指数公司法定权重，也不是收益预测。",
                "业务证据只接受供应商入选理由，或本地机构语料中题材标签与股票明确提及的交叉命中。",
                "Beta 使用剔除个股自身后的题材组合，并同时控制沪深300，避免机械自相关。",
                "Beta 置信度同时检查样本数、来源数、R²及回归系数t统计量，并展示95%区间。",
                "Alpha 分为统计残差与可核验证据两层；证据只做归因线索，不声称因果。",
                "题材轮动保留每日来源快照后再聚合，不用当前值伪造历史，也不把轮动分解释为收益预测。",
                "个股透镜同时读取20/60/120日独立快照，跨周期稳定性不混用不同窗口或日期。",
            ],
            "weight_formula": {
                "source_consensus": 0.36, "business_evidence": 0.29,
                "market_attention": 0.20, "theme_specificity": 0.15,
            },
            "beta_formula": "r_stock = alpha + beta_theme × r_theme_leave_one_out + beta_market × r_CSI300 + epsilon",
            "windows": [20, 60, 120],
            "minimum_observations": 20,
            "sources": [{"key": key, "name": value, "provider": SOURCE_PROVIDERS[key], "reliability": SOURCE_RELIABILITY[key]}
                        for key, value in SOURCE_LABELS.items()],
            "license_note": "同花顺等第三方接口数据按其授权仅用于个人学习研究；不在此功能中转售原始数据。",
        }

    def _theme_row(self, *, source: str, code: Any, name: Any, theme_type: str, level: int,
                   market_date: date, payload: Dict[str, Any], count: Any = 0, parent: Any = None,
                   heat: Any = None, pct_change: Any = None, fund_flow: Any = None) -> Dict[str, Any]:
        label = str(name or code or "未知题材").strip()
        return {
            "source": source, "source_code": str(code or label).strip(), "name": label,
            "canonical_name": canonicalize_theme(label), "theme_type": theme_type, "level": int(level),
            "parent_code": str(parent).strip() if parent not in (None, "", "0") else None,
            "constituent_count": _safe_int(count) or 0, "market_date": market_date,
            "heat_score": _safe_float(heat), "pct_change": _safe_float(pct_change),
            "fund_flow": _safe_float(fund_flow), "source_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
        }

    def _upsert_themes(self, rows: Iterable[Dict[str, Any]]) -> int:
        now = utc_naive_now()
        saved = 0
        prepared = [values for values in rows if values.get("source_code")]
        with self.db.session_scope() as session:
            theme_map = {(row.source, row.source_code): row for row in session.execute(
                select(ConceptThemeRecord)
            ).scalars().all()}
            dates = {values.get("market_date") for values in prepared if values.get("market_date")}
            snapshot_map = {(row.source, row.source_code, row.market_date): row for row in session.execute(
                select(ConceptThemeSnapshotRecord).where(ConceptThemeSnapshotRecord.market_date.in_(dates))
            ).scalars().all()} if dates else {}
            for values in prepared:
                theme_key = (values["source"], values["source_code"])
                existing = theme_map.get(theme_key)
                if existing is None:
                    existing = ConceptThemeRecord(**values, first_seen_at=now, last_seen_at=now, updated_at=now)
                    session.add(existing)
                    theme_map[theme_key] = existing
                else:
                    for key, value in values.items():
                        if value is not None or key not in {"heat_score", "pct_change", "fund_flow"}:
                            setattr(existing, key, value)
                    existing.last_seen_at = now
                    existing.updated_at = now
                market_date = values.get("market_date")
                if market_date:
                    snapshot_key = (values["source"], values["source_code"], market_date)
                    snapshot = snapshot_map.get(snapshot_key)
                    snapshot_values = {
                        "canonical_name": values["canonical_name"], "theme_type": values["theme_type"],
                        "constituent_count": values.get("constituent_count") or 0,
                        "heat_score": values.get("heat_score"), "pct_change": values.get("pct_change"),
                        "fund_flow": values.get("fund_flow"), "captured_at": now,
                    }
                    if snapshot is None:
                        snapshot = ConceptThemeSnapshotRecord(
                            source=values["source"], source_code=values["source_code"],
                            market_date=market_date, **snapshot_values,
                        )
                        session.add(snapshot)
                        snapshot_map[snapshot_key] = snapshot
                    else:
                        for key, value in snapshot_values.items():
                            if value is not None or key not in {"heat_score", "pct_change", "fund_flow"}:
                                setattr(snapshot, key, value)
                saved += 1
        with self.__class__._taxonomy_lock:
            self.__class__._taxonomy_cache.pop(id(self.db), None)
        return saved

    def _fetch_theme_members(self, theme: Dict[str, Any]) -> List[Dict[str, Any]]:
        code = theme["source_code"]
        day_text = (self.latest_market_date()).strftime("%Y%m%d")
        source = theme["source"]
        if source == "ths":
            return self.gateway.query("ths_member", params={"ts_code": code})["rows"]
        if source == "dc_board":
            return self.gateway.query("dc_member", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "dc_theme":
            return self.gateway.query("dc_concept_cons", params={"theme_code": code, "trade_date": day_text})["rows"]
        if source == "kpl":
            return self.gateway.query("kpl_concept_cons", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "tdx":
            return self.gateway.query("tdx_member", params={"ts_code": code, "trade_date": day_text})["rows"]
        if source == "sw":
            param = {f"l{theme['level']}_code": code, "is_new": "Y"}
            return self.gateway.query("index_member_all", params=param)["rows"]
        return []

    def _upsert_memberships(
        self, theme: Dict[str, Any], rows: Sequence[Dict[str, Any]],
        force_stock: Optional[str] = None, replace_existing: bool = False,
    ) -> int:
        now = utc_naive_now()
        market_date = theme.get("market_date")
        if isinstance(market_date, str):
            market_date = _parse_date(market_date)
        saved = 0
        received_codes: set[str] = set()
        with self.db.session_scope() as session:
            theme_row = session.get(ConceptThemeRecord, int(theme["id"]))
            for row in rows:
                code = _ts_code(force_stock or row.get("con_code") or row.get("ts_code"))
                if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
                    continue
                received_codes.add(code)
                name = str(row.get("con_name") or row.get("name") or "").strip()
                reason = str(row.get("reason") or row.get("desc") or "").strip() or None
                day = _parse_date(row.get("trade_date")) or market_date
                existing = session.execute(select(ConceptMembershipRecord).where(
                    ConceptMembershipRecord.theme_id == int(theme["id"]),
                    ConceptMembershipRecord.source == theme["source"],
                    ConceptMembershipRecord.ts_code == code,
                )).scalar_one_or_none()
                values = {
                    "stock_name": name, "reason": reason, "active": True,
                    "source_weight": SOURCE_RELIABILITY.get(theme["source"], 0.7),
                    "hot_rank": _safe_int(row.get("hot_num")), "market_date": day,
                    "last_seen_at": now, "updated_at": now,
                }
                if existing is None:
                    session.add(ConceptMembershipRecord(
                        theme_id=int(theme["id"]), source=theme["source"], ts_code=code,
                        first_seen_at=now, **values,
                    ))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                saved += 1
            if replace_existing and received_codes and force_stock is None:
                session.execute(update(ConceptMembershipRecord).where(
                    ConceptMembershipRecord.theme_id == int(theme["id"]),
                    ConceptMembershipRecord.source == theme["source"],
                    ConceptMembershipRecord.active.is_(True),
                    ~ConceptMembershipRecord.ts_code.in_(received_codes),
                ).values(active=False, updated_at=now))
            if theme_row is not None and saved:
                theme_row.constituent_count = saved if replace_existing else max(theme_row.constituent_count or 0, saved)
                theme_row.last_seen_at = now
        return saved

    def _theme_lookup(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeRecord)).scalars().all()
        return {(row.source, row.source_code): self._theme_dict(row) for row in rows}

    def _canonical_market_score(self, canonical_name: str) -> float:
        with self._read_scope() as session:
            rows = session.execute(select(ConceptThemeRecord).where(
                ConceptThemeRecord.canonical_name == canonical_name)).scalars().all()
        values = [row.heat_score for row in rows if row.heat_score is not None]
        changes = [abs(row.pct_change) for row in rows if row.pct_change is not None]
        heat = max(values, default=45.0)
        movement = min(100.0, max(changes, default=0.0) * 14.0)
        return max(20.0, min(100.0, heat * 0.72 + movement * 0.28))

    def _unique_driver_evidence(self, code: str, stock_name: str = "") -> List[Dict[str, Any]]:
        cutoff = utc_naive_now() - timedelta(days=180)
        compact = code[:6]
        items: List[Dict[str, Any]] = []
        with self._read_scope() as session:
            events = session.execute(select(MonitoringEventRecord).where(
                MonitoringEventRecord.event_at >= cutoff,
                MonitoringEventRecord.symbol_codes.like(f"%{code}%"),
            ).order_by(desc(MonitoringEventRecord.importance_score), desc(MonitoringEventRecord.event_at)).limit(12)).scalars().all()
            notes = session.execute(select(ResearchNote).where(
                ResearchNote.created_at >= cutoff,
                or_(ResearchNote.symbol_codes.like(f"%{code}%"), ResearchNote.symbol_codes.like(f"%{compact}%")),
            ).order_by(desc(ResearchNote.created_at)).limit(8)).scalars().all()
            ai_notes = session.execute(
                select(ResearchNote, EssayAnalysisRecord)
                .join(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                .where(
                    ResearchNote.created_at >= cutoff,
                    EssayAnalysisRecord.status == "completed",
                    or_(
                        EssayAnalysisRecord.stock_mentions_json.like(f"%{code}%"),
                        EssayAnalysisRecord.stock_mentions_json.like(f"%{compact}%"),
                        ResearchNote.symbol_codes.like(f"%{code}%"),
                    ),
                )
                .order_by(desc(EssayAnalysisRecord.importance_score), desc(ResearchNote.created_at)).limit(10)
            ).all()
        for event in events:
            items.append({
                "kind": "event", "title": event.title, "summary": event.summary,
                "source": event.source_name, "date": event.event_at.isoformat() if event.event_at else None,
                "importance": event.importance_score, "url": event.url,
                "symbol_count": len(set(re.findall(r"\d{6}", str(event.symbol_codes or "")))),
            })
        ai_topic_ids = {note.topic_id for note, _analysis in ai_notes}
        for note in notes:
            if note.topic_id in ai_topic_ids:
                continue
            items.append({
                "kind": "institution_note", "title": note.title or (note.content or "")[:80],
                "summary": (note.content or "")[:240], "source": note.group_name or "知识星球",
                "date": note.created_at.isoformat() if note.created_at else None,
                "importance": 60, "url": f"/essay-radar/feed?topic={note.topic_id}",
                "symbol_count": len(set(re.findall(r"\d{6}", str(note.symbol_codes or "")))),
            })
        for note, analysis in ai_notes:
            catalysts = _json(analysis.catalysts_json, [])
            risks = _json(analysis.risks_json, [])
            qualifiers = [str(value) for value in [*catalysts[:2], *risks[:2]] if value]
            items.append({
                "kind": "ai_institution_signal",
                "title": note.title or f"{code} 机构语料 AI 结构化线索",
                "summary": str(analysis.summary or "")[:240] + (("；" + "；".join(qualifiers)) if qualifiers else ""),
                "source": f"{analysis.model} · 机构段子研判",
                "date": note.created_at.isoformat() if note.created_at else None,
                "importance": analysis.importance_score or 55,
                "url": f"/essay-radar/feed?topic={note.topic_id}",
                "symbol_count": len(set(re.findall(r"\d{6}", str(note.symbol_codes or "")))),
            })
        items.sort(key=lambda value: (
            value.get("importance") or 0,
            1 if value.get("kind") == "ai_institution_signal" else 0,
            value.get("date") or "",
        ), reverse=True)
        deduplicated: List[Dict[str, Any]] = []
        seen_links: set[str] = set()
        seen_titles: set[str] = set()
        for item in items:
            key = str(item.get("url") or f"{item.get('kind')}:{item.get('title')}")
            title_key = re.sub(r"[^\w\u4e00-\u9fff]", "", str(item.get("title") or "").lower())[:80]
            if key in seen_links or (title_key and title_key in seen_titles):
                continue
            seen_links.add(key)
            if title_key:
                seen_titles.add(title_key)
            category, direction = _classify_unique_driver(item.get("title"), item.get("summary"))
            title_text = str(item.get("title") or "")
            title_has_company = bool((stock_name and stock_name in title_text) or compact in title_text)
            source_text = str(item.get("source") or "").lower()
            if any(marker in title_text for marker in (" 开盘 ", " 收盘 ", "筹码与资金快照", "实时行情")):
                continue
            is_corpus_signal = item.get("kind") in {"institution_note", "ai_institution_signal"} or any(
                marker in source_text for marker in ("知识星球", "机构段子", "研判", "deepseek", "录音纪要")
            )
            if is_corpus_signal:
                # Sector roundups and market recaps may mention a stock once, but
                # that is not a company-specific Alpha driver. Keep them in the
                # institution corpus, not in this stricter company evidence rail.
                if not title_has_company:
                    continue
            item["category"] = category
            item["direction"] = direction
            item.pop("symbol_count", None)
            deduplicated.append(item)
        return deduplicated[:16]

    @staticmethod
    def _theme_dict(row: ConceptThemeRecord) -> Dict[str, Any]:
        family = theme_family(row.canonical_name, row.theme_type)
        return {
            "id": row.id, "source": row.source, "source_label": SOURCE_LABELS.get(row.source, row.source),
            "source_code": row.source_code, "name": row.name, "canonical_name": row.canonical_name,
            "theme_type": row.theme_type, "level": row.level, "parent_code": row.parent_code,
            "constituent_count": row.constituent_count, "market_date": row.market_date.isoformat() if row.market_date else None,
            "heat_score": row.heat_score, "pct_change": row.pct_change, "fund_flow": row.fund_flow,
            "family": family, "cluster": theme_cluster(row.canonical_name, family),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _exposure_dict(row: ConceptExposureRecord) -> Dict[str, Any]:
        return {
            "weight_score": round(row.weight_score, 1), "consensus_score": row.consensus_score,
            "relevance_score": row.relevance_score, "market_score": row.market_score,
            "specificity_score": row.specificity_score, "beta": row.beta, "market_beta": row.market_beta,
            "alpha_annualized": row.alpha_annualized, "residual_return": row.residual_return,
            "r_squared": row.r_squared, "observations": row.observations, "confidence": row.confidence,
            "source_count": row.source_count, "evidence_count": row.evidence_count,
            "beta_interpretation": ConceptThemeService._beta_interpretation(row.beta, row.confidence),
            "components": _json(row.components_json, {}), "evidence": _json(row.evidence_json, []),
        }

    @staticmethod
    def _beta_interpretation(beta: Optional[float], confidence: str) -> str:
        if beta is None:
            return "样本不足，暂不解释"
        prefix = "低置信：" if confidence in {"low", "insufficient"} else ""
        if beta >= 1.2:
            return f"{prefix}对题材波动高度敏感"
        if beta >= 0.8:
            return f"{prefix}与题材走势较同步"
        if beta >= 0.3:
            return f"{prefix}受题材影响但弹性较低"
        if beta <= -0.3:
            return f"{prefix}窗口内与题材反向，需核验公司独立事件"
        return f"{prefix}题材联动较弱，个股因素占比更高"

    def _start_run(self, run_type: str, market_date: date, stage: str) -> int:
        with self.db.session_scope() as session:
            row = ConceptSyncRunRecord(run_type=run_type, status="running", market_date=market_date,
                                       progress=2, stage=stage, started_at=utc_naive_now(), updated_at=utc_naive_now())
            session.add(row)
            session.flush()
            return int(row.id)

    def _finish_run(self, run_id: int, *, status: str, progress: int, stage: str, themes: int,
                    source_stats: Dict[str, Any], error: Optional[Exception] = None) -> None:
        with self.db.session_scope() as session:
            row = session.get(ConceptSyncRunRecord, run_id)
            if row:
                row.status = status
                row.progress = progress
                row.stage = stage
                row.themes_seen = themes
                row.source_stats_json = json.dumps(source_stats, ensure_ascii=False)
                row.error = f"{type(error).__name__}: {str(error)[:500]}" if error else None
                row.finished_at = utc_naive_now()
                row.updated_at = utc_naive_now()

    @staticmethod
    def _run_dict(row: ConceptSyncRunRecord) -> Dict[str, Any]:
        return {
            "id": row.id, "run_type": row.run_type, "status": row.status,
            "market_date": row.market_date.isoformat() if row.market_date else None,
            "progress": row.progress, "stage": row.stage, "themes_seen": row.themes_seen,
            "memberships_seen": row.memberships_seen, "exposures_seen": row.exposures_seen,
            "sources": _json(row.source_stats_json, {}), "error": row.error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
