/**
 * 上游资讯摘要里常混入抓取页面残留的 HTML（`<div class="article-content-left">`、注释、内联样式）。
 * 界面只展示纯文本，这里统一剥掉标签、注释与多余空白。
 */

const HTML_COMMENT = /<!--[\s\S]*?-->/g;
/** 抓取截断时常见未闭合的 `<!--`，后面整段都是注释残渣，直接丢掉。 */
const UNCLOSED_COMMENT = /<!--[\s\S]*$/;
const SCRIPT_OR_STYLE = /<(script|style)\b[\s\S]*?<\/\1>/gi;
const BLOCK_TAG = /<\/?(?:p|div|br|li|ul|ol|tr|td|th|table|section|article|header|footer|h[1-6]|blockquote)\b[^>]*>/gi;
const TAG = /<\/?[a-z][^>]*>/gi;
const ENTITIES: Record<string, string> = {
  '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&hellip;': '…', '&mdash;': '—', '&ldquo;': '“', '&rdquo;': '”',
};

export function stripHtmlText(value: unknown): string {
  if (typeof value !== 'string' || !value) return '';
  let text = value.replace(HTML_COMMENT, ' ').replace(UNCLOSED_COMMENT, ' ').replace(SCRIPT_OR_STYLE, ' ');
  // 块级标签补空格，行内标签（b/span/a…）直接去掉，避免把中文词拆开
  text = text.replace(BLOCK_TAG, ' ').replace(TAG, '');
  text = text.replace(/&[a-z#0-9]+;/gi, entity => ENTITIES[entity.toLowerCase()] ?? ' ');
  return text.replace(/\s+/g, ' ').trim();
}

/** 摘要截断：按字符数截，不切断代理对。 */
export function summarizeText(value: unknown, max = 120): string {
  const text = stripHtmlText(value);
  if (text.length <= max) return text;
  return `${Array.from(text).slice(0, max).join('')}…`;
}
