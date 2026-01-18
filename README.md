# RSS Telegram Bot

一个轻量的 RSS 订阅推送脚本：定时拉取 RSS 源，将最新内容推送到 Telegram 频道或群组，并通过本地历史记录避免重复发送。

> English version: [README_EN.md](README_EN.md)

## 功能特性
- 使用 `rss.config` 维护 RSS 订阅地址，一行一个链接。
- 通过 `data.json` 记录已推送条目，避免重复。
- 支持来源标识、加粗标题与简介展示的 Telegram 消息格式。
- 可通过 GitHub Actions 定时运行。

## 目录结构
- `rss_bot.py`：主脚本。
- `rss.config`：RSS 订阅列表配置。
- `data.json`：推送历史记录。
- `.github/workflows/rss_bot.yml`：定时任务工作流配置。

## 快速开始
1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 配置 RSS 地址：
   ```text
   # 在 rss.config 中每行填入一个 RSS 地址
   https://news.google.com/rss
   ```
3. 配置 Telegram Bot Token：
   ```bash
   export TELEGRAM_BOT_TOKEN="你的机器人 Token"
   ```
4. 运行脚本：
   ```bash
   python rss_bot.py
   ```

## 使用说明
- `rss.config` 中以 `#` 开头的行会被忽略。
- `data.json` 会在首次运行时自动创建。
- 若需修改推送频道，请在 `rss_bot.py` 中调整 `TELEGRAM_CHAT_ID`。
