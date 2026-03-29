#!/usr/bin/env python3
"""
简单的 RSS 订阅推送脚本：
- 从根目录下的“rss.config”读取 RSS 订阅地址
- 与“data.json”比对，发现新内容后推送到多消息通知通道
"""

import html
import http.client
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import gzip
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
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
# 默认放宽到 7 天，减少更新频率较低站点漏推
MAX_FETCH_DAYS = int(os.getenv("MAX_FETCH_DAYS", "7"))
# 通知通道开关集中配置，按需启用一个或多个通道
NOTIFICATION_CHANNELS = {
    "telegram": False,
    "webhook": True,
}

# 抓取 RSS 时统一使用常见浏览器 UA，降低被源站拒绝概率
RSS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# 单个 RSS 地址的最大重试次数
RSS_FETCH_RETRIES = 3


def parse_feed_content(feed_data: bytes) -> dict:
    """解析 RSS 原始内容，兼容压缩与轻度 XML 格式问题。"""
    normalized_data = feed_data

    # 部分站点会直接返回 gzip 压缩内容，这里做兜底解压
    if normalized_data.startswith(b"\x1f\x8b"):
        try:
            normalized_data = gzip.decompress(normalized_data)
        except OSError:
            # 解压失败时继续走原始数据，避免直接中断
            pass

    feed = feedparser.parse(normalized_data)


    # bozo 或无条目时，兜底清理 XML 非法控制字符后重试
    if not getattr(feed, "bozo", False) and feed.entries:
        return feed
    decoded = normalized_data.decode("utf-8", errors="ignore").lstrip("\ufeff")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", decoded)
    cleaned_feed = feedparser.parse(cleaned)

    # 选择条目更多的结果，尽量避免 malformed XML 导致丢条目
    if len(cleaned_feed.entries) >= len(feed.entries):
        return cleaned_feed
    return feed



def parse_timestamp_text(value: str) -> Optional[float]:
    """将文本时间转为时间戳，兼容 RSS 常见时间格式。"""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, OverflowError):
        pass

    # 兼容 ISO 时间中的 Z 后缀
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


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
    # 兼容部分源站仅提供文本时间字段的情况
    for key in ("published", "updated", "pubDate", "dc_date", "date"):
        timestamp = parse_timestamp_text(str(entry.get(key, "")))
        if timestamp is not None:
            return timestamp
    return None


# 判断条目是否在允许范围
def is_recent_entry(entry: dict, max_days: int) -> bool:
    """判断条目是否在允许的时间范围内。"""
    if max_days <= 0:
        # 允许通过环境变量关闭时间过滤
        return True
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


def build_entry_summary(entry: dict, max_length: int = 200) -> str:
    """提取并清理条目摘要，便于多通道复用。"""
    summary = entry.get("summary", "") or entry.get("description", "")
    summary = html.unescape(summary)
    summary = re.sub(r"<[^>]+>", "", summary)
    summary = " ".join(summary.replace("\n", " ").split())
    return shorten_text(summary, max_length)


def extract_entry_thumbnail(entry: dict) -> str:
    """提取条目缩略图链接，优先使用 RSS 内置媒体字段。"""
    for key in ("media_thumbnail", "media_content"):
        media_value = entry.get(key)
        if isinstance(media_value, list) and media_value:
            url = media_value[0].get("url") or media_value[0].get("href")
            if url:
                return url
        if isinstance(media_value, dict):
            url = media_value.get("url") or media_value.get("href")
            if url:
                return url
    for enclosure in entry.get("enclosures", []):
        url = enclosure.get("href")
        if url:
            return url
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure":
            url = link.get("href")
            if url:
                return url
    return ""


# 构建发送内容
def build_message(entry: dict) -> str:
    """构建发送到 Telegram 的消息内容。"""
    title = escape_html(entry.get("title", "(无标题)"))
    source = escape_html(entry.get("source_title", "未知来源"))
    summary = escape_html(build_entry_summary(entry))
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


