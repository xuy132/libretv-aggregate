#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTV 聚合订阅生成器（增强版 + 直播测活）
功能：
- 支持 LibreTV JSON / ziyuanzhan / TVBox
- 自动过滤低评分、慢速源
- 按响应速度排序
- 直播源支持 EPG
- 自动统计直播频道数量 + 抽样测活
"""

import json
import os
import re
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

# ==================== 可调参数 ====================
CONFIG_DIR = "config"
OUTPUT_FILE = "dist/sources.json"

MIN_RATING = 4.0
MAX_RESPONSE_MS = 3000
MIN_UPTIME = 80

MARK_ADULT = True
MAX_WORKERS = 8

# 直播测活参数
LIVE_CHECK_SAMPLE = 8          # 每个源最多抽样测几个频道
LIVE_CHECK_TIMEOUT = 6         # 单个频道测活超时（秒）
LIVE_MIN_CHANNELS = 5          # 频道数太少的源可选择过滤
# =================================================

def fetch(url: str, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; LibreTV-Aggregator/2.1)"
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def load_config(path: str):
    items = []
    if not os.path.exists(path):
        return items
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 1:
                items.append({"name": urlparse(parts[0]).netloc or parts[0], "url": parts[0]})
            elif len(parts) == 2:
                items.append({"name": parts[0], "url": parts[1]})
            else:
                items.append({"name": parts[0], "url": parts[1], "epg": parts[2] or None})
    return items

def normalize_api(url: str) -> str:
    return url.strip().rstrip("/")

def is_adult(name: str) -> bool:
    if not MARK_ADULT:
        return False
    keywords = ["麻豆", "91", "番号", "黄色", "成人", "AV", "JAV", "色情", "18+", "HSCK", "老色", "杏吧", "黑料"]
    return any(k.lower() in name.lower() for k in keywords)

def parse_tvbox(data: dict):
    sources, lives = [], []
    for site in data.get("sites", []):
        if site.get("type") == 1 and site.get("api"):
            api = normalize_api(site["api"])
            sources.append({
                "name": site.get("name") or "TVBox源",
                "url": api,
                "detail": site.get("ext") or "",
                "isAdult": is_adult(site.get("name", "")),
                "responseTime": 9999
            })
    for live in data.get("lives", []):
        url = live.get("url") or live.get("channels")
        if url and isinstance(url, str) and ("m3u" in url.lower() or url.endswith(".m3u8")):
            lives.append({
                "name": live.get("name") or "TVBox直播",
                "url": url,
                "epg": live.get("epg") or live.get("epgUrl")
            })
    return sources, lives

def process_vod_item(item: dict, default_name: str):
    api = item.get("url") or item.get("api") or ""
    if not api:
        return None
    api = normalize_api(api)
    if "provide/vod" not in api and not re.search(r"/api\.php/provide/vod", api):
        return None

    name = item.get("name") or default_name
    detail = item.get("detail") or item.get("link") or ""
    rating = item.get("rating")
    response_time = item.get("responseTime") or item.get("health", {}).get("response_time_ms") or 9999
    uptime_str = item.get("uptime", "100%")
    try:
        uptime = float(str(uptime_str).replace("%", ""))
    except:
        uptime = 100

    if rating is not None and rating < MIN_RATING:
        return None
    if response_time > MAX_RESPONSE_MS:
        return None
    if uptime < MIN_UPTIME:
        return None

    return {
        "name": name,
        "url": api,
        "detail": detail,
        "isAdult": item.get("isAdult", False) or is_adult(name),
        "responseTime": response_time,
        "rating": rating
    }

def parse_m3u_channels(m3u_content: str):
    """解析 M3U，返回频道流地址列表"""
    channels = []
    lines = m3u_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            # 下一行通常是流地址
            if i + 1 < len(lines):
                stream = lines[i + 1].strip()
                if stream and not stream.startswith("#"):
                    channels.append(stream)
            i += 2
        else:
            i += 1
    return channels

def check_stream(url: str, timeout=LIVE_CHECK_TIMEOUT) -> bool:
    """简单测活：能建立连接并读到一点数据就算可用"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; LibreTV-Checker/1.0)",
            "Connection": "close"
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # 读一点数据确认不是空响应
            chunk = r.read(1024)
            return len(chunk) > 0
    except Exception:
        return False

