# WebVox-MCP 🎙️🔍

语音机器人的联网搜索 MCP 服务 —— 让 StackChan / 小智 等语音助手拥有实时联网检索能力。

> **形态区分**
>
> - **源码 / Docker / Windows**：历史主线，默认对接智谱 GLM Web Search。
> - **Windows 源码版（本地改造）**：新增浏览器配置页 `web_config.py`，
>   支持在网页里切换 **智谱 / 阿里百炼 / DeepSeek** 三家搜索服务商。
> - **飞牛 fnOS FPK v1.6.0**：本仓库新交付的应用包“联网查询MCP”，
>   带 fnOS 内嵌配置页（页内菜单切换配置/运行日志），支持智谱 GLM 或阿里百炼 WebSearch MCP
>   （默认百炼）。下载与使用见下方「飞牛 fnOS」章节。

## 架构

```
语音机器人 → wss://小智 MCP 端点 → mcp_pipe.py → 联网查询.py (MCP Server) → 搜索服务商
                                                        ├─ 智谱 GLM Web Search
                                                        ├─ 阿里百炼 WebSearch MCP
                                                        └─ DeepSeek 联网搜索
```

搜索服务商的可选项取决于运行形态：源码 / Docker 版默认接智谱 GLM；
Windows 源码版可通过浏览器配置页在三家之间切换；
fnOS FPK v1.6.0 提供网页配置页，可二选一并默认阿里百炼 WebSearch MCP。

## 🌐 Windows 源码版 · 浏览器配置页

无需改代码，浏览器里就能配置搜索服务商。

```bash
# 1) 启动本地配置服务（默认监听 127.0.0.1:8765）
python web_config.py

# 2) 浏览器打开
#    http://127.0.0.1:8765
```

页面功能：

- **搜索服务商三选一**：智谱 GLM / 阿里百炼 WebSearch MCP / DeepSeek 联网搜索；
- **MCP 端点**：小智控制台的 `wss://…?token=…`；
- **API Key**：三家各自独立填写，留空保存＝保留原值，可显式清除；密钥不回显、不写日志；
- **结果条数**：1–50；
- **语音设备优化**：返回粒度（简要 / 详细）+ 一次回传总字符上限（500–50000）+ 单次搜索超时（3–60 秒）+ 结果缓存时长（0–3600 秒，0 关闭）；
- **网页抓取**：抓取方式（自动 / 仅 DeepSeek / 仅本地）+ 抓取正文长度上限（500–20000），对应 `fetch_url` 工具；
- **API Key 加密**：Windows 下用系统 DPAPI 加密落盘，配置拷到别的机器解不开；容器无 DPAPI 自动回退 600 权限明文；
- **测试与用量**：页面上直接试搜一次（验证 Key 与网络），并显示今日调用次数、缓存命中数、各服务商次数与 DeepSeek 预估费用；
- **运行日志页** `/logs`：查看桥接进程与搜索记录，256KB 自动轮转；
- **桥接自愈**：断线自动指数退避重连（1→600 秒），连续失败到达「自动重连次数上限」后熔断停手，状态区标红，点「重新连接」即可再试一轮；
- 保存后服务端自动重启 MCP 桥接并重连小智。

### 掉线重连与熔断

```
连接成功 → 断线 → 1s / 2s / 4s / 8s … 600s 封顶（带 10% 抖动）自动重连
连续失败超过 RECONNECT_MAX_ATTEMPTS 次 → 写入 gave_up → 进程退出码 78
→ web_config 读到 gave_up 后不再自动拉起（避免容器被无限重启）
→ 页面「运行状态」标红，点「重新连接」清空熔断状态并立即重试一轮
```

- 状态写在配置文件同目录的 `bridge_state.json`（`state` / `attempt` / `backoff` / `reason` / `updated`）；
- 熔断阈值 `RECONNECT_MAX_ATTEMPTS` 默认 20，0 表示永不熔断（容器里不建议）；
- 改了配置（端点/服务商/Key）会自动解除熔断并重连一次；
- Linux / NAS 上桥接子进程用 `start_new_session=True` + `killpg` 杀进程树，比 Windows 干净。

### 开机自启

- **fnOS**：compose 是 `restart: unless-stopped`，NAS 重启后容器自动起来，配置页与桥接随之恢复；只有在应用中心手动停止才不启动。
- **Windows**：默认不会自启。跑一次（在你自己的 PowerShell 里，不需要管理员）：

  ```powershell
  powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
  # 取消：再加 -Uninstall
  ```

  它会在「启动」文件夹建一个快捷方式，登录后用 `pythonw` 拉起 `web_config.py`，
  窗口不弹出，服务照旧在 http://127.0.0.1:8765 。

