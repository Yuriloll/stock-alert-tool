#!/usr/bin/env python3
import argparse
import email.utils
import html
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl={language}&gl={region}&ceid={region}:{ceid_language}"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target}&dt=t&q={text}"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def get_json(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 stock-alert-tool/1.0",
            "Accept": "application/json,text/xml,application/xml,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 stock-alert-tool/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_quote(symbol):
    data = get_json(YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol)))
    result = data.get("chart", {}).get("result") or []
    if not result:
        err = data.get("chart", {}).get("error")
        raise RuntimeError(f"No chart result for {symbol}: {err}")

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    previous_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    market_time = meta.get("regularMarketTime")
    currency = meta.get("currency")

    if price is None:
        raise RuntimeError(f"No price returned for {symbol}")

    return {
        "symbol": symbol,
        "price": float(price),
        "previous_close": float(previous_close) if previous_close else None,
        "currency": currency,
        "market_time": market_time,
    }


def pct_change(now, before):
    if before in (None, 0):
        return None
    return (now - before) / before * 100.0


def format_time(ts, tz_name):
    if not ts:
        return "未知时间"
    tz = ZoneInfo(tz_name)
    return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def local_now(config):
    return datetime.now(ZoneInfo(config.get("timezone", "Asia/Shanghai")))


def parse_hhmm(value):
    parts = str(value).split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time value: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid time value: {value}")
    return hour, minute


def ceid_language(language):
    return str(language).split("-")[0] if language else "en"


def clean_news_title(title):
    title = " ".join(str(title).split())
    separators = [" - ", " | "]
    for separator in separators:
        if separator in title:
            title = title.rsplit(separator, 1)[0].strip()
    return title


def translate_text(config, text):
    translation_config = config.get("translation", {})
    if not translation_config.get("enabled", False):
        return text

    provider = translation_config.get("provider", "openai")
    if provider == "openai":
        return translate_text_openai(config, text)
    if provider != "google_free":
        return text

    target = translation_config.get("target_language", "zh-CN")
    url = GOOGLE_TRANSLATE_URL.format(
        target=urllib.parse.quote(target),
        text=urllib.parse.quote(text),
    )
    try:
        data = get_json(url, timeout=10)
        translated = "".join(part[0] for part in data[0] if part and part[0])
        return translated.strip() or text
    except Exception as exc:
        print(f"Translation failed: {exc}", file=sys.stderr, flush=True)
        return text


