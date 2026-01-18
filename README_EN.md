# RSS Telegram Bot

A lightweight RSS-to-Telegram bot. It periodically fetches RSS feeds and pushes new entries to a Telegram channel/group, using a local history file to prevent duplicates.

> 中文版: [README.md](README.md)

## Features
- Maintain RSS subscriptions in `rss.config`, one URL per line.
- Track sent entries in `data.json` to avoid duplicates.
- Telegram message format includes source tags, bold titles, and summaries.
- Ready for scheduled runs via GitHub Actions.

## Project Layout
- `rss_bot.py`: Main script.
- `rss.config`: RSS subscription list.
- `data.json`: Push history.
- `.github/workflows/rss_bot.yml`: Scheduled workflow.

## Quick Start
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure RSS sources:
   ```text
   # Add one RSS URL per line in rss.config
   https://news.google.com/rss
   ```
3. Set Telegram Bot Token:
   ```bash
   export TELEGRAM_BOT_TOKEN="your-bot-token"
   ```
4. Run the script:
   ```bash
   python rss_bot.py
   ```

## Notes
- Lines starting with `#` in `rss.config` are ignored.
- `data.json` is created automatically on first run.
- To change the target chat, edit `TELEGRAM_CHAT_ID` in `rss_bot.py`.
