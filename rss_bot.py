#!/usr/bin/env python3
"""
简单的 RSS 订阅推送脚本：
- 从根目录下的“rss.config”读取 RSS 订阅地址
- 与“data.json”比对，发现新内容后推送到 Telegram 频道
"""

import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Iterable, List, Optional, Set

try:
    import feedparser
except ImportError as exc:  # pragma: no cover - 仅提示依赖缺失
    raise SystemExit(
        "缺少依赖 feedparser，请先执行：pip install -r requirements.txt"
    ) from exc


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT_DIR, "rss.config")
HISTORY_PATH = os.path.join(ROOT_DIR, "data.json")
TELEGRAM_CHAT_ID = "-1003514584440"
TELEGRAM_API_BASE = "https://api.telegram.org"
# 最大获取天数，用于避免首次运行或长时间未运行导致一次推送过多
MAX_FETCH_DAYS = 1


# 确保配置文件存在
def ensure_config_exists() -> None:
    """确保配置文件存在，若不存在则创建默认模板。"""
    if os.path.exists(CONFIG_PATH):
        return
    default_content = """# 在这里填写 RSS 订阅地址，每行一个
# 以 # 开头的行会被忽略
https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans
"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        file.write(default_content)


# 确保历史记录文件存在
def ensure_history_exists() -> None:
    """确保历史记录文件存在，若不存在则创建空记录。"""
    if os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump([], file, ensure_ascii=False, indent=2)


# 加载配置文件中的 RSS 地址
def load_config_urls() -> List[str]:
    """读取配置文件中的 RSS 地址列表。"""
    urls: List[str] = []
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped)
    return urls


# 加载历史记录
def load_history() -> Set[str]:
    """读取历史记录（已推送过的条目 ID）。"""
    with open(HISTORY_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)
    return set(data)


# 保存历史记录
def save_history(history: Iterable[str]) -> None:
    """保存历史记录（仅在有新增推送时调用）。"""
    with open(HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump(sorted(history), file, ensure_ascii=False, indent=2)


# 提取条目 ID
def extract_entry_id(entry: dict) -> str:
    """提取条目唯一 ID，用于去重。"""
    entry_id = entry.get("id") or entry.get("guid")
    if entry_id:
        return str(entry_id)
    link = entry.get("link", "")
    title = entry.get("title", "")
    return f"{link}::{title}"


# 提取条目时间戳
def extract_entry_timestamp(entry: dict) -> Optional[float]:
    """提取 RSS 条目的时间戳（秒），用于过滤过旧内容。"""
    # feedparser 会将时间字段解析为 time.struct_time
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed_time = entry.get(key)
        if parsed_time:
            return time.mktime(parsed_time)
    return None


# 判断条目是否在允许范围
def is_recent_entry(entry: dict, max_days: int) -> bool:
    """判断条目是否在允许的时间范围内。"""
    timestamp = extract_entry_timestamp(entry)
    if timestamp is None:
        # 如果没有时间信息，默认不处理，避免误推送过旧内容
        return False
    max_age_seconds = max_days * 24 * 60 * 60
    return (time.time() - timestamp) <= max_age_seconds


# 缩短文本避免超长
def shorten_text(text: str, max_length: int = 200) -> str:
    """缩短文本，避免 Telegram 消息过长。"""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


# 转义 Telegram HTML 格式需要的字符
def escape_html(text: str) -> str:
    """转义 HTML，避免 Telegram HTML 模式解析出错。"""
    return html.escape(text, quote=True)


# 构建发送内容
def build_message(entry: dict) -> str:
    """构建发送到 Telegram 的消息内容。"""
    title = escape_html(entry.get("title", "(无标题)"))
    source = escape_html(entry.get("source_title", "未知来源"))
    summary = entry.get("summary", "") or entry.get("description", "")
    summary = shorten_text(summary.replace("\n", " ").strip())
    summary = escape_html(summary)
    link = escape_html(entry.get("link", ""))
    parts = [f"[{source}] 📰 <b>{title}</b>"]
    if summary:
        # 标题与简介之间留空行
        parts.append("")
        parts.append(f"📝 {summary}")
    if link:
        parts.append(f"🔗 {link}")
    return "\n".join(parts)


# 发送 Telegram 消息
def send_to_telegram(token: str, chat_id: str, message: str) -> None:
    """通过 Telegram Bot 发送消息。"""
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"Telegram 发送失败，状态码：{response.status}")


# 抓取 RSS 条目
def fetch_entries(urls: Iterable[str]) -> List[dict]:
    """抓取所有 RSS 条目。"""
    entries: List[dict] = []
    for url in urls:
        feed = feedparser.parse(url)
        source_title = feed.feed.get("title") or feed.feed.get("subtitle") or url
        for entry in feed.entries:
            # 为条目补充来源信息
            entry["source_title"] = source_title
            entries.append(entry)
        time.sleep(0.5)
    return entries


# 主流程
def main() -> None:
    """主流程：读取配置、对比历史、发送新消息。"""
    ensure_config_exists()
    ensure_history_exists()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("未获取到 TELEGRAM_BOT_TOKEN，请在环境变量中配置")
        sys.exit(1)

    urls = load_config_urls()
    if not urls:
        print("配置文件中没有可用的 RSS 地址")
        return

    history = load_history()
    entries = fetch_entries(urls)

    new_entries = []
    for entry in entries:
        # 只处理最近 MAX_FETCH_DAYS 天内的新闻，避免一次推送过多
        if not is_recent_entry(entry, MAX_FETCH_DAYS):
            continue
        entry_id = extract_entry_id(entry)
        if entry_id in history:
            continue
        new_entries.append(entry)
        history.add(entry_id)

    if not new_entries:
        return

    for entry in new_entries:
        message = build_message(entry)
        send_to_telegram(token, TELEGRAM_CHAT_ID, message)

    save_history(history)


if __name__ == "__main__":
    main()
