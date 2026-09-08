# Crypto-Signal-Copier

一个默认安全、人工审核优先的 Telegram 信号解析与 Bitget 模拟盘执行工具。

## 当前能力

- 使用你自己的 Telegram API 应用和用户会话。
- 只处理 `TELEGRAM_ALLOWED_CHAT_IDS` 白名单中的频道或群组。
- 收到消息后正常发送已读确认，不实现 ghost mode 或管理员规避。
- 从中英文信号中解析币种、方向、入场区间、止损、止盈和风险比例。
- 对消息进行稳定 ID 去重，并保存本地 SQLite 审计记录。
- 查询 Bitget 模拟盘账户状态。
- 支持普通 Bitget API Key 的实盘账户只读连接；实盘模式不会提交订单。
- 使用 Bitget V2 USDT 合约接口生成或提交限价模拟订单。
- 每个 Bitget 请求强制添加 `paptrading: 1`。
- 即使配置了 Demo API Key，默认也只做 dry-run；只有设置
  `BITGET_ENABLE_DEMO_ORDERS=true` 并在界面确认后才会提交模拟订单。
- 支持在界面切换人工复核模式。默认开启；关闭后仅对新收到且校验通过的信号
  自动提交模拟订单，数量由 `AUTO_DEMO_ORDER_SIZE` 控制。
- 自动模式仍受 Demo API 连接和 `BITGET_ENABLE_DEMO_ORDERS` 双重限制；
  条件不满足时信号保持待审核，并写入审计日志。
- 提供完全独立的程序内模拟账户：只读取 Bitget 真实合约标记价，不使用交易 API Key，
  也不向交易所提交订单。
- 可输入虚拟 USDT 初始本金；系统按信号风险比例、止损距离、杠杆和可用保证金计算仓位，
  持续统计浮动/已实现盈亏、手续费、保证金、账户权益和收益率。
- 程序内模拟单会等待真实价格进入信号的入场区间，并按真实标记价触发止损及等比例分批止盈。

这不是可直接用于真实资金的生产交易系统。代码没有真实盘开关。

## 实盘行情模拟账户

打开控制台左侧的“实盘模拟”，输入初始虚拟本金和模拟杠杆即可创建本地账户。
创建后可以把当前信号手动加入模拟账户，也可以开启“新信号自动模拟”。
跟单策略通过选择 Telegram 博主/频道来源配置；自动模拟只处理已选择来源的新信号。

这个功能与 Bitget Demo 模拟盘相互独立：

- 行情来自 Bitget 公开的真实合约市场接口。
- 本金、挂单、持仓和盈亏只保存在本地 SQLite 数据库中。
- 实盘价格未进入消息指定的入场区间时，订单保持“等待入场”。
- 价格进入区间后，按照 `账户权益 × 风险比例 ÷ 入场价与止损价距离` 计算数量，
  同时限制仓位不能超过可用保证金容量。
- 多个止盈价默认等比例分批平仓；止损会关闭全部剩余仓位。
- 默认模拟手续费率为单边 0.06%，创建账户时可通过 API 调整。

后台每 5 秒刷新有活动订单的币种价格。该轮询是教学与策略验证级实现，不能等同于
交易所撮合回放；进程停止期间发生的瞬时穿价不会被捕获。

## 目录

- `backend/`：FastAPI、Telegram 客户端、解析器、SQLite 和 Bitget 模拟盘客户端。
- `prototype/`：React 控制台，通过 Vite 代理访问本地后端。

## 本地启动

### 1. 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

没有凭据时后端仍会启动，并生成一条演示信号；Telegram 和 Bitget 会显示“未配置”。

### 2. 前端

```powershell
cd prototype
npm.cmd install
npm.cmd run dev -- --host 0.0.0.0 --port 4173 --strictPort
```

打开 `http://localhost:4173/`。

## Telegram 一次性授权

1. 在 [Telegram API development tools](https://my.telegram.org/apps) 创建自己的应用。
2. 把 `api_id`、`api_hash`、手机号和允许来源的数值 ID 写入 `backend/.env`。
3. 执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.telegram_login
```

验证码和二次验证密码只在本机终端输入。生成的 session 文件位于 `backend/data/`，
已被 Git 忽略。重启后端即可开始监听白名单来源。

## Bitget 模拟盘

1. 在 Bitget 网站切换到模拟盘，并创建 Demo API Key。
2. 把 Demo API Key、Secret 和 Passphrase 写入 `backend/.env`。
3. 先保持 `BITGET_ENABLE_DEMO_ORDERS=false` 验证账户连接和 dry-run。
4. 确认无误后才将其改为 `true` 并重启后端。保持
   `REQUIRE_MANUAL_REVIEW=true` 时逐笔确认；关闭界面中的人工复核开关后，
   新信号会使用 `AUTO_DEMO_ORDER_SIZE` 自动提交到模拟盘。

不要在聊天、截图、提交记录或前端代码中粘贴任何密钥。

## Bitget 实盘只读连接

普通 Bitget API Key 需要设置 `BITGET_API_ENVIRONMENT=live`。程序会移除仅供模拟盘使用的
`paptrading: 1` 请求头，并通过合约账户接口验证连接。实盘模式在代码层面禁止下单；即使误将
`BITGET_ENABLE_DEMO_ORDERS=true`，下单请求也会在访问网络前被拒绝。

若账户已升级为 Bitget 统一账户（UTA），程序会在经典 V2 账户接口返回 `40085` 后自动改用
UTA 的 `GET /api/v3/account/assets` 只读接口。

资金管理页面通过 UTA 私有 WebSocket 订阅 `account`、`position` 和 `order` 频道。账户、持仓或
订单发生变化时，后端会立即重新获取完整 REST 快照并推送到浏览器；连接中断时自动重连，页面
打开期间也会每 60 秒执行一次 REST 校准。WebSocket 只接收账户事件，不包含下单操作。

建议为该 Key 仅开启读取权限、设置 IP 白名单，并关闭交易和提现权限。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest

cd ..\prototype
npm.cmd run build
npm.cmd run test:sites
```

## 参考文档

- [Telegram 创建 API 应用](https://core.telegram.org/api/obtaining_api_id)
- [Telegram 用户授权](https://core.telegram.org/api/auth)
- [Telegram API 条款](https://core.telegram.org/api/terms)
- [Bitget 模拟盘 REST API](https://www.bitget.com/api-doc/classic/demotrading/restapi)
- [Bitget REST 签名](https://www.bitget.com/api-doc/common/signature)
- [Bitget UTA WebSocket 指南](https://www.bitget.com/api-doc/uta/guide)
- [Bitget UTA 账户私有频道](https://www.bitget.com/api-doc/uta/websocket/private/Account-Channel)
- [Bitget 合约下单](https://www.bitget.com/api-doc/contract/trade/Place-Order)
