# 币安空投清扫器

定时监控你的币安账户，把不在白名单的空投币自动换成指定的 `BNB` 或 `USDT`。

## 功能
- 轮询账户余额，识别不在白名单的资产
- **自动从币安宝（灵活理财）赎回到现货账户**（可选，需要开启 `AUTO_REDEEM_FLEXIBLE_SAVINGS`）
- **自动从资金账户划转到现货账户**（可选，需要开启 `AUTO_TRANSFER_FROM_FUNDING`）
- 按配置的目标币（`SWEEP_TARGET`）用市价单卖出
- **自动将小额资产（低于最小成交额）转换为 BNB**（可选，需要开启 `AUTO_CONVERT_DUST_TO_BNB`）
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
- `AUTO_REDEEM_FLEXIBLE_SAVINGS`：`true/false`，为 `true` 时自动从币安宝（灵活理财）赎回资产到现货账户再卖出。默认 `false`。
- `AUTO_TRANSFER_FROM_FUNDING`：`true/false`，为 `true` 时自动将资金账户中的资产划转到现货账户再卖出。默认 `false`。
- `AUTO_CONVERT_DUST_TO_BNB`：`true/false`，为 `true` 时自动将小额资产（低于最小成交额无法卖出的）转换为 BNB。默认 `false`。
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
   - ✅ 勾选「启用现货与杠杆交易」（必须）
   - ✅ 勾选「启用万向划转」（如果需要使用 `AUTO_TRANSFER_FROM_FUNDING` 功能）
   - ❌ **不要** 开启提现权限
   - ❌ 只读权限不足以下单
4. 推荐设置 IP 白名单，限制只允许你的服务器/本机 IP 调用。
5. 如果需要测试网，可用测试网专用 Key，并在 `.env` 设置 `BINANCE_API_URL=https://testnet.binance.vision/api`（注意测试网是否支持相应交易对）。

## 自动赎回和划转功能

### 币安宝（灵活理财）自动赎回

如果你的空投币在**币安宝（灵活理财）**，可以开启自动赎回功能：

1. 在 `.env` 中设置 `AUTO_REDEEM_FLEXIBLE_SAVINGS=true`
2. 确保 API Key 已开启「**启用万向划转**」权限
3. 程序会在每次扫描时：
   - 先检查币安宝余额
   - 将不在白名单的资产自动赎回到现货账户（通常 T+0 即时到账）
   - 然后执行正常的卖出流程

**注意事项：**
- ✅ 支持灵活理财（币安宝）自动赎回
- ❌ 定期理财产品无法提前赎回，必须等到期
- ⏱️ 灵活理财赎回通常是即时到账（T+0）
- 🔄 如果同一资产在多个账户（币安宝、资金账户、现货），会依次处理

### 资金账户自动划转

如果你的空投币在**资金账户**，可以开启自动划转功能：

1. 在 `.env` 中设置 `AUTO_TRANSFER_FROM_FUNDING=true`
2. 确保 API Key 已开启「**启用万向划转**」权限
3. 程序会在每次扫描时：
   - 检查资金账户余额
   - 将不在白名单的资产自动划转到现货账户
   - 然后执行正常的卖出流程

**注意事项：**
- 资金账户划转通常是实时到账

### 推荐配置

建议同时开启两个功能，实现全自动化：

```bash
AUTO_REDEEM_FLEXIBLE_SAVINGS=true
AUTO_TRANSFER_FROM_FUNDING=true
DRY_RUN=true  # 先用测试模式观察
```

### 小额资产转 BNB（Dust Transfer）

如果你的账户有很多**小额资产**（价值低于最小成交额，无法直接卖出），可以开启自动转 BNB 功能：

1. 在 `.env` 中设置 `AUTO_CONVERT_DUST_TO_BNB=true`
2. 程序会在每次扫描时：
   - 检查所有无法达到最小成交额的资产
   - 将它们批量转换为 BNB（使用币安的小额资产转换功能）
   - 转换后的 BNB 会自动卖出（如果你的目标是 USDT）

**注意事项：**
- ✅ 适合处理价值低于 5 USDT 的小额资产
- ✅ 币安官方功能，安全可靠
- ⚠️ 转换会收取少量手续费（BNB）
- ⚠️ BNB 和白名单中的资产不会被转换
- ⚠️ **币安限制：每小时只能转换一次**（所有资产批量转换）
- ℹ️ 如果你的目标是 BNB（`SWEEP_TARGET=BNB`），转换后会保留为 BNB
- 💡 建议：不要频繁运行，等待资产积累后再转换
- 🐛 **已知问题**：批量转换可能因为 API 格式问题失败。如果遇到问题，建议手动在币安网页/APP 操作：`账户 > 钱包 > 小额资产兑换BNB`

**执行顺序：**
1. 从币安宝赎回 → 2. 从资金账户划转 → 3. 在现货账户卖出 → 4. 小额资产转 BNB
