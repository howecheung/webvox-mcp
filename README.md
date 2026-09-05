# WebVox-MCP 🎙️🔍

语音机器人的联网搜索 MCP 服务 —— 让 StackChan / 小智 等语音助手拥有实时联网检索能力。

> **形态区分**
>
> - **源码 / Docker / Windows**：历史主线，默认对接智谱 GLM Web Search。
> - **飞牛 fnOS FPK v1.6.0**：本仓库新交付的应用包“小智联网查询MCP”，
>   带 fnOS 内嵌配置页与运行日志页，支持智谱 GLM 或阿里百炼 WebSearch MCP
>   （默认百炼）。下载与使用见下方「飞牛 fnOS」章节。

## 架构

```
语音机器人 → wss://小智 MCP 端点 → mcp_pipe.py → 联网查询.py (MCP Server) → 搜索服务商
                                                        ├─ 智谱 GLM Web Search
                                                        └─ 阿里百炼 WebSearch MCP
```

搜索服务商的可选项取决于运行形态：源码 / Docker 版默认接智谱 GLM；
fnOS FPK v1.6.0 提供网页配置页，可二选一并默认阿里百炼 WebSearch MCP。

## 📦 飞牛 fnOS（FPK v1.6.0）

> 版本区分：仓库历史中曾有一个旧的 `fpk/`，因安装即报
> `env file ... not found` 被标记 abandoned 并移除。本仓库 `fpk/` 目录是
> **v1.6.0 的完整重写**，已改用 Docker Compose 挂载 + fnOS 内嵌网页配置，
> 可在飞牛应用中心正常安装、配置与升级。

### 下载与安装

1. 到 [Releases](https://github.com/howecheung/webvox-mcp/releases) 下载
   `webvox-mcp-1.6.0.fpk`（仓库内 `fpk/webvox-mcp-1.6.0.fpk` 为同一产物）。
2. 飞牛桌面 → **应用中心 → 手动安装**，选择该 fpk。
3. 首次启动会按 compose 拉取固定 digest 的 `ghcr.io/howecheung/webvox-mcp`
   镜像，请确保 NAS 能访问外网。

### 功能

- 安装后桌面出现“小智联网查询MCP”与“小智联网查询MCP 运行日志”两个入口，
  均在 fnOS 页面内嵌打开，不再占用独立浏览器标签；
- 搜索服务商卡片式二选一，**默认阿里百炼 WebSearch MCP**，也可切换智谱 GLM：
  - 阿里百炼：连接百炼 MCP 广场 WebSearch，需先在控制台开通，前 2000 次免费，
    之后约 29 元/千次，不经过阿里侧模型；
  - 智谱：`open.bigmodel.cn` 的 Web Search API。
- API Key 不在页面明文回显、不写日志；留空保存保留原值，可显式清除；
- 配置/升级兼容：旧配置若为已下线的 dashscope 原生搜索，自动迁移为
  bailian-mcp 并保留百炼 Key；
- 运行日志页自动刷新；桥接日志超过 256KB 自动轮转；Docker 日志限制 10MB×3。

### 配置流程

1. 点击桌面“小智联网查询MCP”，在“小智连接”里粘贴从小智控制台复制的
   `wss://…?token=…` 完整 MCP 服务地址；
2. 搜索服务商默认选中“阿里百炼 WebSearch MCP”，在卡片下方填百炼 `sk-` API Key，
   MCP 服务地址保持默认；
3. 改用智谱时，点选“智谱 GLM Web Search”并填智谱 API Key；
4. 点“保存并重连”，服务端会自动重启 MCP 桥接进程并重连小智；
5. 打开“运行日志”确认 `Successfully connected` 与后续搜索记录。

### FPK 构建方案（简要）

`.fpk` 本质是双层 tar.gz：外层包含 `manifest`、`manifest.checksum`、`cmd/`、
`config/`、`wizard/`、图标与内层 `app.tgz`；`manifest.checksum` 与 manifest 中的
`checksum` 均为 `app.tgz` 的 MD5，改过 `app/` 后必须重算。

Windows 下用官方 fnpack 打包：

```powershell
cd fpk
.\build-fpk.ps1          # 产物：fpk/dist/webvox-mcp.fpk + fpk/webvox-mcp-1.6.0.fpk
```

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
- **智谱API密钥** — [open.bigmodel.cn](https://open.bigmodel.cn) 获取

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
  {"success": true, "results": [{"title": "...", "content": "...", "url": "..."}]}
```

> 供应商说明：源码 / Docker 版通过环境变量对接智谱 GLM；fnOS FPK v1.6.0
> 通过网页配置页选择智谱 GLM 或阿里百炼 WebSearch MCP（默认百炼）。

## 项目来源与致谢

本项目基于小智AI团队的 MCP 服务教程，深表感谢 🙏

📖 参考文档：[小智AI · MCP服务接入指南](https://my.feishu.cn/docx/JKFXd8bLYo6YZtxz9ORcbnA8nbe)

## 项目结构

```
├── 联网查询.py          # MCP Server — 注册联网搜索工具
├── mcp_pipe.py          # WebSocket 管道 — 连接远程服务器，自动重连
├── 启动_main.py          # GUI 配置面板 (tkinter)
├── config_manager.py    # 配置读写 (~/.xiaozhi_mcp_config.json)
├── requirements.txt     # Python 依赖
├── fpk/                 # 飞牛 fnOS FPK v1.6.0 打包工程（含 webvox-mcp-1.6.0.fpk）
└── .env.example         # 环境变量模板
```
