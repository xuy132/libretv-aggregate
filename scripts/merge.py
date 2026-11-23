#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTV 聚合订阅生成器（增强版）
功能：
- 支持 LibreTV JSON / ziyuanzhan / TVBox 配置
- 自动过滤低评分、慢速源
- 按响应速度排序
- 直播源支持 EPG 节目单
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 可调参数 ====================
CONFIG_DIR = "config"
OUTPUT_FILE = "dist/sources.json"

# 过滤条件
MIN_RATING = 4.0          # 最低评分（ziyuanzhan 有 rating 才生效）
MAX_RESPONSE_MS = 3000    # 最大响应时间（毫秒），超过丢弃
MIN_UPTIME = 80           # 最低在线率（%）

# 是否开启成人源过滤（True = 标记为 isAdult）
MARK_ADULT = True

# 并发拉取数量
MAX_WORKERS = 8
# =================================================

def fetch(url: str, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; LibreTV-Aggregator/2.0)"
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def load_config(path: str):
    """读取 txt，支持 名称|URL 或 名称|URL|EPG"""
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
    """解析 TVBox 配置，只取 type=1 的直连采集站 + lives"""
    sources = []
    lives = []

    # 点播 sites
    for site in data.get("sites", []):
        if site.get("type") == 1 and site.get("api"):  # type=1 为苹果CMS
            api = normalize_api(site["api"])
            sources.append({
                "name": site.get("name") or "TVBox源",
                "url": api,
                "detail": site.get("ext") or "",
                "isAdult": is_adult(site.get("name", "")),
                "responseTime": 9999  # TVBox 无测速信息，排后面
            })

    # 直播 lives
    for live in data.get("lives", []):
        url = live.get("url") or live.get("channels")
        if url and isinstance(url, str) and (url.endswith(".m3u") or url.endswith(".m3u8") or "m3u" in url):
            lives.append({
                "name": live.get("name") or "TVBox直播",
                "url": url,
                "epg": live.get("epg") or live.get("epgUrl")
            })
    return sources, lives

def process_vod_item(item: dict, default_name: str):
    """把各种格式统一成 LibreTV 源结构，并返回是否通过过滤"""
    api = item.get("url") or item.get("api") or ""
    if not api:
        return None
    api = normalize_api(api)

    # 只保留看起来像采集接口的
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

    # ===== 过滤条件 =====
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
            # 多种格式兼容
            if isinstance(data, list):
                items = data
            elif "sources" in data:                 # LibreTV 标准
                items = data["sources"]
            elif "data" in data:                    # ziyuanzhan online.json
                items = data["data"]
            elif "resources" in data:
                items = data["resources"]
            elif "sites" in data:                   # TVBox
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

    # ---------- 直播源（带 EPG）----------
    live_list = load_config(os.path.join(CONFIG_DIR, "live_sources.txt"))
    print(f"\n开始处理 {len(live_list)} 个直播源...")

    for conf in live_list:
        url = conf["url"]
        if url in seen_live:
            continue
        seen_live.add(url)
        live_item = {
            "name": conf["name"],
            "url": url
        }
        if conf.get("epg"):
            live_item["epg"] = conf["epg"]
        live_sources.append(live_item)
        print(f"[LIVE] {conf['name']}" + (" (带EPG)" if conf.get("epg") else ""))

    # ---------- 按响应速度排序 ----------
    sources.sort(key=lambda x: (x.get("responseTime", 9999), -(x.get("rating") or 0)))

    # 清理临时字段
    for s in sources:
        s.pop("responseTime", None)
        s.pop("rating", None)

    # ---------- 输出 ----------
    result = {
        "name": "聚合订阅（过滤+排序+EPG）",
        "version": 2,
        "snapshotDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generatedBy": "libretv-aggregate-v2",
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
    print(f"点播源: {len(sources)} 个（已过滤低分/慢速）")
    print(f"直播源: {len(live_sources)} 个")
    print(f"输出: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
