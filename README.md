# A股ETF量化交易项目

这是一个面向个人账户的A股ETF量化研究项目。第一版使用日频数据，只做多，不使用杠杆。当前阶段只建设研究、回测和模拟交易基础，不连接真实券商账户。

## 当前进度

- 第一步已完成：确定策略范围和风险边界。
- 第二步已完成：建立Python环境、项目结构、API安全配置和Git检查流程。
- 第三步已完成：建立交易日历、ETF主表、候选池和数据质量检查。
- 第四步已完成：下载候选ETF日线行情，建立未复权/前复权口径、缺失交易日检查和质量报告。
- 第五步已完成：建立现金、持仓、公司行动和事件流水账户，并完成七组买入持有基准对账。
- 第六步已完成：加入T+1、停牌、涨跌停、公司行动参考价和ETF整手交易约束。
- 第七步已完成：加入佣金、最低收费、ETF税费、滑点、市场冲击、成交容量和成本压力测试。
- 第八步已完成：构建第一版趋势动量轮动策略并完成无成本、1倍、2倍、3倍成本回测。
- 第九步已完成：完成样本外、三年滚动窗口、18组参数邻域和回撤控制检验。
- 第十步已完成：完成市场环境、选股边际、换手成本和回撤过程失败归因。
- 第十一步已完成：用开发期内部训练/验证检验第二版月度多周期候选；v2未通过晋级条件。
- 第十二步已完成：检验120日动量、多周期动量、趋势强度、低波动和复合因子的横截面有效性。
- 第十三步已完成：扩展至14只境内股票型ETF并完成扩展池因子复检。
- 第十四步已完成：比较等权、波动率倒数和8%/10%/12%目标波动率风险预算。
- 第十五步已完成：在境内多资产ETF池完成目标波动率与成本压力回测。
- 第十六步已完成：冻结多资产规则并完成最终样本外、成本压力和滚动窗口验证；因近期跑输基准，不晋级实盘。
- 第十七步已完成：建立离线模拟盘、状态持久化、幂等订单ID、数据回滚检测和每日报告。
- 第十八步已完成：建立全部20只ETF的每日增量更新、复权重基准检测和一键模拟运行脚本。
- 第十九步已完成：增加本地图形界面，可双击启动并一键完成每日更新和模拟盘。
- 下一步：用新交易日数据连续运行20至30个模拟日。

## 第一次运行

在PowerShell中执行：

```powershell
cd F:\量化\A股ETF量化
uv sync --dev
uv run quant-check
uv run pytest
```

## 更新基础数据

```powershell
# 联网更新交易日历和ETF主表
uv run quant-data update-all

# 查看当前数据摘要
uv run quant-data summary

# 重新执行数据质量检查
uv run quant-data validate
```

## 更新候选ETF日线行情

```powershell
# 下载六只候选ETF的完整日线，并执行缺日、价格和复权质量检查
uv run quant-data update-history

# 查看历史数据摘要
uv run quant-data history-summary

# 重新校验已保存的历史数据
uv run quant-data validate-history
```

原始未复权行情、前复权行情和清洗结果分别保存。质量检查失败时，不会覆盖上一版已验证的 `etf_daily`。详细字段和判断方法见 [第四步日线行情说明](docs/04_日线行情说明.md)。

## 运行账户模型

```powershell
# 更新公司行动，重建买入持有基准并检查现金与持仓对账
uv run quant-account build

# 查看账户模型汇总
uv run quant-account summary

# 检查最近一次质量报告
uv run quant-account validate
```

账户模型包含现金分红和份额折算。T+1、停牌和涨跌停已在后续交易约束层接入，但账户模型本身仍不包含佣金和滑点。详细机制和当前验收结果见 [第五步账户模型说明](docs/05_账户模型说明.md)。

## 检查交易约束

```powershell
# 按生效日期检查涨跌幅规则，并扫描历史停牌、零成交和涨跌停事件
uv run quant-execution audit

# 查看最近一次扫描摘要
uv run quant-execution summary
```

T+1、整手、停牌、涨跌停和公司行动日参考价已经接入订单判断。详见 [第六步交易约束说明](docs/06_交易约束说明.md)。

## 检查交易成本

```powershell
# 按1、2、3倍成本压力测试佣金、滑点、冲击和成交容量
uv run quant-cost audit

# 查看最近一次结果
uv run quant-cost summary
```

成本参数目前是研究假设，必须按中信证券最终费率和交易所现行规则复核。详见 [第七步成本模型说明](docs/07_成本模型说明.md)。

## 运行基线策略回测

```powershell
# 首次使用时更新沪深300基准
uv run quant-backtest update-benchmark

# 运行四种成本场景的完整回测
uv run quant-backtest run

# 查看最近一次汇总
uv run quant-backtest summary
```

第一版趋势动量策略已接入账户、公司行动、T+1、涨跌停和成本模型。当前1倍成本下累计收益高于沪深300，但最大回撤约45%，未达到20%风险目标，因此仅作为研究基线。详见 [第八步基线策略回测说明](docs/08_基线策略回测说明.md) 和 [首份回测报告](runs/backtest_v1/report.md)。

## 运行稳健性检验

```powershell
uv run quant-robustness run
uv run quant-robustness summary
```

最近约3年的样本外，原始规则累计收益-0.72%、最大回撤-28.66%；回撤控制仍为-8.15%收益和-22.26%回撤。18组开发期参数均未达到20%回撤目标，因此策略仍未通过稳健性验收。详见 [第九步稳健性检验说明](docs/09_稳健性检验说明.md) 和 [稳健性报告](runs/robustness_v1/report.md)。

## 运行失败归因

```powershell
uv run quant-diagnose run
uv run quant-diagnose summary
```