def translate_text_openai(config, text):
    translation_config = config.get("translation", {})
    api_key_env = translation_config.get("api_key_env", "OPENAI_API_KEY")
    api_key = (os.environ.get(api_key_env) or "").strip()
    if not api_key:
        print(f"OpenAI translation skipped: environment variable {api_key_env} is not set", file=sys.stderr, flush=True)
        return text

    target = translation_config.get("target_language", "zh-CN")
    model = translation_config.get("model", "gpt-5.4-mini")
    prompt = (
        "Translate the following financial news headline into concise, natural Simplified Chinese. "
        "Preserve company names, stock tickers, product names such as HBM, GPU, AI, DRAM, NAND, and numbers. "
        "Return only the translated headline, with no explanation.\n\n"
        f"Headline: {text}"
    )
    payload = json.dumps(
        {
            "model": model,
            "input": prompt,
            "max_output_tokens": 120,
            "store": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        log_openai_usage(config, data, model)
        translated = extract_openai_text(data)
        return translated.strip() or text
    except Exception as exc:
        print(f"OpenAI translation failed: {safe_error_message(exc)}", file=sys.stderr, flush=True)
        return text


def extract_openai_text(data):
    if data.get("output_text"):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    return "".join(chunks)


def log_openai_usage(config, data, model):
    usage = data.get("usage")
    if not usage:
        return

    translation_config = config.get("translation", {})
    log_file = translation_config.get("usage_log_file", "openai_usage_log.jsonl")
    if not os.path.isabs(log_file):
        log_file = os.path.join(os.getcwd(), log_file)

    record = {
        "timestamp": int(time.time()),
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"OpenAI usage log failed: {safe_error_message(exc)}", file=sys.stderr, flush=True)


def safe_error_message(exc):
    message = str(exc)
    if "sk-" in message:
        return "request failed; the error message contained a secret and was hidden"
    return message


def format_news_title(config, title):
    return translate_text(config, clean_news_title(title))


def push_message(config, title, body):
    print(f"\n[{title}]\n{body}\n", flush=True)
    push = config.get("push", {})

    if push.get("macos_notification"):
        script = (
            'display notification '
            + json.dumps(body[:240])
            + " with title "
            + json.dumps(title[:80])
        )
        try:
            subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    token = push.get("telegram_bot_token") or os.environ.get(push.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat_id = push.get("telegram_chat_id") or os.environ.get(push.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        messages = split_telegram_message(f"{title}\n{body}")
        for message in messages:
            payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
            try:
                urllib.request.urlopen(url, data=payload, timeout=15).read()
            except Exception as exc:
                print(f"Telegram push failed: {exc}", file=sys.stderr)

    webhook_url = push.get("webhook_url")
    if webhook_url:
        payload = json.dumps({"title": title, "body": body}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as exc:
            print(f"Webhook push failed: {exc}", file=sys.stderr)


def split_telegram_message(text, limit=3800):
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if line_len > limit:
            while line:
                chunks.append(line[:limit])
                line = line[limit:]
            continue
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))
    total = len(chunks)
    if total <= 1:
        return chunks
    return [f"{chunk}\n\n第 {i}/{total} 部分" for i, chunk in enumerate(chunks, start=1)]


def check_prices(config, state, force=False):
    tz_name = config.get("timezone", "Asia/Shanghai")
    state.setdefault("prices", {})
    print(f"Checking prices for {len(config.get('symbols', []))} symbols...", flush=True)
    for item in config.get("symbols", []):
        symbol = item["symbol"]
        name = item.get("name", symbol)
        try:
            quote = fetch_quote(symbol)
        except Exception as exc:
            print(f"Price check failed for {name} ({symbol}): {exc}", file=sys.stderr, flush=True)
            continue
        price = quote["price"]
        prev_close = quote["previous_close"]
        last_seen = state["prices"].get(symbol, {}).get("last_price")

        from_prev = pct_change(price, prev_close)
        from_last = pct_change(price, last_seen)
        alert_prev = item.get("alert_pct_from_previous_close")
        alert_last = item.get("alert_pct_from_last_seen")

        reasons = []
        if force:
            reasons.append("测试快照")
        if alert_prev is not None and from_prev is not None and abs(from_prev) >= float(alert_prev):
            reasons.append(f"较昨收 {from_prev:+.2f}%")
        if alert_last is not None and from_last is not None and abs(from_last) >= float(alert_last):
            reasons.append(f"较上次记录 {from_last:+.2f}%")

        if reasons:
            currency = item.get("currency") or quote.get("currency") or ""
            previous_text = f"{prev_close:,.2f}" if prev_close is not None else "n/a"
            last_text = f"{last_seen:,.2f}" if last_seen is not None else "n/a"
            body = "\n".join(
                [
                    f"{name} ({symbol})",
                    f"当前价格：{price:,.2f} {currency}",
                    f"昨收：{previous_text}",
                    f"上次记录：{last_text}",
                    f"触发原因：{', '.join(reasons)}",
                    f"行情时间：{format_time(quote.get('market_time'), tz_name)}",
                ]
            )
            push_message(config, "股票价格提醒", body)

        state["prices"][symbol] = {"last_price": price, "updated_at": int(time.time())}


def parse_google_news(xml_text):
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    items = []
    for item in channel.findall("item"):
        title = html.unescape(item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published_raw = item.findtext("pubDate") or ""
        published_ts = None
        if published_raw:
            try:
                published_ts = int(email.utils.parsedate_to_datetime(published_raw).timestamp())
            except Exception:
                published_ts = None
        if title and link:
            items.append({"title": title, "link": link, "published_ts": published_ts})
    return items


def check_news(config, state, force=False):
    news_config = config.get("news", {})
    if not news_config.get("enabled", False):
        return

    state.setdefault("news", {})
    language = news_config.get("language", "en-US")
    region = news_config.get("region", "US")
    print(f"Checking news for {len(news_config.get('keywords', []))} keywords...", flush=True)

    for keyword in news_config.get("keywords", []):
        query = urllib.parse.quote(keyword)
        url = GOOGLE_NEWS_RSS_URL.format(query=query, language=language, region=region, ceid_language=ceid_language(language))
        try:
            items = parse_google_news(get_text(url))
        except Exception as exc:
            print(f"News check failed for {keyword}: {exc}", file=sys.stderr, flush=True)
            continue
        seen = set(state["news"].get(keyword, []))
        new_links = []

        for article in items[:10]:
            if article["link"] in seen:
                continue
            new_links.append(article["link"])
            if not force:
                body = f"关键词：{keyword}\n标题：{format_news_title(config, article['title'])}"
                push_message(config, "市场新闻提醒", body)

        merged = list(dict.fromkeys(new_links + list(seen)))[:100]
        state["news"][keyword] = merged


def collect_price_snapshot(config):
    tz_name = config.get("timezone", "Asia/Shanghai")
    lines = []
    for item in config.get("symbols", []):
        symbol = item["symbol"]
        name = item.get("name", symbol)
        try:
            quote = fetch_quote(symbol)
            price = quote["price"]
            prev_close = quote["previous_close"]
            move = pct_change(price, prev_close)
            currency = item.get("currency") or quote.get("currency") or ""
            move_text = f"{move:+.2f}%" if move is not None else "n/a"
            time_text = format_time(quote.get("market_time"), tz_name)
            lines.append(f"- {name} ({symbol})：{price:,.2f} {currency}，较昨收 {move_text}，{time_text}")
        except Exception as exc:
            lines.append(f"- {name} ({symbol})：价格暂不可用（{exc}）")
    return lines


def collect_digest_news(config, digest_config):
    language = digest_config.get("language") or config.get("news", {}).get("language", "en-US")
    region = digest_config.get("region") or config.get("news", {}).get("region", "US")
    lookback_seconds = int(float(digest_config.get("lookback_hours", 24)) * 3600)
    cutoff = int(time.time()) - lookback_seconds
    max_per_keyword = int(digest_config.get("max_news_per_keyword", 3))
    lines = []
    seen_links = set()

    for keyword in digest_config.get("keywords", []):
        query = urllib.parse.quote(keyword)
        url = GOOGLE_NEWS_RSS_URL.format(query=query, language=language, region=region, ceid_language=ceid_language(language))
        try:
            items = parse_google_news(get_text(url))
        except Exception as exc:
            lines.append(f"- {keyword}：新闻暂不可用（{exc}）")
            continue

        count = 0
        for article in items:
            published_ts = article.get("published_ts")
            if published_ts is not None and published_ts < cutoff:
                continue
            if article["link"] in seen_links:
                continue
            seen_links.add(article["link"])
            lines.append(f"- [{keyword}] {format_news_title(config, article['title'])}")
            count += 1
            if count >= max_per_keyword:
                break

    return lines


def should_send_daily_digest(config, state, now=None):
    digest_config = config.get("daily_digest", {})
    if not digest_config.get("enabled", False):
        return False

    now = now or local_now(config)
    hour, minute = parse_hhmm(digest_config.get("time", "10:00"))
    if now.hour != hour or now.minute != minute:
        return False

    state.setdefault("daily_digest", {})
    today_key = now.strftime("%Y-%m-%d")
    return state["daily_digest"].get("last_sent_date") != today_key


def send_daily_digest(config, state, force=False):
    digest_config = config.get("daily_digest", {})
    if not digest_config.get("enabled", False):
        return

    now = local_now(config)
    if not force and not should_send_daily_digest(config, state, now=now):
        return

    price_lines = collect_price_snapshot(config)
    news_lines = collect_digest_news(config, digest_config)
    if not news_lines:
        news_lines = ["- 配置的时间窗口内没有找到匹配新闻。"]

    body = "\n".join(
        [
            f"每日市场简报（{now.strftime('%Y-%m-%d %H:%M %Z')}）",
            "",
            "价格",
            *price_lines,
            "",
            f"过去 {digest_config.get('lookback_hours', 24)} 小时新闻",
            *news_lines,
        ]
    )
    push_message(config, "每日市场简报", body)

    state.setdefault("daily_digest", {})
    state["daily_digest"]["last_sent_date"] = now.strftime("%Y-%m-%d")


def send_news_test(config):
    digest_config = dict(config.get("daily_digest", {}))
    if not digest_config:
        digest_config = dict(config.get("news", {}))
    digest_config["keywords"] = digest_config.get("keywords", [])[:5]
    digest_config["max_news_per_keyword"] = 1
    digest_config["lookback_hours"] = digest_config.get("lookback_hours", 24)

    news_lines = collect_digest_news(config, digest_config)
    if not news_lines:
        news_lines = ["- 配置的时间窗口内没有找到匹配新闻。"]
    body = "\n".join(["这是一条 Telegram 新闻推送测试。", *news_lines[:8]])
    push_message(config, "新闻测试", body)


def run_once(config_path, force=False):
    config = load_json(config_path, {})
    if not config:
        raise RuntimeError(f"Config is empty or missing: {config_path}")
    print("Running one monitor check...", flush=True)

    state_path = config.get("state_file", "state.json")
    if not os.path.isabs(state_path):
        state_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), state_path)
    state = load_json(state_path, {})

    check_prices(config, state, force=force)
    check_news(config, state, force=force)
    if force:
        send_daily_digest(config, state, force=True)
        send_news_test(config)
    save_json(state_path, state)


def run_daily_digest_once(config_path):
    config = load_json(config_path, {})
    if not config:
        raise RuntimeError(f"Config is empty or missing: {config_path}")
    print("Running daily market digest once...", flush=True)

    state_path = config.get("state_file", "state.json")
    if not os.path.isabs(state_path):
        state_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), state_path)
    state = load_json(state_path, {})
    send_daily_digest(config, state, force=True)
    save_json(state_path, state)


def run_loop(config_path):
    config = load_json(config_path, {})
    print("Daily market digest monitor started.", flush=True)
    print(f"Config: {os.path.abspath(config_path)}", flush=True)
    print(f"Daily digest time: {config.get('daily_digest', {}).get('time', '10:00')}", flush=True)
    print("Press Control+C to stop.", flush=True)

    while True:
        state_path = config.get("state_file", "state.json")
        if not os.path.isabs(state_path):
            state_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), state_path)
        state = load_json(state_path, {})

        try:
            send_daily_digest(config, state)
            save_json(state_path, state)
        except Exception as exc:
            print(f"Monitor error: {exc}", file=sys.stderr, flush=True)

        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Free stock price and market news alert monitor.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--snapshot", action="store_true", help="Push a startup snapshot for all configured symbols.")
    parser.add_argument("--daily-digest", action="store_true", help="Send the daily digest once and exit.")
    args = parser.parse_args()

    if args.daily_digest:
        run_daily_digest_once(args.config)
    elif args.once:
        run_once(args.config, force=args.snapshot)
    else:
        run_loop(args.config)


if __name__ == "__main__":
    main()
