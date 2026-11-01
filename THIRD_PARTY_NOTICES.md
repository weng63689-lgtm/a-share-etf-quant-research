# Third-Party Notices

## A-Stock-Data

- Project: A-Stock-Data
- Repository: https://github.com/simonlin1212/a-stock-data
- Revision used for interface review: `bf447c1d05fa75eac907426601d602125c81a0cd`
- License: Apache License 2.0

The ETF provider in
`src/a_share_quant/data/providers/a_stock_data_provider.py` adapts the
documented Tencent K-line access strategy from A-Stock-Data. The full upstream
license is included at `third_party/a-stock-data/LICENSE`.

Changes made for this project:

- Exposed the endpoint as a Python provider implementing the project's
  `DailyHistoryData` interface.
- Added project-specific schemas, date pagination, validation, error handling
  and cross-checks against the existing ETF data pipeline.
- Kept turnover amount and corporate actions in the existing local provider
  because the upstream Tencent K-line endpoint does not return turnover amount.
