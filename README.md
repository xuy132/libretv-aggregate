# libretv-aggregate

定期检测各站点可用性，生成静态JSON页面。


项目结构:


libretv-aggregate/
├── .github/workflows/update.yml
├── config/
│   ├── vod_sources.txt          # 点播订阅地址（LibreTV JSON）
│   └── live_sources.txt         # 直播源地址（M3U 或 TXT）
├── scripts/
│   └── merge.py
├── dist/
│   └── sources.json             # 最终生成的订阅文件
└── README.md


订阅地址
在 LibreTV 的「设置 → 数据源 → 数据源订阅」
粘贴：
https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json 


点「添加并同步」即可。地址永久固定，不需要再换。


备用（ghproxy，网络不允许时可用）：


https://ghproxy.net/https://raw.githubusercontent.com/xuy132/libretv-aggregate/main/dist/sources.json





本仓库仅为接口可用性记录，不存储、不分发任何媒体文件。第三方服务与内容的权利归其权利人所有，公开接口或开源配置不代表取得内容授权，请仅使用获得许可的服务与内容。

