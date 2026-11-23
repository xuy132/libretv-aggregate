#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTV 聚合订阅生成器 v3.0

改动要点（相对 v2.1）：
- 无上游 responseTime 时不再默认 9999 直接淘汰，改为实测延迟
- TVBox / 普通 JSON 走同一套过滤与探测
- 点播 API 并发轻量测活，按实测延迟排序
- 直播均匀抽样测活，按存活率过滤与排序
- 输出前截断到 LibreTV 上限（点播 100 / 直播 50）
- 支持黑名单、拉取重试、简单变更摘要
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

# ==================== 可调参数 ====================
CONFIG_DIR = "config"
OUTPUT_FILE = "dist/sources.json"
# ---------- 同时生成 TVBox 配置 ----------
tvbox_sites = []
for i, s in enumerate(sources):
    tvbox_sites.append({
        "key": f"agg_{i}",
        "name": s["name"],
        "type": 1,                    # 苹果 CMS
        "api": s["url"],
        "searchable": 1,
        "quickSearch": 1,
        "filterable": 1
    })

tvbox_lives = []
for live in live_sources:
    item = {
        "name": live["name"],
        "type": 0,
        "url": live["url"]
    }
    if live.get("epg"):
        item["epg"] = live["epg"]
    tvbox_lives.append(item)

tvbox_config = {
    "sites": tvbox_sites,
    "lives": tvbox_lives
}

with open("dist/tvbox.json", "w", encoding="utf-8") as f:
    json.dump(tvbox_config, f, ensure_ascii=False, indent=2)

print(f"TVBox 配置已生成: dist/tvbox.json")
REPORT_FILE = "dist/report.json"

# 上游自报指标（有则用，无则跳过该项过滤，不靠默认值误杀）
MIN_RATING = 4.0
MIN_UPTIME = 80.0

# 实测延迟过滤 / 排序（毫秒）
PROBE_TIMEOUT = 5
MAX_PROBE_MS = 4000          # 实测超过此值视为不可用
PROBE_WORKERS = 12

MARK_ADULT = True
FETCH_WORKERS = 6
FETCH_RETRIES = 2
FETCH_TIMEOUT = 20

# LibreTV 硬上限
MAX_VOD_SOURCES = 100
MAX_LIVE_SOURCES = 50

# 直播测活
LIVE_CHECK_SAMPLE = 8
LIVE_CHECK_TIMEOUT = 6
LIVE_MIN_CHANNELS = 3
LIVE_MIN_ALIVE_RATE = 25.0   # 抽样存活率低于此值则丢弃
LIVE_PROBE_WORKERS = 6

UA = "Mozilla/5.0 (compatible; LibreTV-Aggregator/3.0)"
# =================================================


def fetch(url: str, timeout: int = FETCH_TIMEOUT, retries: int = FETCH_RETRIES) -> str:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise last_err  # type: ignore[misc]


def load_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
    return out


