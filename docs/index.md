---
title: A股ETF量化研究与模拟交易系统
description: 从数据治理到离线模拟盘的完整量化研究工程
---

# A股ETF量化研究与模拟交易系统

独立设计并实现一套面向境内ETF的量化研究系统，覆盖数据治理、账户与公司行动、交易制度、成本模型、回测验证、风险预算、失败归因和离线模拟盘。

[查看源代码](https://github.com/weng63689-lgtm/a-share-etf-quant-research){: .btn}

## 项目范围

- 20只境内上市ETF：股票、债券、黄金和货币。
- 日频、只做多、不使用杠杆。
- 信号产生后下一交易日开盘模拟执行。
- 不连接券商，不发送真实订单。
- 原始行情、数据库、密钥和模拟账户状态不进入GitHub。

## 系统链路

```text
数据源适配与质量校验
        ↓
交易日历、公司行动、复权口径
        ↓
趋势与动量信号
        ↓
目标波动率与风险预算
        ↓
T+1、整手、停牌、涨跌停和成交容量
        ↓
佣金、滑点、市场冲击
        ↓
账户事件流水与每日对账
        ↓
样本外、稳健性、前向验证和模拟盘
```

## 工程亮点

- 交易规则和成本约束与策略信号分离。
- 账户金额使用高精度Decimal计算。
- 公司行动保留原始事件和来源。
- 历史数据回填时，模拟盘会停止而不是静默继续。
- 模拟订单ID具有确定性，重复运行不会重复下单。
- 冻结配置与算法实现均记录SHA-256指纹。
- 78项自动测试、Ruff和GitHub Actions持续集成。

## 严谨的研究结论

多资产10%目标波动率方案在内部验证期取得30.97%累计收益和-5.52%最大回撤，并通过2倍、3倍成本压力。

但在规则冻结后的最终样本外：

- 策略收益：10.91%
- 最大回撤：-8.02%
- 同期沪深300：12.78%
- 超额收益：-1.87%

项目因此没有通过正超额收益条件，也未进入真实资金交易。相比选择性展示有利区间，本项目保留了完整的失败归因和拒绝上线结论。

## 关键文档

- [项目案例摘要](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/PORTFOLIO.md)
- [策略范围与需求](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/01_策略范围与需求说明.md)
- [每日增量更新](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/18_每日增量更新说明.md)
- [模拟盘图形界面](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/19_模拟盘图形界面说明.md)
- [QuantDinger信号模式集成](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/22_QuantDinger集成说明.md)
- [策略优化实验](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/23_策略优化实验.md)
- [A-Stock-Data数据接入](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/25_A-Stock-Data数据接入.md)
- [冻结策略前向验证](https://github.com/weng63689-lgtm/a-share-etf-quant-research/blob/main/docs/16_冻结策略前向验证.md)

## 使用方式

```powershell
git clone https://github.com/weng63689-lgtm/a-share-etf-quant-research.git
cd a-share-etf-quant-research
uv sync --locked --dev
uv run quant-check
uv run pytest
```

本项目仅用于量化研究与模拟交易，不构成投资建议，也不保证历史结果能够复现于未来。