### DeepSeek 联网搜索与抓取

走 DeepSeek 官方接口，无需自建 MCP：

| 项 | 值 |
| --- | --- |
| 搜索 | `POST https://api.deepseek.com/v1/tools/search` |
| 抓取 | `POST https://api.deepseek.com/v1/tools/fetch`（`fetch_url` 工具用） |
| 鉴权 | `Authorization: Bearer <DeepSeek API Key>` |
| 参数 | 搜索：`query`(必填, 1–128 字)、`count`(1–50, 默认 10)、`need_content`(true 时返回正文)；抓取：`url`(≤8192 字符) |
| 返回 | 搜索 `results[]`：`title` / `url` / `snippet` / `summary` / `content` / `authority` 等；抓取：`title` + Markdown `content` |
| 计费 | 5 元 / 1000 次成功请求（搜索 `results` 非空、且抓取返回正文才计费） |
| 限速 | 搜索 100 QPS、抓取 25 QPS（各自独立） |

在本页「DeepSeek」卡片填 API Key（Base URL 默认 `https://api.deepseek.com`）即可。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WEBVOX_PORT` | `8765` | 配置页端口 |
| `WEBVOX_HOST` | Windows `127.0.0.1` / 容器 `0.0.0.0` | 监听地址 |
| `WEBVOX_LOG_DIR` | `<项目目录>/logs` | 桥接日志目录 |
| `XIAOZHI_MCP_CONFIG` | `~/.xiaozhi_mcp_config.json` | 配置文件路径 |

## 📦 飞牛 fnOS（FPK v1.7.2）

> 版本区分：仓库历史中曾有一个旧的 `fpk/`，因安装即报
> `env file ... not found` 被标记 abandoned 并移除。本仓库 `fpk/` 目录是
> **v1.6.0 起的完整重写**（当前 v1.7.2），已改用 Docker Compose 挂载 +
> fnOS 内嵌网页配置，可在飞牛应用中心正常安装、配置与升级。

### 下载与安装

1. 到 [Releases](https://github.com/howecheung/webvox-mcp/releases) 下载
   `webvox-mcp-1.7.2.fpk`（仓库内 `fpk/webvox-mcp-1.7.2.fpk` 为同一产物）。
2. 飞牛桌面 → **应用中心 → 手动安装**，选择该 fpk。
3. 首次启动会按 compose 拉取固定 digest 的 `ghcr.io/howecheung/webvox-mcp`
   镜像，请确保 NAS 能访问外网。

### 安装后起不来怎么排查

先看应用日志（应用中心 → 该应用 → 日志）。常见两类：

| 日志现象 | 原因 | 处理 |
| --- | --- | --- |
| `start.sh: 4: set: Illegal option -`，容器反复重启 | 打包时 `start.sh` 被 Windows 的 CRLF 行尾污染，`/bin/sh`（dash）解析不了 `set -e` | 1.6.0 之后的包已用 `tools` 里的构建脚本强制 LF 并自检；用 v1.7.2 及以上版本 |
| 日志里有 `[deps] 缺失依赖: xxx` | 镜像里缺某个 Python 包 | 缺 `requests` 时 DeepSeek 会明确报错（不影响智谱/百炼）；缺 `mcp` 则需要换官方镜像 |
| 容器 Running 但页面打不开 | 端口没映射或服务未监听 `0.0.0.0` | 检查 compose 的 `WEBVOX_HOST=0.0.0.0` 与 `service_port = 8765` |

> 校验自建包是否干净（应有 `CRLF: 无`）：
>
> ```powershell
> python -c "import tarfile,io; o=tarfile.open('fpk/webvox-mcp-1.7.2.fpk'); inner=tarfile.open(fileobj=io.BytesIO(o.extractfile('app.tgz').read())); d=inner.extractfile('python/start.sh').read(); print('CRLF:', d.count(b'\r'))"
> ```

### 功能

- 安装后桌面只出现一个“联网查询MCP”入口，在 fnOS 页面内嵌打开；页内顶部菜单
  在“配置 / 运行日志”之间切换，不产生第二个桌面应用；
- 搜索服务商卡片式三选一，**默认阿里百炼 WebSearch MCP**，也可切换智谱 GLM
  或 DeepSeek：
  - 阿里百炼：连接百炼 MCP 广场 WebSearch，需先在控制台开通，前 2000 次免费，
    之后约 29 元/千次，不经过阿里侧模型；
  - 智谱：`open.bigmodel.cn` 的 Web Search API；
  - DeepSeek：官方 `/v1/tools/search` + `/v1/tools/fetch`，5 元/1000 次成功请求。
- 页面内可直接“测试搜索”验证 Key 与网络，并查看今日调用量与 DeepSeek 预估费用；
- 同一搜索词在“结果缓存时长”内复用结果，不重复计费；
- API Key 不在页面明文回显、不写日志；留空保存保留原值，可显式清除；
- 配置/升级兼容：旧配置若为已下线的 dashscope 原生搜索，自动迁移为
  bailian-mcp 并保留百炼 Key；
- 运行日志页自动刷新；桥接日志超过 256KB 自动轮转；Docker 日志限制 10MB×3。

### 配置流程

1. 点击桌面“联网查询MCP”，在页面菜单“配置”的“小智连接”里粘贴从小智控制台复制的
   `wss://…?token=…` 完整 MCP 服务地址；