def load_config(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in load_lines(path):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 1:
            items.append({"name": urlparse(parts[0]).netloc or parts[0], "url": parts[0]})
        elif len(parts) == 2:
            items.append({"name": parts[0], "url": parts[1]})
        else:
            items.append({"name": parts[0], "url": parts[1], "epg": parts[2] or None})
    return items


def load_blacklist() -> set[str]:
    """每行一个 URL 前缀或完整 URL / host。"""
    raw = load_lines(os.path.join(CONFIG_DIR, "blacklist.txt"))
    return {x.lower().rstrip("/") for x in raw}


def load_adult_keywords() -> list[str]:
    path = os.path.join(CONFIG_DIR, "adult_keywords.txt")
    kws = load_lines(path)
    if kws:
        return kws
    return [
        "麻豆", "91", "番号", "黄色", "成人", "AV", "JAV", "色情",
        "18+", "HSCK", "老色", "杏吧", "黑料", "色色", "福利姬",
    ]


ADULT_KEYWORDS = load_adult_keywords()
BLACKLIST = load_blacklist()


def normalize_api(url: str) -> str:
    return url.strip().rstrip("/")


def normalize_url_key(url: str) -> str:
    """去 scheme 差异、去尾斜杠，便于去重。"""
    try:
        p = urlparse(url.strip())
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url.strip().rstrip("/").lower()


def is_blacklisted(url: str) -> bool:
    if not BLACKLIST:
        return False
    u = url.lower().rstrip("/")
    key = normalize_url_key(url)
    for b in BLACKLIST:
        if u.startswith(b) or key.startswith(b) or b in u or b in key:
            return True
    return False


def is_adult(name: str) -> bool:
    if not MARK_ADULT:
        return False
    lower = name.lower()
    return any(k.lower() in lower for k in ADULT_KEYWORDS)


def is_apple_cms(url: str) -> bool:
    u = url.lower()
    return "provide/vod" in u or bool(re.search(r"/api\.php/provide/vod", u))


def parse_uptime(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def parse_rating(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def parse_response_ms(item: dict[str, Any]) -> int | None:
    """仅在上游明确给出时返回，否则 None（不默认 9999）。"""
    for key in ("responseTime", "response_time", "response_time_ms", "latency"):
        if key in item and item[key] is not None:
            try:
                return int(float(item[key]))
            except Exception:
                pass
    health = item.get("health")
    if isinstance(health, dict):
        for key in ("response_time_ms", "responseTime", "latency"):
            if key in health and health[key] is not None:
                try:
                    return int(float(health[key]))
                except Exception:
                    pass
    return None


def process_vod_item(item: dict[str, Any], default_name: str) -> dict[str, Any] | None:
    api = item.get("url") or item.get("api") or ""
    if not api:
        return None
    api = normalize_api(str(api))
    if not is_apple_cms(api):
        return None
    if is_blacklisted(api):
        return None

    name = str(item.get("name") or default_name)
    detail = str(item.get("detail") or item.get("link") or item.get("ext") or "")

    rating = parse_rating(item.get("rating"))
    if rating is not None and rating < MIN_RATING:
        return None

    uptime = parse_uptime(item.get("uptime"))
    if uptime is not None and uptime < MIN_UPTIME:
        return None

    # 上游自报延迟仅作参考，不在这里直接淘汰（交给实测）
    reported_ms = parse_response_ms(item)

    return {
        "name": name,
        "url": api,
        "detail": detail,
        "isAdult": bool(item.get("isAdult", False)) or is_adult(name),
        "reportedMs": reported_ms,
        "rating": rating,
        "responseTime": reported_ms if reported_ms is not None else 99999,
    }


def parse_tvbox(data: dict[str, Any], default_name: str) -> tuple[list[dict], list[dict]]:
    sources: list[dict] = []
    lives: list[dict] = []

    for site in data.get("sites") or []:
        if not isinstance(site, dict):
            continue
        # type=1 为 JSON 接口；部分配置省略 type 但 api 是 Apple CMS
        stype = site.get("type")
        api = site.get("api") or site.get("url")
        if not api:
            continue
        api = normalize_api(str(api))
        if stype not in (None, 0, 1) and stype != 1:
            # 明确非 JSON 直连则跳过；仍允许 type 缺失且命中 Apple CMS
            if not is_apple_cms(api):
                continue
        if stype == 1 or is_apple_cms(api):
            parsed = process_vod_item(
                {
                    "name": site.get("name") or default_name,
                    "url": api,
                    "detail": site.get("ext") or site.get("detail") or "",
                    "isAdult": site.get("isAdult"),
                    "rating": site.get("rating"),
                    "uptime": site.get("uptime"),
                    "responseTime": site.get("responseTime"),
                },
                default_name,
            )
            if parsed:
                sources.append(parsed)

    for live in data.get("lives") or []:
        if not isinstance(live, dict):
            continue
        url = live.get("url") or live.get("channels")
        if not url or not isinstance(url, str):
            continue
        low = url.lower()
        if "m3u" not in low and not low.endswith(".m3u8") and not low.endswith(".txt"):
            continue
        if is_blacklisted(url):
            continue
        lives.append(
            {
                "name": live.get("name") or "TVBox直播",
                "url": url.strip(),
                "epg": live.get("epg") or live.get("epgUrl"),
            }
        )
    return sources, lives


def probe_vod_api(url: str, timeout: int = PROBE_TIMEOUT) -> int | None:
    """
    轻量测活：请求 list 接口，成功解析出 JSON 则返回耗时 ms，否则 None。
    """
    candidates = [
        f"{url}?ac=list",
        f"{url}?ac=list&pg=1",
        url,
    ]
    for test_url in candidates:
        try:
            req = urllib.request.Request(
                test_url,
                headers={"User-Agent": UA, "Connection": "close"},
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(65536)
            ms = int((time.perf_counter() - t0) * 1000)
            text = body.decode("utf-8", errors="ignore").lstrip()
            if not text:
                continue
            # 粗判 JSON
            if text[0] not in "{[":
                continue
            json.loads(text if len(body) < 65536 else text + "]}"[:0] or text)
            # 上面 loads 对截断可能失败，再宽松一次
            try:
                json.loads(text)
            except Exception:
                # 截断的大 JSON：只要看起来像对象/数组且有 list/code 等字段特征也算活
                if not re.search(r'"(list|code|msg|data|page)"\s*:', text):
                    continue
            return ms
        except Exception:
            continue
    return None


def probe_all_vod(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sources:
        return []

    print(f"\n开始点播实测（并发 {PROBE_WORKERS}，超时 {PROBE_TIMEOUT}s）...\n")
    results: list[dict[str, Any]] = []

    def work(item: dict[str, Any]) -> dict[str, Any] | None:
        ms = probe_vod_api(item["url"])
        if ms is None:
            print(f"  ✗ {item['name']}  探测失败")
            return None
        if ms > MAX_PROBE_MS:
            print(f"  ✗ {item['name']}  {ms}ms > {MAX_PROBE_MS}ms")
            return None
        item = dict(item)
        item["responseTime"] = ms
        print(f"  ✓ {item['name']}  {ms}ms")
        return item

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as ex:
        futs = {ex.submit(work, s): s for s in sources}
        for fut in as_completed(futs):
            got = fut.result()
            if got:
                results.append(got)
    return results


def parse_m3u_channels(m3u_content: str) -> list[str]:
    channels: list[str] = []
    lines = m3u_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            if i + 1 < len(lines):
                stream = lines[i + 1].strip()
                if stream and not stream.startswith("#"):
                    channels.append(stream)
            i += 2
        else:
            # 兼容纯 txt：名称,url 或 直接 url
            if line and not line.startswith("#"):
                if "," in line and not line.startswith("http"):
                    maybe = line.split(",", 1)[-1].strip()
                    if maybe.startswith("http"):
                        channels.append(maybe)
                elif line.startswith("http"):
                    channels.append(line)
            i += 1
    return channels


def uniform_sample(items: list[str], n: int) -> list[str]:
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[len(items) // 2]]
    # 均匀取点，略加抖动避免永远同一批
    step = (len(items) - 1) / (n - 1)
    idxs = []
    for i in range(n):
        base = int(round(i * step))
        jitter = random.randint(-1, 1) if len(items) > n + 2 else 0
        idxs.append(min(max(base + jitter, 0), len(items) - 1))
    # 去重并保持数量
    seen = set()
    out = []
    for i in idxs:
        if i not in seen:
            seen.add(i)
            out.append(items[i])
    # 不足则随机补
    while len(out) < n:
        j = random.randrange(len(items))
        if j not in seen:
            seen.add(j)
            out.append(items[j])
    return out


def check_stream(url: str, timeout: int = LIVE_CHECK_TIMEOUT) -> bool:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Connection": "close"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            chunk = r.read(1024)
            return len(chunk) > 0
    except Exception:
        return False


def analyze_live_source(url: str) -> tuple[int, int, float]:
    try:
        content = fetch(url, timeout=25)
        channels = parse_m3u_channels(content)
        total = len(channels)
        if total == 0:
            return 0, 0, 0.0

        sample = uniform_sample(channels, min(LIVE_CHECK_SAMPLE, total))
        alive = 0
        with ThreadPoolExecutor(max_workers=min(LIVE_PROBE_WORKERS, len(sample))) as ex:
            futs = [ex.submit(check_stream, u) for u in sample]
            for f in as_completed(futs):
                if f.result():
                    alive += 1
        rate = round(alive / len(sample) * 100, 1) if sample else 0.0
        return total, alive, rate
    except Exception as e:
        print(f"    → 测活失败: {e}")
        return 0, 0, 0.0


def extract_items_from_payload(data: Any) -> tuple[list, str]:
    """返回 (items 或 None 表示 TVBox 已处理标记, kind)。"""
    if isinstance(data, list):
        return data, "list"
    if not isinstance(data, dict):
        return [], "unknown"
    if "sites" in data:
        return data, "tvbox"
    for key in ("sources", "data", "resources"):
        if key in data and isinstance(data[key], list):
            return data[key], key
    return [], "empty"


def process_one_vod_feed(conf: dict[str, Any], seen_vod: set[str], seen_live: set[str]) -> tuple[list, list]:
    name, url = conf["name"], conf["url"]
    print(f"[VOD] {name}")
    sources: list[dict] = []
    lives: list[dict] = []
    try:
        content = fetch(url)
        data = json.loads(content)
        items, kind = extract_items_from_payload(data)

        if kind == "tvbox":
            tv_sources, tv_lives = parse_tvbox(data, name)
            for s in tv_sources:
                key = normalize_url_key(s["url"])
                if key in seen_vod:
                    continue
                seen_vod.add(key)
                sources.append(s)
            for l in tv_lives:
                key = normalize_url_key(l["url"])
                if key in seen_live or is_blacklisted(l["url"]):
                    continue
                seen_live.add(key)
                lives.append(l)
            print(f"    → TVBox：点播候选 {len(tv_sources)}，直播 {len(tv_lives)}，入库点播 {len(sources)}")
            return sources, lives

        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            parsed = process_vod_item(item, name)
            if not parsed:
                continue
            key = normalize_url_key(parsed["url"])
            if key in seen_vod:
                continue
            seen_vod.add(key)
            sources.append(parsed)
            count += 1
        print(f"    → 候选有效源 {count} 个")
    except Exception as e:
        print(f"    → 失败: {e}")
    return sources, lives


def strip_internal_fields(item: dict[str, Any], keep: set[str]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k in keep and v not in (None, "")}


def load_previous_urls(path: str) -> tuple[set[str], set[str]]:
    if not os.path.exists(path):
        return set(), set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        vod = {normalize_url_key(s["url"]) for s in data.get("sources") or [] if s.get("url")}
        live = {normalize_url_key(s["url"]) for s in data.get("liveSources") or [] if s.get("url")}
        return vod, live
    except Exception:
        return set(), set()


def main() -> None:
    global ADULT_KEYWORDS, BLACKLIST
    ADULT_KEYWORDS = load_adult_keywords()
    BLACKLIST = load_blacklist()

    prev_vod, prev_live = load_previous_urls(OUTPUT_FILE)

    candidates: list[dict] = []
    live_raw: list[dict] = []
    seen_vod: set[str] = set()
    seen_live: set[str] = set()

    # ---------- 点播上游（可并发拉订阅）----------
    vod_list = load_config(os.path.join(CONFIG_DIR, "vod_sources.txt"))
    print(f"开始处理 {len(vod_list)} 个点播订阅...\n")

    # 串行拉订阅更稳（上游少）；内部探测再并发
    for conf in vod_list:
        s, l = process_one_vod_feed(conf, seen_vod, seen_live)
        candidates.extend(s)
        live_raw.extend(l)

    # 实测过滤
    sources = probe_all_vod(candidates)
    sources.sort(key=lambda x: (x.get("responseTime", 99999), -(x.get("rating") or 0)))

    if len(sources) > MAX_VOD_SOURCES:
        print(f"\n点播截断: {len(sources)} → {MAX_VOD_SOURCES}")
        sources = sources[:MAX_VOD_SOURCES]

    vod_out = [
        strip_internal_fields(s, {"name", "url", "detail", "isAdult"})
        for s in sources
    ]

    # ---------- 直播源 ----------
    live_list = load_config(os.path.join(CONFIG_DIR, "live_sources.txt"))
    print(f"\n开始处理 {len(live_list)} 个直播源（含测活）...\n")

    live_sources: list[dict] = []

    # 合并配置里的直播 + 上游 TVBox 带出的直播
    pending_live: list[dict] = []
    for conf in live_list:
        url = conf["url"]
        key = normalize_url_key(url)
        if key in seen_live or is_blacklisted(url):
            continue
        seen_live.add(key)
        pending_live.append(conf)
    for conf in live_raw:
        url = conf["url"]
        key = normalize_url_key(url)
        if key in seen_live or is_blacklisted(url):
            continue
        seen_live.add(key)
        pending_live.append(conf)

    for conf in pending_live:
        print(f"[LIVE] {conf['name']}")
        total, alive, rate = analyze_live_source(conf["url"])
        sample_n = min(total, LIVE_CHECK_SAMPLE) if total else 0
        print(f"    → 频道数: {total} | 抽样存活: {alive}/{sample_n} ({rate}%)")

        if total < LIVE_MIN_CHANNELS:
            print("    → 频道过少，已跳过")
            continue
        if rate < LIVE_MIN_ALIVE_RATE:
            print(f"    → 存活率 < {LIVE_MIN_ALIVE_RATE}%，已跳过")
            continue

        live_item = {
            "name": conf["name"],
            "url": conf["url"],
            "channelCount": total,
            "sampleAlive": alive,
            "sampleAliveRate": rate,
        }
        if conf.get("epg"):
            live_item["epg"] = conf["epg"]
        live_sources.append(live_item)

    live_sources.sort(
        key=lambda x: (-(x.get("sampleAliveRate") or 0), -(x.get("channelCount") or 0))
    )
    if len(live_sources) > MAX_LIVE_SOURCES:
        print(f"\n直播截断: {len(live_sources)} → {MAX_LIVE_SOURCES}")
        live_sources = live_sources[:MAX_LIVE_SOURCES]

    # 输出给 LibreTV 的字段
    live_out = []
    for item in live_sources:
        o = {"name": item["name"], "url": item["url"]}
        if item.get("epg"):
            o["epg"] = item["epg"]
        live_out.append(o)

    # ---------- 变更摘要 ----------
    cur_vod = {normalize_url_key(s["url"]) for s in vod_out}
    cur_live = {normalize_url_key(s["url"]) for s in live_out}
    added_vod = len(cur_vod - prev_vod)
    removed_vod = len(prev_vod - cur_vod)
    added_live = len(cur_live - prev_live)
    removed_live = len(prev_live - cur_live)

    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    result = {
        "name": "聚合订阅（实测过滤+排序+EPG+测活）",
        "version": 2,
        "snapshotDate": snapshot,
        "generatedBy": "libretv-aggregate-v3.0",
        "filter": {
            "minRating": MIN_RATING,
            "minUptime": MIN_UPTIME,
            "maxProbeMs": MAX_PROBE_MS,
            "liveMinChannels": LIVE_MIN_CHANNELS,
            "liveMinAliveRate": LIVE_MIN_ALIVE_RATE,
            "maxVod": MAX_VOD_SOURCES,
            "maxLive": MAX_LIVE_SOURCES,
        },
        "stats": {
            "vod": len(vod_out),
            "live": len(live_out),
            "vodAdded": added_vod,
            "vodRemoved": removed_vod,
            "liveAdded": added_live,
            "liveRemoved": removed_live,
            "candidatesBeforeProbe": len(candidates),
        },
        "sources": vod_out,
        "liveSources": live_out,
    }

    os.makedirs("dist", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 详细报告（含测活字段，不给客户端用）
    report = {
        "snapshotDate": snapshot,
        "vodProbed": [
            {
                "name": s["name"],
                "url": s["url"],
                "responseTime": s.get("responseTime"),
                "rating": s.get("rating"),
                "isAdult": s.get("isAdult"),
            }
            for s in sources
        ],
        "liveProbed": live_sources,
        "stats": result["stats"],
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== 完成 ==========")
    print(f"点播源: {len(vod_out)} 个（候选 {len(candidates)}，+{added_vod}/-{removed_vod}）")
    print(f"直播源: {len(live_out)} 个（+{added_live}/-{removed_live}）")
    print(f"输出: {OUTPUT_FILE}")
    print(f"报告: {REPORT_FILE}")


if __name__ == "__main__":
    main()
