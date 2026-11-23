# libretv-aggregate

定期聚合多个上游点播/直播源，**实测可用性**后生成 LibreTV / Tvbox订阅 JSON。

- 点播：并发探测 Apple CMS 接口，按延迟排序，截断至 100
- 直播：均匀抽样测活，按存活率过滤排序，截断至 50
- 支持黑名单、成人标记、变更统计

## 订阅地址

- 在 LibreTV「设置 → 数据源 → 数据源订阅」粘贴：https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json - 点「添加并同步」即可。
- TVBox：设置 → 配置地址 → 粘贴：https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/tvbox.json
- 地址固定，无需更换。

### 备用

- ghproxy：`https://ghproxy.net/https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json`
- jsDelivr：`https://cdn.jsdelivr.net/gh/xuy132/libretv-aggregate@main/dist/sources.json`
- ghproxy：`https://ghproxy.net/https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/tvbox.json`
- jsDelivr：`https://cdn.jsdelivr.net/gh/xuy132/libretv-aggregate@main/dist/tvbox.json`
-
- 地址永久固定，无需频繁更换。

---

## 项目结构

```text
libretv-aggregate/
├── .github/workflows/
│   └── update.yml              # 定时自动更新
├── config/
│   ├── vod_sources.txt         # 点播源订阅地址
│   └── live_sources.txt        # 直播源地址（支持 EPG）
├── scripts/
│   └── merge.py                # 聚合 + 过滤 + 测活脚本
├── dist/
│   └── sources.json            # 最终生成的订阅文件
└── README.md
## 配置

| 文件 | 说明 |
|------|------|
| `config/vod_sources.txt` | 点播上游订阅，格式 `名称\|URL` |
| `config/live_sources.txt` | 直播源，格式 `名称\|M3U\|EPG(可选)` |
| `config/blacklist.txt` | URL/host 黑名单 |
| `config/adult_keywords.txt` | 成人源名称关键词 |

## 本地运行

```bash
python scripts/merge.py产物：dist/sources.json — LibreTV 订阅（客户端用）
dist/report.json — 含延迟/测活明细的报告

Actions 每 6 小时自动更新；也可在 Actions 页手动 Run workflow。


---

### 使用说明

1. 用上面内容覆盖/新建对应文件。  
2. `config/vod_sources.txt`、`config/live_sources.txt` **不用改**，继续用你现有的即可。  
3. 提交后可在 Actions 里手动跑一次，看日志里的 `✓/✗` 探测结果。  
4. 若某次点播过少：把 `MAX_PROBE_MS` 调到 `5000`，或把 `PROBE_TIMEOUT` 调到 `8`。  
5. 若直播被滤光：把 `LIVE_MIN_ALIVE_RATE` 降到 `10`，或把 `LIVE_MIN_CHANNELS` 降到 `1`。

主要行为变化：点播会真正请求接口测活；没有上游延迟字段也不会再被默认淘汰；直播会按存活率丢掉明显挂掉的源；输出严格压在 LibreTV 的 100/50 上限内。



声明


 免责声明本仓库仅做接口可用性聚合与记录，不存储、不分发任何音视频媒体文件。

第三方服务与内容的权利归其权利人所有，公开接口或开源配置不代表已获得内容授权。请仅使用获得合法许可的服务与内容。

