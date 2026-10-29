# 基础数据字典

## trading_calendar

| 字段 | 类型 | 含义 |
|---|---|---|
| calendar_date | date | 自然日 |
| market | string | `CN_STOCK`表示沪深股票市场共享日历 |
| is_open | bool | 是否交易日 |
| previous_open_date | date | 此日期之前最近交易日 |
| next_open_date | date | 此日期之后最近交易日 |
| source | string | 原始数据来源适配器 |
| retrieved_at | string | 抓取时间，使用带时区ISO格式 |

## etf_master

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | 六位基金代码 |
| exchange | string | `SSE`或`SZSE` |
| exchange_symbol | string | 统一代码，例如`510300.SH` |
| name | string | 数据源返回的基金简称 |
| security_type | string | 固定为`ETF` |
| lot_size | int | 二级市场交易单位，第一版为100份 |
| last_price | float | 抓取时最新价，仅用于数据检查 |
| volume | float | 抓取时成交量 |
| turnover | float | 抓取时成交额 |
| source | string | 实际使用的接口来源 |
| retrieved_at | string | 抓取时间 |

`etf_master`是全量开发主表，不代表每只ETF均符合首版策略范围。首版候选池以`strategy_universe.csv`为准。

## etf_daily

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | 六位ETF代码 |
| exchange | string | `SSE`或`SZSE` |
| exchange_symbol | string | 带交易所后缀的统一代码 |
| trade_date | date | 交易日 |
| open/high/low/close | float | 未复权开高低收 |
| volume | float | 成交量，统一为“手” |
| turnover | float | 人民币成交额 |
| amplitude_pct | float | 振幅百分比 |
| change_pct | float | 数据源涨跌幅百分比 |
| change_amount | float | 数据源涨跌额 |
| turnover_rate_pct | float | 数据源换手率百分比 |
| adj_open/adj_high/adj_low/adj_close | float | 前复权开高低收 |
| adjustment_factor | float | `adj_close / close` |
| adjustment_method | string | 第一版固定为`qfq` |
| source | string | 实际使用的行情接口 |
| retrieved_at | string | 抓取时间 |

`etf_daily`只包含股票代码、交易日、全部OHLC和成交额等结构检查通过的数据。交易日缺口及最长连续缺口记录在`etf_daily_missing_dates`和`etf_daily_coverage`中。

## etf_corporate_actions

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | 六位ETF代码 |
| event_date | date | 公司行动生效或除息日期 |
| action_type | string | `cash_distribution`或`share_adjustment` |
| cash_per_unit | float | 每份现金分红，非现金事件为0 |
| share_multiplier | float | 份额调整倍数，现金分红为1 |
| source | string | 行情或分红接口来源 |
| retrieved_at | string | 抓取时间 |
| derivation | string | 数据推导方法 |

现金分红使用累计分红差得到。份额折算由原始价格与前复权价格收益差异推导，正式交易前需要基金公告复核。

## buy_hold_snapshots

| 字段 | 类型 | 含义 |
|---|---|---|
| benchmark | string | 单只ETF或等权组合基准名称 |
| symbol | string | ETF代码；组合为`MULTI` |
| trade_date | date | 估值日期 |
| cash | float | 当日现金 |
| market_value | float | 未复权收盘价计算的持仓市值 |
| equity | float | 现金加持仓市值 |
| total_return | float | 相对初始资金的总收益 |
| reconciled | bool | 账户等式是否通过 |
| positions_json | string | 当日各标的持仓份额 |

## account_ledger

记录买入、卖出、现金分红和份额折算事件。`cash_delta`是本次现金变化，`fees`是佣金等现金费用，`cash_balance`是事件后的现金，`position_after`是事件后的持仓数量。

## buy_hold_summary

保存每组买入持有基准的期末权益、累计收益、年化收益、年化波动率和最大回撤。当前指标只用于验证账户链路，不包含策略和真实交易成本。

