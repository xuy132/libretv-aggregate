# LibreTV Aggregate

定期聚合、过滤、测活多个 LibreTV 点播源与 IPTV 直播源，自动生成可直接订阅的静态 JSON。

支持：
- 点播源自动过滤（低评分 / 慢速 / 低在线率）
- 按响应速度排序
- 直播源频道数量统计 + 抽样测活
- 直播源 EPG 节目单配置
- 兼容 LibreTV JSON / ziyuanzhan / TVBox 配置格式
- 完全配置驱动，添加新源只需改 txt 文件

---

## 订阅地址

在 LibreTV → **设置 → 数据源 → 数据源订阅** 中粘贴以下地址，然后点击「添加并同步」：
[raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json](https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json)

**备用加速（网络访问 GitHub 困难时使用）：**
[ghproxy.net/https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json](https://ghproxy.net/https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json)

地址永久固定，无需频繁更换。

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

使用方法1. 添加点播源编辑 config/vod_sources.txt，每行一个源：
# 名称|URL
ziyuanzhan在线|https://mylazily.github.io/ziyuanzhan/data/online.json
loloshilele|https://raw.githubusercontent.com/loloshilele/libretv-sources/main/dist/libretv-sources.json
2. 添加直播源（支持 EPG）编辑 config/live_sources.txt：
# 名称|M3U地址|EPG地址（可选）
IPTV-org中国|https://iptv-org.github.io/iptv/countries/cn.m3u|https://iptv-org.github.io/epg/guides/cn.xml.gz
fanmingming|https://live.fanmingming.cn/tv/m3u/ipv6.m3u|https://live.fanmingming.cn/tv/e.xml
xuy132综合|https://raw.githubusercontent.com/xuy132/collect-tv-txt/main/merged_output.m3u|


3. 自动更新仓库已配置 GitHub Actions，默认每 6 小时自动执行一次聚合与测活。

也可手动触发：Actions → Update Sources → Run workflow。生成结果说明dist/sources.json 中直播源会附带测活信息，例如：
{
  "name": "IPTV-org中国",
  "url": "https://iptv-org.github.io/iptv/countries/cn.m3u",
  "epg": "https://iptv-org.github.io/epg/guides/cn.xml.gz",
  "channelCount": 312,
  "sampleAlive": 7,
  "sampleAliveRate": 87.5
}
| 字段 | 说明 |
| --- | --- |
| channelCount | 总频道数 |
| sampleAlive | 抽样测活成功数量 |
| sampleAliveRate | 抽样存活率（%） |

推荐带 EPG 的直播源

| 名称 | M3U | EPG |
| --- | --- | --- |
| IPTV-org 中国 | https://iptv-org.github.io/iptv/countries/cn.m3u | https://iptv-org.github.io/epg/guides/cn.xml.gz |
| fanmingming | https://live.fanmingming.cn/tv/m3u/ipv6.m3u | https://live.fanmingming.cn/tv/e.xml |
| IPTV-org 香港 | https://iptv-org.github.io/iptv/countries/hk.m3u | https://iptv-org.github.io/epg/guides/hk.xml.gz |
| IPTV-org 台湾 | https://iptv-org.github.io/iptv/countries/tw.m3u | https://iptv-org.github.io/epg/guides/tw.xml.gz |

免责声明本仓库仅做接口可用性聚合与记录，不存储、不分发任何音视频媒体文件。

第三方服务与内容的权利归其权利人所有，公开接口或开源配置不代表已获得内容授权。请仅使用获得合法许可的服务与内容。
