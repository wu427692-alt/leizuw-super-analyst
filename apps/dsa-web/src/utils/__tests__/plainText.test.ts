import { describe, expect, it } from 'vitest';
import { stripHtmlText, summarizeText } from '../plainText';

describe('stripHtmlText', () => {
  it('removes scraped page markup and comments', () => {
    const raw = '<div class="article-content-left"><!-- 正文广告top start --><p>沪指震荡上扬 <b>人形机器人</b>概念活跃</p></div>';
    expect(stripHtmlText(raw)).toBe('沪指震荡上扬 人形机器人概念活跃');
  });

  it('drops an unterminated trailing comment', () => {
    expect(stripHtmlText('沪指震荡上扬<!-- 行情图')).toBe('沪指震荡上扬');
  });

  it('drops script and style blocks entirely', () => {
    const raw = '<style>.a{color:red}</style>正文<script>alert(1)</script>结束';
    expect(stripHtmlText(raw)).toBe('正文 结束');
  });

  it('decodes common entities and collapses whitespace', () => {
    expect(stripHtmlText('A&nbsp;&amp;&nbsp;B\n\n  C')).toBe('A & B C');
  });

  it('returns empty string for non-string input', () => {
    expect(stripHtmlText(null)).toBe('');
    expect(stripHtmlText(undefined)).toBe('');
    expect(stripHtmlText(42)).toBe('');
  });

  it('keeps plain text untouched', () => {
    expect(stripHtmlText('央行开展 4000 亿元买断式逆回购')).toBe('央行开展 4000 亿元买断式逆回购');
  });
});

describe('summarizeText', () => {
  it('truncates by characters and never splits surrogate pairs', () => {
    const input = '一'.repeat(10) + '😀😀😀';
    const out = summarizeText(input, 11);
    expect(out.endsWith('…')).toBe(true);
    expect(Array.from(out)).toHaveLength(12);
  });
});