## trading_rules.csv

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | 六位ETF代码 |
| effective_from | date | 规则开始生效日期 |
| effective_to | date | 规则结束日期，空表示持续有效 |
| price_limit_pct | float | 涨跌幅限制比例 |
| tick_size | float | 最小价格变动单位 |
| buy_lot_size | int | 买入整手数量 |
| sell_lot_size | int | 标准卖出整手数量 |
| settlement_days | int | 买入后可卖所需交易日数；当前为1 |

## trading_constraint_events

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | ETF代码 |
| trade_date | date | 发生约束的日期 |
| event_type | string | 涨停、跌停、零成交或缺失行情 |
| open | float | 当日开盘价；缺失行情时为空 |
| previous_close | float | 前一可交易记录收盘价 |
| limit_reference_close | float | 经公司行动修正后的涨跌停参考价 |
| limit_price | float | 触发当日适用的涨停价或跌停价 |
| detail | string | 约束补充说明 |

## cost_model_scenarios

| 字段 | 类型 | 含义 |
|---|---|---|
| symbol | string | ETF代码 |
| trade_date | date | 流动性取样日期 |
| side | string | 买入或卖出 |
| target_notional | float | 目标订单金额 |
| quantity | float | 按最近价格换算的整手数量 |
| market_price | float | 未计成本的订单参考价 |
| fill_price | float | 经滑点和冲击调整后的成交价 |
| turnover | float | 当日全市场ETF成交额 |
| participation | float | 订单金额占当日成交额比例 |
| stress_multiplier | float | 成本压力倍数 |
| commission | float | 佣金 |
| stamp_duty | float | 印花税 |
| transfer_fee | float | 过户费 |
| slippage_cost | float | 基础滑点和价格取整成本 |
| impact_cost | float | 市场冲击成本 |
| total_cost | float | 总交易成本 |
| cost_bps | float | 总成本占订单金额的基点 |
| capacity_quantity | float | 配置参与率允许的最大整手数量 |

## benchmark_daily

记录沪深300指数未复权日线，字段包括`benchmark`、`trade_date`、开高低收、成交量和来源。策略回测与基准都从首次实际目标组合日期起算。

## backtest_signals

| 字段 | 类型 | 含义 |
|---|---|---|
| signal_date | date | 信号使用数据的最后日期 |
| execution_date | date | 最早允许成交日期 |
| symbol | string | ETF代码 |
| rank | int | 当周风险调整动量排名 |
| momentum | float | 120日动量 |
| volatility | float | 20日年化波动率 |
| score | float | 动量除以波动率 |
| retained | bool | 是否因持仓缓冲保留 |
| target_weight | float | 目标组合权重 |
| selected | bool | 是否进入最终目标组合 |

## backtest_snapshots

保存每个成本场景的日现金、市值、权益、总仓位、持仓数量、调仓换手、沪深300净值和账户对账状态。

## backtest_orders

保存每笔订单的信号日、执行日、方向、数量、成交或拒单原因、市场价、成交价、佣金、滑点、冲击和总成本。

## backtest_summary / backtest_yearly

分别保存四场景绩效摘要和年度收益。它们用于研究，不构成实盘收益承诺。

## robustness_scenarios

保存开发期、最终样本外、开发期回撤控制和样本外回撤控制四个场景的绩效摘要。`risk_scale`在逐日快照中表示下一周目标仓位使用的风险暴露比例。

## robustness_parameter_grid

保存18组动量周期、趋势周期和最小换手参数。所有参数组合都只在开发期数据上检验，并包含收益、回撤、超额收益和换手指标。

## robustness_rolling_windows

保存从不同起始年份重置账户后的三年滚动结果。`window_start`和`window_end`是评估区间，不是参数训练区间。

## robustness_snapshots / robustness_orders

保存四个主要稳健性场景的逐日账户快照和订单流水，用于复核回撤降仓、样本外表现及对账。

## diagnostics_regimes

按沪深300位于200日均线上方或下方拆分策略收益、基准收益、收益捕获率、平均仓位和持仓数量。

