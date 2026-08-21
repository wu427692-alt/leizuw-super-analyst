# 本地多周期行情数据库

财经情报台将行情图表统一为五个时间尺度：`intraday`、`daily`、`weekly`、`monthly`、`yearly`。
首页大盘指数、自选股卡片和自选股超级看板复用同一组件与查询契约，切换周期只刷新图表，不写入投资情报流。

首页默认使用分时口径展示大盘与当前自选股，顶部八个核心 A 股指数同时也是主 K 线切换器，选中后代码、报价、分时/日周月年 K 线保持同一指数。顶部报价和主图读取同一份本地秒快照；北向资金和海外最近收盘分别显示实际交易日，不与盘中秒行情混算。

首页市场广度和行业板块分布采用严格“上海当日”契约。交易日盘中优先使用 AkShare 的新浪全 A 股实时快照与新浪行业板块快照，分别绘制涨跌家数分布、涨跌比例、涨跌停家数、行业涨跌幅分箱以及领涨/领跌行业；实时源失败时只允许 Tushare `daily`、`moneyflow_ind_ths` / `moneyflow_ind_dc` 的同一交易日记录补位。接口返回上一交易日、交易日历判定当日休市或当日数据尚未发布时，模块显示“当日数据暂不可用”，不会回退旧交易日冒充今日。

首页“当日涨跌幅”采用严格日期门禁：只有报价时间属于上海当前自然日才展示。盘前、采集异常或实时接口不可用而回退到 Tushare 上一交易日日线时，页面只展示“上一收盘”和实际交易日，涨跌幅保持隐藏；历史日线的涨跌幅仍可在用户主动切换日 K 后查看。

图表按行情语义绘制：日内为分钟分时线，横轴显示交易时间，纵轴显示所选阶段相对基准的涨跌幅，并按可见数据的最高/最低涨跌幅自适应留出小幅边距，不强制围绕 0% 或昨收对称；日线、周线、月线、年线为红涨绿跌 K 线，叠加 MA5/MA10/MA20 与独立成交量副图。当日分时以昨收为基准，五日分时以可见区间首个有效价格为阶段基准。

## 存储口径

- `stock_daily`：个股日线事实库。远端补取成功后按股票代码和交易日 UPSERT。
- `stock_intraday`：一分钟 OHLCV，作为长期压缩数据和秒级源异常时的兜底。
- `stock_ticks`：自选股 1 秒实时快照，按股票代码和本机采集秒 UPSERT；同时保存行情源返回的当日累计成交量/额，以及相邻真实快照差分得到的本秒成交量/额。每个交易日首笔没有可靠前值，秒量保持空值，不把开盘前累计量伪装成一秒成交。
- `market_index_bars`：保留交易所后缀的指数日线/分钟线，避免 `000001.SH` 与 `000001.SZ` 冲突；`1SEC` 指数快照使用相同的累计量与秒增量双口径。
- 周线、月线、年线不重复建表，查询时从 `stock_daily` 聚合；周期标签使用真实最后交易日，不使用未来的自然周期结束日。
- 四张行情表都进入 iCloud 知识数据库快照白名单，但实时 SQLite 仍只允许单设备写入。

盘中采集器默认在上海时间工作日连续竞价时段 `09:30–11:30`、`13:00–15:00` 运行，默认采集全部当前自选股和配置的大盘指数。自选股通过一次批量请求按固定 1 秒节拍写入 `stock_ticks`；两只股票理论约 28,800 条/交易日。启动及新增自选时自动补齐最近 5 个交易日分钟历史和约 20 年日线历史；真正的历史秒线只保留程序实际采集到的事实快照，外部只能取得分钟历史时不会伪装成秒线。日内和五日图始终以一分钟 OHLCV 作为已结束区间；仅当前最新一分钟由该分钟内已入库的秒快照合成为一个实时 OHLCV 点，并随最新快照更新。若分钟历史尚未补齐，接口也会先把秒快照按分钟聚合，不直接返回密集秒点。SQLite 仍保留保留期内全部秒级事实行。

```dotenv
MARKET_DATA_AUTO_START=true
MARKET_TICK_POLL_SECONDS=1
MARKET_INTRADAY_SESSIONS=5
MARKET_DAILY_HISTORY_DAYS=7300
MARKET_BOOTSTRAP_DELAY_SECONDS=90
MARKET_INDEX_LIST=000001.SH,000016.SH,000300.SH,000688.SH,000905.SH,000852.SH,399001.SZ,399006.SZ
```

`MARKET_INDEX_LIST` 是可扩展的秒级指数池。默认覆盖上证指数、上证50、沪深300、科创50、中证500、中证1000、深证成指和创业板指。`MARKET_BOOTSTRAP_DELAY_SECONDS` 让开机后的首个看板请求优先于全量历史维护；已有鲜活日线不会因标的上市时间短而在每次启动时被重复全量下载。运行中新增自选股会立即采集首笔行情，并自动进入近半年全渠道回填；即使从设置页直接修改 `STOCK_LIST`，采集器也会检测新增标的。

## API

```http
GET /api/v1/stocks/603306/history?period=intraday&range=1d&refresh=true
GET /api/v1/stocks/603306/history?period=daily&range=6m
GET /api/v1/stocks/603306/history?period=weekly&range=2y
GET /api/v1/stocks/603306/history?period=monthly&range=5y
GET /api/v1/stocks/603306/history?period=yearly&range=max
GET /api/v1/stocks/market-data/index/000001.SH?period=monthly&range=5y
```

允许的 `range` 为 `1d`、`5d`、`1m`、`3m`、`6m`、`1y`、`2y`、`3y`、`5y`、`10y`、`max`。
旧客户端仍可传 `days`；新客户端优先使用 `range`。响应会给出 `source`、`stored_count`、
`latest_at`、`refreshed` 和 `storage=sqlite`，便于页面明确显示数据来源和本地覆盖情况。
日内图表数据中，`volume` / `amount` 表示该分钟成交量/额；当前最新一分钟使用该分钟内可验证的秒增量合计，`cumulative_volume` /
`cumulative_amount` 同时保留最新快照的当日累计量。接口每个交易分钟最多返回一个点，最后一个点可保留秒级时间戳用于标识当前分钟正在实时更新。
最新行情接口额外返回 `second_volume` 和 `second_amount`，原 `volume` / `amount` 保持当日累计口径，兼容现有调用方。

```http
GET  /api/v1/stocks/market-data/status
POST /api/v1/stocks/market-data/refresh
```

状态接口返回分钟采集 worker 和本地日线/分钟行数；立即刷新接口只采集已配置自选股。
采集器状态同时提供 `last_success_at`、`consecutive_empty_runs`、本次/本进程累计个股与指数写入量，以及当前监控标的。盘中上游短暂返回空数据时不会退出，而是在下一秒自动重试。
