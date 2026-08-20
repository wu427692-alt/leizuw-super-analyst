export const CHANNEL_LABELS: Record<string, string> = {
  company: '公司公告', news: '财经快讯', research: '券商研报', institution: '机构调研', capital: '资金席位',
  ownership: '股权事项', governance: '公司治理', fundamental: '财务业绩', enterprise: '企业风险', essay: '知识星球',
  market: '市场行情', technical: '技术因子', other: '其他',
};

export function eventTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
