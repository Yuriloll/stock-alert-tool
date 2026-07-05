# Free Stock Alert Tool

这个小工具做两件事：

- 价格提醒：从 Yahoo Finance 公开 chart 接口轮询价格，支持美股和常见国际股票代码，例如 `NVDA`、`MU`、`000660.KS`。
- 新闻提醒：从 Google News RSS 按关键词抓取最新新闻。
- 每日简报：每天固定时间推送一份过去 24 小时金融新闻 + 当前价格快照。

它不需要付费 API key。缺点是数据源不是交易所授权实时行情，可能延迟、限流或偶发失效。短线交易不能把它当作唯一信号源。

## 快速开始

1. 复制配置文件：

```bash
cp config.example.json config.json
```

2. 编辑 `config.json`：

- `symbols`：放你要监控的股票。
- `alert_pct_from_previous_close`：相对昨收涨跌超过多少百分比时提醒。
- `alert_pct_from_last_seen`：相对上次抓取价格涨跌超过多少百分比时提醒。
- `news.keywords`：放新闻关键词。
- `daily_digest.time`：每日简报发送时间，例如 `10:00`。
- `daily_digest.keywords`：每日简报要搜索的新闻关键词。

3. 试跑一次：

```bash
python3 monitor.py --config config.json --once --snapshot
```

4. 持续运行：

```bash
python3 monitor.py --config config.json
```

现在持续运行模式只会等待每日简报时间，不再每 5 分钟监控价格。

如果只想立即发送一次每日简报：

```bash
python3 monitor.py --config config.json --daily-digest
```

本地 `.env` 已被 `.gitignore` 排除。你也可以把 Telegram token、chat id 和本地代理写进 `.env` 后直接运行：

```bash
./run_daily_digest_local.sh
```

## 推送方式

默认会尝试使用 macOS 本机通知，同时在终端打印。

如果你想推送到手机，最简单的是 Telegram：

1. 在 Telegram 找 `@BotFather` 创建 bot，拿到 `telegram_bot_token`。
2. 给你的 bot 发一条消息。
3. 打开 `https://api.telegram.org/bot你的TOKEN/getUpdates`，找到你的 `chat.id`。
4. 填入 `config.json`：

```json
"push": {
  "telegram_bot_token": "123456:ABC...",
  "telegram_chat_id": "123456789",
  "webhook_url": "",
  "macos_notification": true
}
```

也可以填 `webhook_url`，推到你自己的服务、Discord/Slack/飞书等 webhook。

## 每日 10 点简报

默认配置已经打开每日简报：

```json
"daily_digest": {
  "enabled": true,
  "time": "10:00",
  "lookback_hours": 24,
  "max_news_per_keyword": 3,
  "summary_items": 10
}
```

它会在配置的时区里每天 10:00 推送一次，内容包括：

- `symbols` 里所有股票的当前价格、相对昨收涨跌幅、行情时间。
- 从 `daily_digest.keywords` 抓取过去 24 小时内的 Google News RSS 新闻，去重后按本地规则挑选最有价值的 10 条，生成中文影响说明，并附带新闻源链接。

如果你想马上测试每日简报，可以运行：

```bash
python3 monitor.py --config config.json --once --snapshot
```

这条命令会立刻推送一次价格快照，也会立刻推送一次每日简报测试版。

## GitHub Actions 每日推送

这个目录已经包含 GitHub Actions 配置：

```text
.github/workflows/daily-market-digest.yml
config.github-actions.json
```

它会在 GitHub 云端每天 UTC 02:23 运行一次，也就是北京时间 10:23，所以你的电脑关机、合盖、断网都不影响。之所以是 10:23，是为了避开整点高峰；GitHub 官方说明定时任务在高负载时可能延迟，整点尤其常见。

上传到 GitHub 前，不要提交本地 `config.json`、`.env`、`state.json`、`openai_usage_log.jsonl`。`.gitignore` 已经把它们排除。

在 GitHub 仓库里进入：

```text
Settings → Secrets and variables → Actions → New repository secret
```

无 OpenAI API 版本只需要添加这 2 个 Secrets：

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

然后把 `stock-alert-tool` 目录里的文件作为仓库根目录推到 GitHub。Actions 页面里可以手动点 `Daily Market Digest → Run workflow` 测试一次。

## 常用代码

- SK hynix：`000660.KS`
- Samsung Electronics：`005930.KS`
- NVIDIA：`NVDA`
- Micron：`MU`
- AMD：`AMD`
- Broadcom：`AVGO`
- TSMC：`TSM`
- VanEck Semiconductor ETF：`SMH`
- iShares Semiconductor ETF：`SOXX`
- Nasdaq 100 ETF：`QQQ`

## 建议阈值

半导体个股波动大，可以先这样设：

- 大市值美股：相对昨收 `3%`，相对上次抓取 `2%`
- ETF：相对昨收 `2%`，相对上次抓取 `1.5%`
- 韩股：相对昨收 `3%` 到 `5%`

## 免费方案的硬限制

- Yahoo Finance 公开接口不是正式 SLA API。
- 免费抓取不保证实时，韩股尤其可能延迟。
- Google News RSS 是新闻聚合，速度和完整性不等于 Bloomberg/Reuters。
- 如果轮询过快，可能被限流。建议 3 到 10 分钟一次。