def analyze_live_source(url: str, name: str):
    """统计频道数 + 抽样测活"""
    try:
        content = fetch(url, timeout=25)
        channels = parse_m3u_channels(content)
        total = len(channels)
        if total == 0:
            return total, 0, 0.0

        # 抽样测活
        sample = channels[:LIVE_CHECK_SAMPLE] if total > LIVE_CHECK_SAMPLE else channels
        alive = 0
        with ThreadPoolExecutor(max_workers=min(6, len(sample))) as executor:
            futures = [executor.submit(check_stream, u) for u in sample]
            for f in as_completed(futures):
                if f.result():
                    alive += 1

        alive_rate = round(alive / len(sample) * 100, 1) if sample else 0.0
        return total, alive, alive_rate
    except Exception as e:
        print(f"    → 测活失败: {e}")
        return 0, 0, 0.0

def main():
    sources = []
    live_sources = []
    seen_vod = set()
    seen_live = set()

    # ---------- 点播源 ----------
    vod_list = load_config(os.path.join(CONFIG_DIR, "vod_sources.txt"))
    print(f"开始处理 {len(vod_list)} 个点播订阅...\n")

    for conf in vod_list:
        name, url = conf["name"], conf["url"]
        print(f"[VOD] {name}")
        try:
            content = fetch(url)
            data = json.loads(content)

            items = []
            if isinstance(data, list):
                items = data
            elif "sources" in data:
                items = data["sources"]
            elif "data" in data:
                items = data["data"]
            elif "resources" in data:
                items = data["resources"]
            elif "sites" in data:
                tv_sources, tv_lives = parse_tvbox(data)
                for s in tv_sources:
                    if s["url"] not in seen_vod:
                        seen_vod.add(s["url"])
                        sources.append(s)
                for l in tv_lives:
                    if l["url"] not in seen_live:
                        seen_live.add(l["url"])
                        live_sources.append(l)
                print(f"    → TVBox 格式，导入点播 {len(tv_sources)} + 直播 {len(tv_lives)}")
                continue

            count = 0
            for item in items:
                parsed = process_vod_item(item, name)
                if parsed and parsed["url"] not in seen_vod:
                    seen_vod.add(parsed["url"])
                    sources.append(parsed)
                    count += 1
            print(f"    → 有效源 {count} 个")
        except Exception as e:
            print(f"    → 失败: {e}")

    # ---------- 直播源（带 EPG + 测活）----------
    live_list = load_config(os.path.join(CONFIG_DIR, "live_sources.txt"))
    print(f"\n开始处理 {len(live_list)} 个直播源（含测活）...\n")

    for conf in live_list:
        url = conf["url"]
        if url in seen_live:
            continue
        seen_live.add(url)

        print(f"[LIVE] {conf['name']}")
        total, alive, rate = analyze_live_source(url, conf["name"])
        print(f"    → 频道数: {total} | 抽样存活: {alive}/{min(total, LIVE_CHECK_SAMPLE)} ({rate}%)")

        # 可选：过滤频道太少的源
        # if total < LIVE_MIN_CHANNELS:
        #     print("    → 频道过少，已跳过")
        #     continue

        live_item = {
            "name": conf["name"],
            "url": url,
            "channelCount": total,
            "sampleAlive": alive,
            "sampleAliveRate": rate
        }
        if conf.get("epg"):
            live_item["epg"] = conf["epg"]
        live_sources.append(live_item)

    # ---------- 按响应速度排序 ----------
    sources.sort(key=lambda x: (x.get("responseTime", 9999), -(x.get("rating") or 0)))

    for s in sources:
        s.pop("responseTime", None)
        s.pop("rating", None)

    # ---------- 输出 ----------
    result = {
        "name": "聚合订阅（过滤+排序+EPG+测活）",
        "version": 2,
        "snapshotDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generatedBy": "libretv-aggregate-v2.1",
        "filter": {
            "minRating": MIN_RATING,
            "maxResponseMs": MAX_RESPONSE_MS,
            "minUptime": MIN_UPTIME
        },
        "sources": sources,
        "liveSources": live_sources
    }

    os.makedirs("dist", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n========== 完成 ==========")
    print(f"点播源: {len(sources)} 个")
    print(f"直播源: {len(live_sources)} 个")
    print(f"输出: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