2. 搜索服务商默认选中“阿里百炼 WebSearch MCP”，在卡片下方填百炼 `sk-` API Key，
   MCP 服务地址保持默认；
3. 改用智谱时，点选“智谱 GLM Web Search”并填智谱 API Key；
4. 点“保存并重连”，服务端会自动重启 MCP 桥接进程并重连小智；
5. 点页面顶部菜单“运行日志”，确认 `Successfully connected` 与后续搜索记录。

### FPK 构建方案（简要）

`.fpk` 本质是双层 tar.gz：外层包含 `manifest`、`manifest.checksum`、`cmd/`、
`config/`、`wizard/`、图标与内层 `app.tgz`；`manifest.checksum` 与 manifest 中的
`checksum` 均为 `app.tgz` 的 MD5，改过 `app/` 后必须重算。

Windows 下用官方 fnpack 打包：

```powershell
cd fpk
.\build-fpk.ps1          # 产物：fpk/dist/webvox-mcp.fpk + fpk/webvox-mcp-<版本>.fpk
```

**没有 fnpack 时**用仓库自带的手工脚本（用旧 fpk 作模板，只替换 `app.tgz`
与 `manifest`，其余 `cmd/`、`config/`、`wizard/`、图标原样保留）：

```powershell
python fpk\build-fpk-manual.py                          # 按 manifest 的 version 命名
python fpk\build-fpk-manual.py --template fpk\webvox-mcp-1.6.0.fpk
```

两条路产物等价（同一份 `app/` 打出来的 `app.tgz` 字节一致）。

更完整的目录、权限、升级与故障排查说明见
[`fpk/README.md`](fpk/README.md)。

## 快速开始

### 🐳 Docker 部署（非 fnOS 的通用服务器）

```bash
# 1. 创建项目目录
mkdir -p ~/webvox-mcp && cd ~/webvox-mcp

# 2. 下载项目文件
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/Dockerfile
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/docker-compose.yaml
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/docker-entrypoint.sh
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/mcp_pipe.py
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/联网查询.py
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/config_manager.py
curl -O https://raw.githubusercontent.com/howecheung/webvox-mcp/master/requirements.txt

# 3. 创建 .env 文件，填入你的密钥
cat > .env << 'EOF'
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=你的小智MCP端点token
ZHIPU_API_KEY=你的智谱API密钥
EOF

# 4. 构建并启动
docker compose up -d

# 5. 查看日志确认运行状态
docker logs -f webvox-mcp

# 常用管理命令
docker compose restart        # 重启服务
docker compose down           # 停止并删除容器
docker compose up -d          # 重新启动
docker compose pull           # 更新镜像
```

> 💡 如果 NAS 无法直接访问 GitHub，可在电脑下载文件后通过 SMB/FTP 传到 NAS 的 `~/webvox-mcp/` 目录，再执行 `docker compose up -d`。

---

### 🪟 Windows 桌面

