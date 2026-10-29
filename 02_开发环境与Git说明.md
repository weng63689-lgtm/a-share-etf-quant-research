# 第二步开发环境与Git说明

版本日期：2026年10月29日

## 本步完成内容

1. 使用uv管理Python 3.12和项目虚拟环境。
2. 使用`pyproject.toml`声明正式依赖和开发依赖。
3. 使用`uv.lock`锁定实际安装版本，保证本机和GitHub环境一致。
4. 创建标准的`src`代码结构、测试目录和数据目录。
5. 创建通用只读HTTP API客户端，为后续行情接口预留统一入口。
6. 使用`.env`保存本机密钥，并通过`.gitignore`阻止密钥进入Git。
7. 建立本地Git仓库，默认分支为`main`。
8. 准备GitHub Actions，在上传后自动运行Ruff和pytest。

## 为什么使用uv

当前电脑没有可直接使用的Python。uv可以安装项目指定的Python版本、创建虚拟环境、安装依赖并生成锁文件。以后在另一台电脑或GitHub Actions中执行`uv sync --locked --dev`，即可恢复相同环境。

## 对初学者最重要的三个命令

```powershell
uv run quant-check
uv run pytest
uv run ruff check .
```

第一个命令检查环境，第二个命令验证代码，第三个命令检查常见代码问题。后续教学会在每一步解释新增文件和命令。

## API安全规则

- `.env.example`只保存变量名称和示例，不保存真实密钥。
- `.env`只保存在本机，而且被Git忽略。
- 代码通过环境变量读取密钥，不在日志中打印密钥。
- GitHub Actions需要密钥时，使用GitHub Secrets，不把密钥写进工作流文件。
- 行情API和券商API使用不同变量和不同客户端，避免权限混用。

## GitHub连接状态

当前状态是“本地Git已初始化，远程GitHub未连接”。这样可以先检查文件和历史，再由用户决定GitHub仓库名称、可见性和登录方式。建议后续使用私有仓库。

## 下一步

第三步将建立交易日历和ETF主表。数据接口将先实现可替换的适配层，避免把整个项目绑定到单一供应商。

