# A-Stock-Data数据接入

版本日期：2026年10月31日

## 接入范围

项目接入了 A-Stock-Data 文档中的腾讯K线访问方案，用于获取ETF的：

- 未复权开、高、低、收和成交量。
- 前复权开、高、低、收和成交量。
- 日线数据。

腾讯K线接口不返回成交额，因此成交额仍由原项目数据源提供。公司行动和现金分红也继续由原数据链路处理。

## 数据安全策略

每天增量更新时：

1. 原有数据源抓取未复权、前复权和成交额。
2. A-Stock-Data适配器独立抓取最近区间的未复权与前复权价格。
3. 两个来源按交易日期对齐。
4. 只有日期覆盖率不低于95%、所有OHLC和前复权价格的相对差异都不超过0.05%时，才使用A-Stock-Data价格。
5. 成交额、公司行动和账户模型继续使用原有经过验证的数据。
6. 如果A-Stock-Data不可用或价格不一致，则回退原数据源，并在每日更新报告中记录原因。

## 已验证结果

2026年10月20日至2026年10月30日，对全部20只ETF进行交叉校验：

- 成功校验：20只。
- 日期覆盖率：全部100%。
- 未复权OHLC最大差异：0。
- 前复权OHLC最大差异：0。

可随时执行只读双源审计：

```powershell
uv run quant-daily-update cross-check --days 10
```

审计结果保存在：

```text
data/processed/a_stock_data_crosscheck.json
```

## 文件

- `src/a_share_quant/data/providers/a_stock_data_provider.py`
- `data/raw/a-stock-data/`
- `data/processed/daily_update_report.json`
- `third_party/a-stock-data/LICENSE`
- `THIRD_PARTY_NOTICES.md`

## 上游项目

https://github.com/simonlin1212/a-stock-data

上游采用 Apache 2.0 许可证。本项目保留许可证和来源说明。
