# 第二十步：QuantDinger信号模式集成

版本日期：2026年10月30日

## 集成目标

将现有境内ETF研究策略接入QuantDinger的研究、展示和监控界面。

集成遵守以下边界：

- 只使用QuantDinger的 `signal` 模式。
- 不创建 `live` 模式策略。
- 不填写券商凭据，不授予Agent Token交易权限。
- 本地A股账户和成本模型仍是正式研究结果。
- QuantDinger界面用于观察信号和虚拟账户，不用于真实下单。

## 文件

- `src/a_share_quant/integrations/quantdinger_strategy.py`
  - 符合QuantDinger Strategy API V2的20只境内ETF策略源码。
- `src/a_share_quant/integrations/quantdinger.py`
  - 调用QuantDinger Agent Gateway的部署工具。
- `scripts/deploy_quantdinger.ps1`
  - 本地部署入口。

## 策略对应关系

| 本地冻结策略 | QuantDinger策略 |
|---|---|
| 20只境内ETF | `CNStock:<代码>.<SH或SZ>` |
| 120日动量 | 同一参数 |
| 200日趋势过滤 | 同一参数 |
| 20日波动率 | 同一参数 |
| 60日协方差和10%目标波动率 | 同一逻辑 |
| 月初调仓、最多3只 | `run_monthly` 和最多3只 |
| 8%/12%/16%组合回撤控制 | 同一阈值 |
| 只做多、不使用杠杆 | `long_only`、关闭杠杆 |

## 先决条件

1. 安装支持Docker Compose v2的Docker Desktop。
2. 按QuantDinger官方说明在本地启动服务。
3. 注册本地管理员账户。
4. 在QuantDinger中创建一个Agent Token。

Agent Token只授予 `R` 和 `W` 权限，不要授予 `T` 或实盘权限。

## 配置

在项目根目录创建或编辑 `.env`：

```text
QUANTDINGER_BASE_URL=http://127.0.0.1:8888
QUANTDINGER_AGENT_TOKEN=这里填写Agent_Token
```

`.env` 已被Git忽略。不要把令牌写入代码、README、截图或GitHub。

## 部署前演练

不连接QuantDinger，先检查将要提交的配置：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_quantdinger.ps1 -DryRun
```

## 部署

确保QuantDinger已经在 `http://127.0.0.1:8888` 启动，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_quantdinger.ps1
```

部署工具会依次：

1. 验证Agent Token。
2. 调用官方编译接口检查Strategy API V2源码。
3. 新建或更新私有策略源码。
4. 创建处于停止状态的 `signal` 模式策略。

工具不会启动策略，也不会创建实盘策略。首次部署后需要在QuantDinger网页中人工检查并启动。

## 部署后检查

截至2026年10月30日，本地部署和首次接入已经完成：

- QuantDinger Web：`http://127.0.0.1:8888`
- 移动端：`http://127.0.0.1:8889`
- 后端健康检查：`http://127.0.0.1:5000/api/health`
- 策略源码ID：`1`
- 策略部署ID：`1`
- 策略状态：`running`
- 执行模式：`signal`
- Agent权限：`R/W`
- Agent市场范围：`CNStock`
- Agent有效期：30天
- Agent Token只保存在当前Windows用户环境变量中

2026年10月30日又创建了独立优化候选，未覆盖原策略：

- 候选源码ID：`2`
- 候选部署ID：`2`
- 候选名称：`A股境内ETF多资产模拟盘 v2止损8候选`
- 状态：`running`
- 执行模式：`signal`
- 风险规则：8%单品种止损

原策略和止损候选是两套独立虚拟账户，可以同时运行和比较。

查看两套策略的只读状态：

```powershell
uv run quant-qd-status
```

该命令只读取状态、权益和盈亏，不会启动、停止、修改策略或下单。

管理员用户名和密码保存在 `F:\量化\QuantDinger\.env`，不要把该文件上传GitHub。

可以使用项目根目录的一键脚本：

- `启动QuantDinger.bat`
- `停止QuantDinger.bat`

在QuantDinger网页中确认：

- 策略类型为投资组合策略。
- 执行模式为 `signal`。
- 杠杆关闭。
- 市场标的是20只 `CNStock` ETF。
- 调度时间为每月第一个交易日的09:30。
- 没有关联券商凭据。

然后先运行一个短区间回测，检查数据覆盖和订单日志，再进行模拟运行。

## 重要限制

QuantDinger的中国股票数据适配器使用腾讯等数据源，可能以复权价格进行模拟成交。本项目使用未复权价格成交、前复权价格生成信号，并单独处理公司行动。

因此，QuantDinger的虚拟账户净值不能与本地模拟盘假设为逐笔一致。两套系统必须分别查看数据来源和成交假设，不能只比较最终收益数字。

QuantDinger当前明确支持的传统券商是IBKR和Alpaca，没有境内券商原生接入。本项目继续禁止真实交易。

## 第一版验收标准

- 策略源码通过QuantDinger官方编译接口。
- 策略只创建为 `signal` 模式。
- 20只ETF均有日线数据。
- 至少完成一次回测并生成订单日志。
- 模拟运行满20个交易日后，再评估是否需要对账差异进行修正。

2026年5月1日至2026年10月30日的首次QuantDinger验收回测已经完成：

- 20只ETF全部有数据，每只读取444根日线。
- 产生14次执行、6笔完整交易。
- 区间收益：-5.35%。
- 最大回撤：-7.09%。

该结果只验证平台接入和数据覆盖，不是新的策略有效性证据。
