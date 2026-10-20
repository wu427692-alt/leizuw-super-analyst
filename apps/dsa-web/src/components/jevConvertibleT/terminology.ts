// Translate known engine explanations for display; preserve unrecognized source text.
const explanations: Record<string, string> = {
  'Recorded order books and tape do not reproduce queue position, hidden liquidity or market impact.': '历史盘口和成交记录无法还原排队位置、隐藏流动性或市场冲击。',
  'Commission, slippage and other fees are configurable research assumptions, not broker-verified charges.': '佣金、滑点及其他费用是可调整的研究假设，未经实际券商费用核验。',
  'Some events have no best bid/ask; exact execution cannot be claimed.': '部分事件缺少最优买卖报价，无法声称已精确还原成交。',
  'Some best-quote sizes are missing; trade-volume participation alone does not prove available queue depth.': '部分最优档挂单量缺失，仅以成交参与率限制数量，不能证明队列中有足够可成交流动性。',
  'Exchange price-band metadata is incomplete; full exchange-rule coverage is not claimed.': '交易所价格限制资料不完整，尚未覆盖全部交易规则。',
  'Some quote ages are absent; contemporaneous order-book freshness is unverified.': '部分报价缺少距最近更新的时间，无法核验当时盘口是否仍有效。',
  'Some last-trade marks are stale; displayed last-price valuations are not guaranteed executable prices.': '部分最近成交价较陈旧；按其计算的账户估值不代表当时一定可以成交。',
  'Some factor availability timestamps are assumed equal to asof; true publication latency is unverified.': '部分因子的可用时间假设等于数据所属时点，真实发布延迟尚未核验。',
  'USD research account is not a CNY conversion; cash-account settlement, margin eligibility, SEC/FINRA fees and corporate actions are not fully reconstructed.': '研究账户直接以美元计价，未进行人民币换算；现金账户结算、保证金资格、SEC/FINRA 费用及公司行为尚未完整还原。',
  'Initial base inventory is valued, not purchased: lot-rounding residual joins cash and total initial equity is conserved.': '初始底仓按市值计入账户，不模拟首次买入；整手取整后的余款计入现金，初始总权益保持不变。',
  'Incremental T P&L equals actual equity minus unchanged base inventory plus unchanged initial cash; excess cash alone is not profit.': '增量做T收益等于实际权益减去“初始底仓持续持有＋初始现金不动”的基准权益；现金增加本身不等于利润。',
  'No shorting or borrowing. Existing base inventory can be sold and repurchased on the same day.': '不做空、不借款；已有底仓可在同日卖出和回补。',
  'Only later real events after max(configured, reported model latency) may fill. Sparse time intervals are not interpolated.': '仅允许在决策后续的真实事件上成交，并等待配置延迟与实测模型延迟中的较大值；缺失时间段不作插值。',
  'Best ask/bid with adverse tick-rounded slippage and verified incremental-volume participation cap; supplied depth and price bands constrain fills.': '按最优卖价买入、最优买价卖出，加入不利方向滑点并按价格最小单位取整；已核验的增量成交量参与率、盘口深度和价格限制共同约束成交。',
  'A quote interval overlapping the order latency is unusable because its within-interval trade timing is unknown.': '若报价统计区间与委托等待时间重叠，该区间不能用于成交，因为区间内部的实际成交时点未知。',
  'Minimum commission accrues per simulated order across partial fills. No stock stamp tax is applied.': '同一模拟委托的分笔成交合并计算最低佣金；不计入股票印花税。',
  'End-of-session restoration submits ordinary delayed, liquidity-constrained orders; missing fills remain visible inventory gaps.': '收盘恢复底仓通过普通委托执行，同样受延迟与流动性限制；未成交部分保留为可见的底仓缺口。',
  'Session-end or configured research-window-end restoration submits ordinary delayed, liquidity-constrained orders; no liquidation is fabricated and missing fills remain visible inventory gaps.': '交易时段或指定研究区间结束时，恢复底仓通过普通委托执行，受延迟与流动性限制；不虚构平仓成交，未成交部分保留为底仓缺口。',
  'Context factors require backward asof and available_at timestamps; stale or missing values are never replaced with zero.': '参考因子必须在决策时点之前已发布并可用；过期或缺失值不以零替代。',
  'Model sees anonymous event-normalized prices, volume and PIT factors; identity, account balances and execution parameters are excluded.': '模型输入仅包含匿名化、按事件归一化的价格、成交量及当时可用因子，不包含标的身份、账户余额和成交参数。',
  'Replay is historical research; pretrained-model information, parameter selection and genuine out-of-sample performance remain separate questions.': '回放属于历史研究；预训练信息影响、参数选择和真正的样本外表现仍需分别验证。',
};
export function explainResearch(text: string): string {
  return explanations[text] ?? text;
}
