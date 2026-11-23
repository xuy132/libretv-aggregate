#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

CONFIG_DIR = "config"
OUTPUT_FILE = "dist/sources.json"

def fetch(url: str, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; LibreTV-Aggregator/1.0)"
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def load_config(path: str):
    """读取 txt 配置，返回 [(name, url), ...]"""
    items = []
    if not os.path.exists(path):
        return items
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = line.split("|", 1)
                name, url = name.strip(), url.strip()
            else:
                url = line
                name = urlparse(url).netloc or url
            if url:
                items.append((name, url))
    return items

def normalize_api(url: str) -> str:
    url = url.strip().rstrip("/")
    return url

def is_adult(name: str) -> bool:
    keywords = ["麻豆", "91", "番号", "黄色", "成人", "AV", "JAV", "色情", "18+", "HSCK", "老色"]
    return any(k.lower() in name.lower() for k in keywords)

def main():
    sources = []          # 点播源
    live_sources = []     # 直播源
    seen_vod = set()
    seen_live = set()

    # ========== 处理点播订阅 ==========
    vod_list = load_config(os.path.join(CONFIG_DIR, "vod_sources.txt"))
    for name, url in vod_list:
        print(f"[VOD] 处理: {name} -> {url}")
        try:
            content = fetch(url)
            data = json.loads(content)

            # 兼容多种格式
            items = []
            if isinstance(data, list):
                items = data
            elif "sources" in data:
                items = data["sources"]
            elif "data" in data:                    # ziyuanzhan online.json
                items = data["data"]
            elif "resources" in data:
                items = data["resources"]

            for item in items:
                # 统一字段
                api = item.get("url") or item.get("api") or ""
                if not api:
                    continue
                api = normalize_api(api)
                if "provide/vod" not in api and not api.endswith("/vod"):
                    # 尽量只保留采集接口
                    if not re.search(r"api\.php/provide/vod", api):
                        continue

                if api in seen_vod:
                    continue
                seen_vod.add(api)

                src_name = item.get("name") or name
                detail = item.get("detail") or item.get("link") or ""
                adult = item.get("isAdult", False) or is_adult(src_name)

                sources.append({
                    "name": src_name,
                    "url": api,
                    "detail": detail,
                    "isAdult": adult
                })
            print(f"    → 成功导入部分源")
        except Exception as e:
            print(f"    → 失败: {e}")

    # ========== 处理直播源 ==========
    live_list = load_config(os.path.join(CONFIG_DIR, "live_sources.txt"))
    for name, url in live_list:
        url = url.strip()
        if url in seen_live:
            continue
        seen_live.add(url)
        live_sources.append({
            "name": name,
            "url": url
        })
        print(f"[LIVE] 添加: {name}")

    # ========== 输出 ==========
    result = {
        "name": "聚合订阅（配置驱动）",
        "version": 2,
        "snapshotDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generatedBy": "libretv-aggregate",
        "sources": sources,
        "liveSources": live_sources
    }

    os.makedirs("dist", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n========== 完成 ==========")
    print(f"点播源: {len(sources)} 个")
    print(f"直播源: {len(live_sources)} 个")
    print(f"输出文件: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
