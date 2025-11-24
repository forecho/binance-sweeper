# 币安空投清扫器

定时监控你的币安账户，把不在白名单的空投币自动换成指定的 `BNB` 或 `USDT`。

## 功能
- 轮询账户余额，识别不在白名单的资产
- 按配置的目标币（`SWEEP_TARGET`）用市价单卖出
- 按交易对的 `LOT_SIZE`、`MIN_NOTIONAL` 自动规整数量，避免下单失败
- `DRY_RUN` 默认开启，先观察日志确认再放行真实交易

## 环境准备（必须用 venv）
```bash
# 创建虚拟环境
python3 -m venv .venv
# 激活虚拟环境（macOS/Linux）
source .venv/bin/activate
# 若在 Windows： .\.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入自己的密钥与配置

# 运行
python main.py --once     # 只跑一轮
python main.py            # 持续轮询
```

## 配置项（`.env`）
- `BINANCE_API_KEY` / `BINANCE_API_SECRET`：必填，现货 API Key。
- `SWEEP_TARGET`：`USDT` 或 `BNB`，决定卖出的目标币。
- `WHITELIST`：逗号分隔的资产符号，不会被卖出。程序会自动把 `SWEEP_TARGET` 加入白名单。
- `POLL_SECONDS`：轮询间隔秒数。
- `MIN_QUOTE_NOTIONAL`：最小成交额（以目标币计），低于该值或交易所 `MIN_NOTIONAL` 都不会下单。
- `DRY_RUN`：`true/false`，为 `true` 时只打印日志不下单。
- `BINANCE_API_URL`：可选，留空使用官方接口；需要测试网可改为 `https://testnet.binance.vision/api`（仅在支持测试网的现货接口时有效）。

## 运行模式
- `python main.py --once`：执行一轮检查，适合 cron。
- `python main.py`：常驻进程，每 `POLL_SECONDS` 秒轮询一次。

## 注意事项
- 真实下单前先保持 `DRY_RUN=true` 观察几轮日志，确认不会误卖。
- 仅会尝试 `资产/目标币` 这个现货交易对；如果交易对不存在会跳过并记录日志。
- 需要为 API Key 开通现货交易权限，且确保账户资产充足以满足最小下单金额。

## 如何申请并配置 Binance API Key
1. 登录币安 Web，点击头像 -> 「API 管理」，新建一个 API（比如命名 `sweeper`）。
2. 生成后会得到 `API Key` 与 `Secret Key`，复制到 `.env` 中的 `BINANCE_API_KEY` 与 `BINANCE_API_SECRET`。
3. 权限设置：
   - 勾选「启用现货与杠杆交易」。
   - **不要** 开启提现权限。
   - 只读权限不足以下单。
4. 推荐设置 IP 白名单，限制只允许你的服务器/本机 IP 调用。
5. 如果需要测试网，可用测试网专用 Key，并在 `.env` 设置 `BINANCE_API_URL=https://testnet.binance.vision/api`（注意测试网是否支持相应交易对）。