def build_webhook_payload(entry: dict) -> dict:
    """构建 Webhook 消息体，符合指定 JSON 结构。"""
    summary = build_entry_summary(entry)
    return {
        "content": summary,
        "msgType": 1,
        "param": {
            "appName": entry.get("source_title", "未知来源"),
            "title": entry.get("title", "(无标题)"),
            "lightAppId": "",
            "thumbUrl": extract_entry_thumbnail(entry),
            "webpageUrl": entry.get("link", ""),
            "customStyle": 0,
            "content": summary,
        },
    }


def send_to_webhook(webhook_url: str, payload: dict) -> None:
    """通过 Webhook 发送消息。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(webhook_url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"Webhook 发送失败，状态码：{response.status}")


def get_telegram_config() -> Optional[dict]:
    """获取 Telegram 通道配置，未启用或缺少配置时返回 None。"""
    if not NOTIFICATION_CHANNELS.get("telegram"):
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("未获取到 TELEGRAM_BOT_TOKEN，已跳过 Telegram 通道")
        return None
    return {"token": token, "chat_id": TELEGRAM_CHAT_ID}


def get_webhook_config() -> Optional[dict]:
    """获取 Webhook 通道配置，未启用或缺少配置时返回 None。"""
    if not NOTIFICATION_CHANNELS.get("webhook"):
        return None
    webhook_url = os.getenv("WEBHOOK")
    if not webhook_url:
        print("未获取到 WEBHOOK，已跳过 Webhook 通道")
        return None
    return {"url": webhook_url}


def parse_feed_with_retry(url: str, retries: int = RSS_FETCH_RETRIES) -> Optional[dict]:
    """抓取并解析单个 RSS，失败时短暂重试。"""
    for attempt in range(1, retries + 1):
        try:
            # 增加请求头，避免部分站点直接断开默认客户端连接
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": RSS_USER_AGENT,
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                feed_data = response.read()
            feed = parse_feed_content(feed_data)
            if getattr(feed, "bozo", False) and not feed.entries:
                print(f"RSS 解析异常（bozo），地址：{url}，原因：{feed.bozo_exception}")
            return feed
        except (HTTPError, URLError, TimeoutError, http.client.HTTPException) as exc:
            if attempt == retries:
                print(f"RSS 抓取失败（已重试 {retries} 次）：{url}，原因：{exc}")
                return None
            # 简单退避，减少连续请求触发限流
            time.sleep(attempt)
        except Exception as exc:
            print(f"RSS 抓取异常，已跳过：{url}，原因：{exc}")
            return None


# 抓取 RSS 条目
def fetch_entries(urls: Iterable[str]) -> List[dict]:
    """抓取所有 RSS 条目。"""
    entries: List[dict] = []
    for url in urls:
        feed = parse_feed_with_retry(url)
        if not feed:
            continue
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

    # 根据开关集中配置各通道，至少启用一个可用通道
    telegram_config = get_telegram_config()
    webhook_config = get_webhook_config()
    enabled_channels = {
        "telegram": telegram_config,
        "webhook": webhook_config,
    }
    enabled_channels = {key: value for key, value in enabled_channels.items() if value}
    if not enabled_channels:
        print("未启用任何可用的通知通道，请检查开关与环境变量配置")
        return

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
        # 逐条推送到启用的通道，支持多通道同时发送
        if "telegram" in enabled_channels:
            message = build_message(entry)
            try:
                send_to_telegram(
                    enabled_channels["telegram"]["token"],
                    enabled_channels["telegram"]["chat_id"],
                    message,
                )
            except Exception as exc:
                # 通道异常时不阻断整体流程，避免定时任务中断
                print(f"Telegram 通道发送失败：{exc}")
        if "webhook" in enabled_channels:
            payload = build_webhook_payload(entry)
            try:
                send_to_webhook(enabled_channels["webhook"]["url"], payload)
            except Exception as exc:
                # 通道异常时不阻断整体流程，避免定时任务中断
                print(f"Webhook 通道发送失败：{exc}")

    save_history(history)


if __name__ == "__main__":
    main()