下载 [Release 页](https://github.com/howecheung/webvox-mcp/releases) 的 `webvox-mcp.exe`，双击运行 GUI 配置面板。

---

### 🐍 源码运行

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 配置密钥

打开 GUI 配置面板：

```bash
python 启动_main.py
```

填入：
- **MCP端点** — 小智平台控制台获取的 WebSocket 地址
- **搜索服务商** — 智谱 / 阿里百炼 / DeepSeek 三选一
- **对应 API Key** — 智谱 [open.bigmodel.cn](https://open.bigmodel.cn)、百炼 `sk-` Key、DeepSeek `sk-` Key
- **结果条数** — 1–50
- **网页抓取** — 抓取方式（自动 / 仅 DeepSeek / 仅本地）+ 抓取正文长度上限（500–20000）
- **语音设备优化** — 返回粒度（简要 / 详细）+ 回传字符上限（500–50000）+ 搜索超时（3–60 秒）+ 缓存时长（0–3600 秒）

> 想用浏览器配置，见上文「Windows 源码版 · 浏览器配置页」。
> 配置保存在 `~/.xiaozhi_mcp_config.json`，不会被提交到 Git。

#### 3. 启动服务

点击"启动服务"，或在命令行：

```bash
python mcp_pipe.py 联网查询.py
```

## MCP 工具

### `联网查询`

```
参数:
  query_text             - 搜索关键词
  count (可选, 默认8)     - 返回条数 (1-50)
  search_domain_filter   - 限定域名，空=全网
  search_recency_filter  - 时间过滤：noLimit / week / month / year

返回:
  {"success": true, "results": [{"title": "...", "content": "...", "url": "..."}],
   "count": 8, "mode": "brief", "truncated": true}
```

语音设备优化（配置页「语音设备优化」区可切换）：

| 项 | 默认 | 说明 |
| --- | --- | --- |
| 返回粒度 `RESULT_MODE` | `brief` | `brief` 只取摘要/片段，不请求网页全文；`detail` 才带完整正文（耗 token） |
| 回传字符上限 `RESULT_MAX_CHARS` | 4000 | 500–50000。超出按条截断，触顶时返回 `"truncated": true` |
| 搜索超时 `SEARCH_TIMEOUT` | 15 | 3–60 秒。超时直接返回错误，语音端不必干等 |

> 桥接进程会把 `[GUI_LOG]` 开头的调试日志只写到本地，不再混进 MCP 协议流。

> 供应商说明：源码 / Docker 版通过环境变量对接智谱 GLM；Windows 源码版通过
> 浏览器配置页在智谱 / 百炼 / DeepSeek 之间切换；
> fnOS FPK v1.7.2 通过网页配置页在三者中选择（默认百炼）。

### `fetch_url`

```
参数:
  url                  - 要抓取的网页地址（http:// 或 https:// 开头，≤8192 字符）
  max_chars (可选)      - 正文最大字符数，500-20000，默认取配置里的"抓取正文长度"

返回:
  {"success": true, "url": "...", "title": "...", "content": "正文…",
   "source": "deepseek" | "local", "truncated": false, "length": 1234}
```

抓取策略（配置页「网页抓取」区可切换）：

| 模式 | 行为 |
| --- | --- |
| `auto`（默认） | 有 DeepSeek Key 就调官方 Fetch，失败自动回落本地抓取 |
| `deepseek` | 只用 DeepSeek 官方 Fetch（`POST /v1/tools/fetch`），返回 Markdown |
| `local` | 只用本地 requests + 抽取正文（有 BeautifulSoup 用它，没有则正则去标签），免费、不消耗 DeepSeek 额度 |

> DeepSeek Fetch 按成功请求计费（5 元 / 1000 次），Fetch 单独限速 25 QPS。
> 本地抓取会自动剥除 script/style/nav/header/footer 等标签，只留正文。

### 缓存与用量

- 同一 `provider + 粒度 + 条数 + 查询词 + 域名 + 时间范围` 在「结果缓存时长」内
  直接复用上次结果，返回里带 `"cached": true`，不产生新的 API 计费；
- 每次调用（含缓存命中与失败）都会向配置文件同目录的 `usage.jsonl` 追加一行，
  超过 512KB 自动截留最后 2000 行；
- 配置页「测试与用量」展示今日调用次数、缓存命中数、各服务商次数与
  DeepSeek 预估费用（按 5 元 / 1000 次成功请求折算）。

## 项目来源与致谢

本项目基于小智AI团队的 MCP 服务教程，深表感谢 🙏

📖 参考文档：[小智AI · MCP服务接入指南](https://my.feishu.cn/docx/JKFXd8bLYo6YZtxz9ORcbnA8nbe)

## 项目结构

```
├── 联网查询.py          # MCP Server — 注册联网搜索工具（智谱/百炼/DeepSeek）+ fetch_url 抓页工具
├── mcp_pipe.py          # WebSocket 管道 — 连接远程服务器，自动重连
├── 启动_main.py          # GUI 配置面板 (tkinter)
├── web_config.py        # 浏览器配置页服务端 (stdlib http.server)
├── www/                 # 配置页静态文件 (index.html / logs.html)
├── config_manager.py    # 配置读写 (~/.xiaozhi_mcp_config.json) + API Key DPAPI 加密
├── usage.jsonl          # 用量流水（运行时自动生成，不入库）
├── requirements.txt     # Python 依赖
├── fpk/                 # 飞牛 fnOS FPK v1.7.2 打包工程（含 webvox-mcp-1.7.2.fpk）
│   ├── build-fpk.ps1        # 官方 fnpack 打包
│   └── build-fpk-manual.py  # 无 fnpack 时的手工重建脚本
└── .env.example         # 环境变量模板
```
