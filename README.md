# WebVox-MCP 🎙️🔍

语音机器人的联网搜索 MCP 服务 —— 让 StackChan / 小智 等语音助手拥有实时联网检索能力。

## 架构

```
语音机器人 → wss://api.xiaozhi.me → mcp_pipe.py → 联网查询.py (MCP Server) → 智谱AI 搜索
```

## 快速开始

### 🐳 Docker 部署（推荐，NAS / 服务器首选）

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
└── .env.example         # 环境变量模板
```
