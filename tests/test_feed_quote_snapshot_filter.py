"""快讯不得出现「行情快照」条目。

RSS/NewsNow 上游里有雪球个股页这类订阅源，把个股行情当 feed 条目输出：
标题只是股票名（"中国石油"），摘要是涨跌幅+市场（"2.99% SH"），
URL 是个股页而不是文章。这种条目没有任何新闻内容，进到快讯里就是噪音。

线上数据：近 4000 条样本里约 33 条，历史累计 362 条。
"""

import pytest

from src.services.investment_monitor_service import _is_quote_snapshot_item


class TestQuoteSnapshotDetection:
    @pytest.mark.parametrize("title,summary,url", [
        ("中国石油", "2.99% SH", "https://xueqiu.com/s/SH601857"),
        ("联想集团", "-6.33% HK", "https://xueqiu.com/s/00992"),
        ("英伟达", "-2.0097% NASDAQ", "https://xueqiu.com/s/NVDA"),
        ("胜宏科技", "-10.57% SZ", "https://xueqiu.com/s/SZ300476"),
        ("中国石油", "2.99% SH", None),  # 无 URL 时组合判据已足够
    ])
    def test_detects_quote_snapshots(self, title, summary, url):
        assert _is_quote_snapshot_item(title, summary, url) is True

    @pytest.mark.parametrize("title,summary,url", [
        # 真实新闻：标题有内容
        ("吴清：进一步深化市场稳定长效机制研究", None, "https://www.cls.cn/detail/2534426"),
        ("美股收盘：苹果再创新高 AI交易依然颠簸", None, "https://www.cls.cn/detail/2534350"),
        ("【早报】51个一类创新药进入医保目录", None, "https://www.cls.cn/detail/2534592"),
        # 异动快讯：摘要也是涨跌幅，但标题有正文
        ("A股异动｜中国石油涨超2%", "2.99% SH", "https://www.cls.cn/detail/1"),
        # 摘要不是纯涨跌幅
        ("中国石油", "公司发布公告，拟回购股份。", "https://xueqiu.com/s/SH"),
        # 没有标题
        ("", "2.99% SH", "https://xueqiu.com/s/SH"),
        # 标题过长
        ("中国石油天然气股份有限公司发布年度业绩预告说明", "2.99% SH", "https://xueqiu.com/s/SH"),
    ])
    def test_keeps_real_news(self, title, summary, url):
        assert _is_quote_snapshot_item(title, summary, url) is False