开发期归因显示：上涨市场收益捕获率约76.8%，选中ETF未来20日平均收益反而比全部合格候选低约0.06个百分点，年化单边换手约3.73倍。详见 [第十步失败归因说明](docs/10_失败归因说明.md) 和 [归因报告](runs/diagnostics_v1/report.md)。

## 运行第二版候选

```powershell
uv run quant-candidate run
uv run quant-candidate summary
```

第二版月度多周期候选在内部验证期收益14.22%、最大回撤-24.61%、跑输沪深300约5.74个百分点，未达到晋级条件。最近样本外没有参与本版设计。详见 [第十一步第二版候选说明](docs/11_第二版候选验证说明.md)。

## 运行因子研究

```powershell
uv run quant-factor run
uv run quant-factor summary
```

训练期表现最好的低波动因子20日平均Rank IC仅0.107，进入内部验证后变为-0.046；120日动量也从0.095转为-0.054。当前六只ETF的横截面因子没有通过稳定性检验。详见 [第十二步因子有效性说明](docs/12_因子有效性研究说明.md)。

## 扩展研究池

```powershell
uv run quant-expand build
uv run quant-factor-expanded run
uv run quant-factor-expanded summary
```

研究池已扩展到14只境内股票型ETF。扩展后低波动因子验证期IC为0.047，但仍没有足够大的经济收益差。详见 [第十三步研究池扩展说明](docs/13_研究池扩展与因子复检.md)。

## 运行风险预算

```powershell
uv run quant-risk-budget run
uv run quant-risk-budget summary
```

10%目标波动率主候选在内部验证期收益22.06%、最大回撤-20.05%，距离20%风险门槛略差，且在3倍成本下超额收益接近零，因此未晋级。详见 [第十四步风险预算说明](docs/14_风险预算测试说明.md)。

## 运行境内多资产版本

```powershell
uv run quant-multiasset build
uv run quant-risk-multiasset run
uv run quant-risk-multiasset summary
```

资产范围仍排除跨境、杠杆、反向和场外产品。20只境内ETF的多资产版本在内部验证期实现30.97%收益和-5.52%最大回撤，3倍成本下仍有正超额。详见 [第十五步境内多资产说明](docs/15_境内多资产风险预算.md) 和 [风险预算报告](runs/risk_budget_multi_asset/report.md)。

## 最终前向验证

```powershell
uv run quant-forward-check run
uv run quant-forward-check summary
```

冻结版本在2023-10-30至2026-10-29取得10.91%收益、-8.02%最大回撤，但同期沪深300上涨12.78%，未通过正超额条件。详见 [第十六步前向验证说明](docs/16_冻结策略前向验证.md)。

## 运行离线模拟盘

```powershell
uv run quant-paper init --start-date 2026-10-30
uv run quant-multiasset build
uv run quant-paper run-latest
uv run quant-paper status
```

模拟盘只使用本地数据，不连接券商或发送真实委托。历史回放已验证727个日快照、107个唯一订单ID和重复运行幂等。详见 [第十七步模拟交易闭环说明](docs/17_模拟交易闭环说明.md) 和 [模拟盘日报](runs/paper/latest_report.md)。

## 每日运行

最简单的方法：双击项目根目录的 `启动模拟盘.bat`，然后点击“一键每日运行”。

也可以使用命令行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_daily_paper.ps1
```

脚本会先更新全部20只ETF并校验，再推进模拟盘和输出账户状态。也可以指定日期：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_daily_paper.ps1 -AsOf 2026-10-30
```

详细机制见 [第十八步每日增量更新说明](docs/18_每日增量更新说明.md)。

图形界面和使用步骤见 [第十九步模拟盘图形界面说明](docs/19_模拟盘图形界面说明.md)。

开发阶段使用AKShare适配器。程序会优先尝试ETF实时接口，失败时自动切换到备用列表接口。原始抓取结果、标准化结果和抓取来源分别保存，不能把免费网站接口当作实盘唯一数据源。

以后进入项目时不需要手动激活虚拟环境，直接使用 `uv run` 即可。

## 常用命令

```powershell
# 检查项目环境
uv run quant-check

# 运行自动测试
uv run pytest

# 检查代码格式和常见问题
uv run ruff check .

# 根据锁文件恢复完全相同的依赖环境
uv sync --locked --dev
```

## API密钥

复制 `.env.example` 为 `.env` 后，在 `.env` 中填写真实API地址和密钥。`.env` 已被Git忽略，不会提交到GitHub。

```powershell
Copy-Item .env.example .env
```

禁止把API密钥、券商账号、密码、访问令牌或个人身份信息写入代码、YAML配置、README或测试文件。

## GitHub连接

项目已经是本地Git仓库，但当前没有远程仓库，也没有上传任何文件。后续连接GitHub时，建议建立私有仓库并使用GitHub CLI登录。连接前先确认 `git status` 中没有 `.env`、数据文件或账户信息。

GitHub Actions配置位于 `.github/workflows/ci.yml`。代码上传后，它会自动恢复锁定环境并运行代码检查和测试。

## 目录说明

```text
configs/             不含密钥的策略和系统配置
data/raw/            原始数据，不提交Git
data/processed/      清洗数据，不提交Git
data/reference/      可提交的小型参考数据
data/market.duckdb   本地查询数据库，不提交Git
docs/                项目说明
runs/                回测和实验结果，不提交Git
src/a_share_quant/   正式Python代码
tests/               自动测试
.github/workflows/   GitHub自动检查
```

## 安全边界

- 当前API客户端只提供通用只读HTTP能力，没有券商下单方法。
- 真实交易功能必须在模拟盘、风控和券商权限确认后单独开发。
- GitHub只保存代码和小型配置，不保存真实行情数据库、账户数据或密钥。