## diagnostics_yearly

保存开发期年度策略收益、基准收益、超额收益、平均仓位和年度单边换手。

## diagnostics_selection

保存不同排名、是否入选和前瞻期限下的候选数量、未来平均收益与中位收益。前瞻收益只用于事后归因，不进入交易信号。

## diagnostics_drawdowns

保存回撤峰值日期、谷底日期、恢复日期、最大回撤和持续时间。

## diagnostics_trades

按ETF汇总成交笔数、买卖次数、成交金额、佣金和总交易成本。

## structural_scenarios

保存第二版候选及其消融方案在训练期和内部验证期的绩效。`variant`表示规则版本，`period`区分`train`和`validation`。

## structural_snapshots / structural_orders

保存第二版候选各场景的逐日账户快照与订单，用于复核月度调仓、多周期信号、市场风险过滤和回撤控制。

## structural_v2_summary.json

包含v2定义、时间切分、全部消融结果、晋级条件及是否晋级的结论。最近最终样本外未参与本版设计。

## factor_panel

每个月末信号日、每只ETF的120日动量、多周期动量、趋势强度、20日波动率、低波动反向值和未来20/60日收益。

## factor_ic_detail

每个信号日的横截面Rank IC和最高三分之一减最低三分之一收益差。

## factor_ic_summary

按训练期/内部验证期、因子和前瞻期限汇总平均IC、IC波动、正IC比例和分组收益差。前瞻收益只用于研究，不用于生成信号。

## research_etf_daily / research_etf_corporate_actions

14只境内股票型ETF的独立研究数据，不覆盖原六只ETF的正式基础表。新增标的包含宽基、成长、红利和行业风格。

## expanded_factor_panel / expanded_factor_ic_detail / expanded_factor_ic_summary

扩展研究池的因子面板、逐信号IC明细和汇总结果。字段与六只ETF的因子表一致。

## risk_budget_scenarios

保存扩展池中等权、波动率倒数和不同目标波动率方案的训练/验证结果，以及主候选的2倍、3倍成本压力。

## risk_budget_snapshots / risk_budget_orders

保存风险预算场景的每日权益、实际仓位、持仓数量和订单成本。未入选标的的强制退出不受最小交易阈值和普通换手上限缩小。

## multi_asset_research_etf_daily / multi_asset_research_etf_corporate_actions

20只境内ETF的独立多资产研究数据，覆盖境内股票、债券、黄金和货币ETF，不包含跨境、杠杆、反向和场外产品。

## multi_asset_risk_budget_scenarios

保存境内多资产等权、波动率倒数和目标波动率方案的训练/验证结果，以及10%目标波动率候选的2倍、3倍成本压力。

## multi_asset_risk_budget_snapshots / multi_asset_risk_budget_orders

保存多资产候选逐日权益、仓位、持仓和订单。跌停或停牌导致强制卖出失败时，持仓可在后续可交易日退出。

## forward_validation_scenarios

冻结境内多资产策略在2023-10-30至2026-10-29的1倍、2倍、3倍成本压力结果。

## forward_validation_rolling

五个三年滚动窗口的冻结策略结果。窗口分别重置账户，用于检查收益和回撤稳定性。

## forward_validation_snapshots / forward_validation_orders

保存最终前向验证和滚动窗口的逐日权益与订单流水，供模拟交易前的最终审计。

## paper_state

模拟盘状态位于`data/paper/state.json`，记录冻结策略指纹、模拟区间、最新已处理日期、数据指纹、账户权益和告警。真实交易及券商连接固定为关闭。

## paper_snapshots / paper_orders / paper_targets / paper_runs

保存模拟账户逐日权益、确定性订单ID、信号目标权重和每日运行记录。重复运行同一日期会覆盖得到相同结果，不追加重复订单。

## daily_update_report

记录全研究池增量更新的目标交易日、ETF数量、完整复权回抓品种、公司行动数量、缺日数量和输出路径。数据更新未通过校验时不会生成成功报告。

