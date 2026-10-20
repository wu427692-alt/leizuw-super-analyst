import { describe, expect, it } from 'vitest';
import { newsPublisher } from '../newsPublisher';

describe('新闻媒体名不显示数据接口品牌', () => {
  it.each([
    ['Tushare 财联社快讯', 'tushare.news.cls', '财联社快讯'],
    ['TUSHARE · 新浪财经', 'tushare.news.sina', '新浪财经'],
    ['', 'tushare.news.cls', ''],
    ['tushare.news.cls', 'tushare.news.cls', ''],
    ['Tushare', 'tushare.long_news', ''],
    ['上海证券报', 'feeds.test', '上海证券报'],
  ])('%s', (sourceName, sourceKey, expected) => {
    expect(newsPublisher({ sourceName, sourceKey })).toBe(expected);
  });
});
