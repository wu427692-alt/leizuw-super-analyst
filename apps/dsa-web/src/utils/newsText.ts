import { stripHtmlText } from './plainText';

const BLOCK_BREAK = /<br\s*\/?>|<\/(?:p|div|section|article|blockquote|li|ul|ol|h[1-6]|table|tr|pre)\s*>/gi;

/**
 * 快讯标题常写成「【一句话标题】后面跟整段正文」，列表只留冒号前那句话。
 * 标题里出现方括号时才裁，否则原样返回。
 */
export function newsHeadline(value?: string | null): string {
  const raw = stripHtmlText(value).trim();
  if (!raw) return '';
  const bracketed = raw.match(/^【([^】]{2,48})】/);
  return bracketed ? bracketed[1] : raw;
}

/**
 * 正文按来源原始段落切分：块级标签换行，其余标签剥成纯文本。
 * 只输出纯文本，交给 React 文本节点渲染，绝不注入上游 HTML。
 */
export function newsParagraphs(value?: string | null): string[] {
  if (typeof value !== 'string' || !value.trim()) return [];
  const separated = value.replace(/<!--[\s\S]*?-->/g, ' ').replace(BLOCK_BREAK, '\n');
  return separated.split('\n').map(line => stripHtmlText(line)).filter(Boolean);
}
